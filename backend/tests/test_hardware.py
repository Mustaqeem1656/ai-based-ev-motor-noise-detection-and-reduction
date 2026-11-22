"""Hardware edition (SOURCE=serial) pipeline, without hardware: frames are fed straight in."""
import asyncio
import base64
import json
import wave

import numpy as np
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

from app.analytics.inference import AUDIO_IDX, FEATURE_NAMES, PHONE_NOTE, VIB_IDX, Inference, feature_row
from app.ingest import hardware
from app.ingest.event_bus import bus
from app.ingest.hardware import HardwareLink, parse_line
from app.ingest.recorder import Recorder
from app.main import app


def feat(rpm=3600.0, fault_bias=0.0, vib=True):
    rng = np.random.default_rng(0)
    return {"t": "feat", "ts": 5000, "vid": "QD-01", "rpm": rpm, "shaft_hz": rpm / 60, "rpm_src": "hall",
            "audio_bands": list(rng.normal(-60, 3, 32) + fault_bias), "vib_bands": list(rng.normal(-50, 3, 16)) if vib else [None] * 16,
            "vib_1x": 0.01 + fault_bias / 100, "vib_2x": 0.005, "vib_rms": 0.02, "audio_rms_db": -40.0,
            "whine_hz": rpm / 60 * 21, "vib_ok": vib}


def tiny_model(source="phone"):
    rng = np.random.default_rng(1)
    X = rng.normal(0, 1, (80, len(FEATURE_NAMES))); y = np.array(["normal", "imbalance"] * 40); X[y == "imbalance"] += 2
    mk = lambda Xi: RandomForestClassifier(n_estimators=5, random_state=0).fit(Xi, y)
    return dict(clf=mk(X), clf_audio=mk(X[:, AUDIO_IDX]), clf_vib=mk(X[:, VIB_IDX]),
                reg=RandomForestRegressor(n_estimators=5, random_state=0).fit(X, (y != "normal") * 2.0),
                whine_order=21, source=source, normal_ref=[(3000, -42.0), (4000, -41.0)], fix_db={}, baseline=X[y == "normal"].mean(0))


def test_parse_line_tolerant():
    assert parse_line('{"t":"tel","rpm":1}') == {"t": "tel", "rpm": 1}
    assert parse_line("QuietDrive boot banner") is None
    assert parse_line('{"t":') is None
    assert parse_line('{"no_type":1}') is None


def test_feature_row_order_and_missing_vibration():
    row, has_vib = feature_row(feat())
    assert len(row) == len(FEATURE_NAMES) == 53 and has_vib
    assert row[FEATURE_NAMES.index("whine_hz")] == 3600 / 60 * 21
    row, has_vib = feature_row(feat(vib=False))
    assert not has_vib and row[FEATURE_NAMES.index("vib_b0")] == -120 and row[FEATURE_NAMES.index("vib_rms")] == 0
    assert feature_row({"audio_bands": [1, 2]}) is None


def test_stopped_gate_skips_model():
    inf = Inference(tiny_model()).on_feat(feat(rpm=120))
    assert inf["fault"] == "stopped" and inf["points"] == 0 and inf["protected_hz"] == []


def test_phone_model_is_flagged_and_loudness_extras_suppressed():
    inf = Inference(tiny_model("phone")).on_feat(feat(fault_bias=5))
    assert inf["model_note"] == PHONE_NOTE and inf["model_source"] == "phone"
    assert inf["fault"] in ("normal", "imbalance") and "tier" in inf and "points" in inf
    if inf["fault"] != "normal":
        assert abs(inf["protected_hz"][0] - 60.0) < 1e-6          # 1x shaft from the Hall speed
    inf2 = Inference(tiny_model("esp32s3")).on_feat(feat())
    assert inf2["model_note"] is None


def b64(a, dtype="<i2"):
    return base64.b64encode(np.asarray(a, dtype=dtype).tobytes()).decode()


def test_recorder_writes_record_py_folder_format(tmp_path):
    r = Recorder(str(tmp_path))
    r.start("imbalance", 2, 1, seconds=0.2)
    t0 = 1_000_000
    r.feed({"t": "raw", "i0": 0, "t0_us": t0, "a": b64(np.arange(1600))})
    r.feed({"t": "raw", "i0": 3200, "t0_us": t0 + 200_000, "a": b64(np.arange(1600))})     # 1600 lost
    r.feed({"t": "rawv", "fs": 1600, "t_last_us": t0 + 100_000, "gap": 0, "g_per_lsb": 0.0039, "v": b64(np.ones((160, 3)) * 256)})
    r.feed({"t": "feat", "ts": 1100, **{k: v for k, v in feat().items() if k not in ("t", "ts")}})
    r.feed({"t": "tel", "ts": 1150, "rpm": 3600, "anc_on": False, "whine_order": 21})
    assert r.due()
    res = r.stop()
    assert res["ok"] and res["dropped_audio"] == 1600
    folder = tmp_path / [p.name for p in tmp_path.iterdir()][0]
    assert folder.name.startswith("imbalance_L2_S1_")
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["source"] == "esp32s3" and meta["audio_fs"] == 16000 and meta["accel_nominal_hz"] == 1600
    assert meta["label"] == "imbalance" and meta["level"] == 2
    with wave.open(str(folder / "audio.wav")) as w:
        assert w.getframerate() == 16000 and w.getnframes() == 4800
    acc = (folder / "accel.csv").read_text().splitlines()
    assert acc[0] == "t_s,x_g,y_g,z_g" and len(acc) == 161
    assert abs(float(acc[-1].split(",")[0]) - 0.1) < 1e-6 and abs(float(acc[1].split(",")[1]) - 0.9984) < 1e-6
    f = json.loads((folder / "features.jsonl").read_text().splitlines()[0])
    assert abs(f["t_s"] - 0.1) < 1e-6 and len(f["audio_bands"]) == 32


class FakeSerial:
    def __init__(self):
        self.lines = []

    def write(self, b):
        self.lines.append(json.loads(b.decode()))


def test_link_runs_model_and_sends_inf_back():
    link = HardwareLink("COMX", 921600, "QD-01", tiny_model())
    link.ser = FakeSerial()
    got = []
    bus.subscribe(key="live:QD-01", callback=got.append)
    try:
        asyncio.run(link.handle(dict(feat())))
        asyncio.run(link.handle({"t": "tel", "ts": 1, "rpm": 3600, "anc_on": True, "anc_freq": 1260.0}))
    finally:
        bus.unsubscribe(key="live:QD-01", callback=got.append)
    kinds = [m["t"] for m in got]
    assert kinds == ["feat", "inf", "tel"]
    inf = got[1]
    assert inf["src"] == "esp32s3" and "tier" in inf and inf["ts"] > 1e12
    sent = link.ser.lines[-1]
    assert sent["t"] == "inf" and set(sent) <= {"t", "fault", "tier", "health", "protected_hz", "severity_raw"}
    assert link.last_tel["ts_dev"] == 1


def test_device_endpoints_need_serial_source():
    hardware.link = None
    c = TestClient(app)
    assert c.get("/api/device/status").json()["connected"] is False
    assert c.post("/api/device/anc", json={"on": True}).status_code == 409
    assert c.post("/api/device/set", json={"whine_order": 1}).status_code == 422       # protected 1x tone


def test_device_endpoints_forward_commands():
    link = HardwareLink("COMX", 921600, "QD-01", None)
    link.ser = FakeSerial()
    hardware.link = link
    try:
        c = TestClient(app)
        assert c.post("/api/device/anc", json={"on": True}).json()["sent"] == {"cmd": "set", "anc": True}
        assert c.post("/api/device/anc/calibrate", json={}).json()["sent"] == {"cmd": "anc_calibrate"}
        assert c.post("/api/device/set", json={"anc_mu": 0.02, "pwm_mode": "spread"}).status_code == 200
        r = c.post("/api/device/record/start", json={"label": "normal", "seconds": 10})
        assert r.status_code == 200 and link.ser.lines[-1] == {"cmd": "set", "raw": True}
        assert c.post("/api/device/record/start", json={"label": "nope"}).status_code == 422
        c.post("/api/device/record/stop")
        assert link.ser.lines[-1] == {"cmd": "set", "raw": False}
    finally:
        hardware.link = None
