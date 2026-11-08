"""Server-side enrichment applied to every frame, whatever the source (ESP32, bridge, serial).

inf frames that do not already carry advice (e.g. the ESP32) get points/health/tier/advice.
The healthy loudness baseline is learned per vehicle while it reports "normal".
"""
from collections import defaultdict

from app.analytics.advice import assess

_state = defaultdict(lambda: {"spl": None, "baseline": None, "n": 0, "vib": None, "vib_base": None, "vn": 0})


def process_frame(payload: dict) -> dict:
    vid = payload.get("vid")
    st = _state[vid]
    kind = payload.get("t")
    if kind == "tel" and isinstance(payload.get("spl_ref_db"), (int, float)):
        st["spl"] = float(payload["spl_ref_db"])
    if kind == "tel" and isinstance(payload.get("vib_rms_g"), (int, float)):
        st["vib"] = float(payload["vib_rms_g"])
    elif kind == "inf" and "points" not in payload:
        payload = {**payload, **advise(st, payload)}
    return payload


def advise(st: dict, inf: dict) -> dict:
    fault = inf.get("fault") or "normal"
    spl = st["spl"]
    if fault == "normal" and spl is not None:          # running healthy baseline
        st["n"] = min(st["n"] + 1, 50)
        st["baseline"] = spl if st["baseline"] is None else st["baseline"] + (spl - st["baseline"]) / st["n"]
    db_over = (spl - st["baseline"]) if (spl is not None and st["baseline"] is not None) else 0.0
    vib = st["vib"]
    if fault == "normal" and vib is not None:          # running healthy vibration baseline
        st["vn"] = min(st["vn"] + 1, 50)
        st["vib_base"] = vib if st["vib_base"] is None else st["vib_base"] + (vib - st["vib_base"]) / st["vn"]
    vib_pct = (100.0 * (vib - st["vib_base"]) / vib) if (vib and st["vib_base"] is not None) else None
    level = 3.0 * float(inf.get("severity_raw") or 0.0)
    return assess(fault, level, inf.get("conf") or {}, max(0.0, db_over), fix_vib_pct=vib_pct)
