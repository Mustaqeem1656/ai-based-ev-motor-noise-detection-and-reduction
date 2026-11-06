"""
QuietDrive - live bridge: laptop mic + phone accelerometer -> model -> backend dashboard.
Sends the same messages as the ESP32 firmware (tel, spec, feat, inf) to the backend's
device WebSocket, so the backend and dashboard treat it like any other device.

  py -3.12 bridge/live.py --phone 192.168.43.20:8080
  py -3.12 bridge/live.py --phone ADDR --mic 2 --backend ws://localhost:8000/ws/device/QD-01
  py -3.12 bridge/live.py --dry-run --phone ADDR   # print instead of sending
  py -3.12 bridge/live.py --fake                   # synthetic motor, no mic/phone needed
      keys in --fake mode: 0 normal, 1 imbalance, 2 loose_mount, 3 bearing, +/- level, q quit
"""
import argparse
import collections
import os
import threading
import time

import numpy as np

from common import (AUDIO_FS, AUDIO_IDX, HOP_S, LABELS, VIB_IDX, WIN_S, FakeMotor, Phyphox,
                    audio_power_spectrum, display_spectrum, dumps, feature_row, now_ms,
                    prefilter_audio, to_16k)

HERE = os.path.dirname(os.path.abspath(__file__))


class Sender:
    """WebSocket client that reconnects on its own; --dry-run prints a short line instead."""

    def __init__(self, url, dry):
        self.url, self.dry, self.ws, self.last_try = url, dry, None, 0

    def send(self, msg):
        if self.dry:
            if msg["t"] in ("inf",):
                print("\n" + dumps(msg)[:300])
            return
        if self.ws is None and time.time() - self.last_try > 2:
            self.last_try = time.time()
            try:
                import websocket
                self.ws = websocket.create_connection(self.url, timeout=3)
                print(f"\nconnected to {self.url}")
            except Exception as e:
                print(f"\nbackend not reachable ({e}); retrying...")
        if self.ws is not None:
            try:
                self.ws.send(dumps(msg))
            except Exception:
                print("\nconnection lost; reconnecting...")
                self.ws = None


def next_due(last, period, now):
    """Fixed-rate scheduling: keep the average rate, but never burst after a stall."""
    return last + period if now - last < 2 * period else now


def predict(model, row, history):
    """Fault (majority of last 3), per-sensor confidence and severity 0-1."""
    if model is None:
        return "normal", dict(audio=None, vib=None, current=None), 0.0, 0.0
    p = model["clf"].predict_proba(row[None])[0]
    labels = list(model["clf"].classes_)
    history.append(labels[int(np.argmax(p))])
    fault = max(set(history), key=list(history).count)

    def sensor_conf(m, idx):
        pp = dict(zip(m.classes_, m.predict_proba(row[idx][None])[0]))
        return float(pp.get(fault, 0.0))     # how sure this sensor alone is about the voted result
    conf = dict(audio=sensor_conf(model["clf_audio"], AUDIO_IDX),
                vib=sensor_conf(model["clf_vib"], VIB_IDX), current=None)
    level = float(np.clip(model["reg"].predict(row[None])[0], 0, 3)) if fault != "normal" else 0.0
    return fault, conf, level / 3, level


def start_fake(a, lock, audio_buf, acc_buf):
    motor = FakeMotor(a.fake_fault, a.fake_level if a.fake_fault != "normal" else 0, rpm=a.fake_rpm)

    def run():
        while True:
            x = motor.audio(int(0.05 * AUDIO_FS))
            t, xyz = motor.accel(0.05)
            with lock:
                audio_buf.extend(x)
                acc_buf.extend(np.column_stack([t, xyz]).tolist())
            time.sleep(0.05)

    def keys():
        try:
            import msvcrt
        except ImportError:
            return
        while True:
            k = msvcrt.getwch()
            if k in "0123":
                motor.set(LABELS[int(k)], motor.level or 2)
            elif k in "+=" and motor.fault != "normal":
                motor.set(motor.fault, motor.level + 1)
            elif k in "-_" and motor.fault != "normal":
                motor.set(motor.fault, motor.level - 1)
            elif k in "qQ":
                os._exit(0)
            print(f"\n[fake motor] {motor.fault} level {motor.level}")
    threading.Thread(target=run, daemon=True).start()
    threading.Thread(target=keys, daemon=True).start()
    print("Fake motor running. Keys: 0 normal, 1 imbalance, 2 loose_mount, 3 bearing, +/- level, q quit")
    return AUDIO_FS


def start_real(a, lock, audio_buf, acc_buf):
    import sounddevice as sd
    mic_fs = AUDIO_FS
    try:
        sd.check_input_settings(device=a.mic, samplerate=AUDIO_FS, channels=1)
    except Exception:
        mic_fs = int(sd.query_devices(a.mic, "input")["default_samplerate"])

    def cb(indata, frames, t, status):
        with lock:
            audio_buf.extend(indata[:, 0].astype(np.float64))
    stream = sd.InputStream(samplerate=mic_fs, channels=1, device=a.mic, callback=cb, blocksize=int(mic_fs * 0.05))
    stream.start()
    start_real.stream = stream                 # keep a reference so it is not garbage-collected
    print(f"Mic running at {mic_fs} Hz")
    if a.phone:
        phone = Phyphox(a.phone, *a.buffers.split(","))
        phone.restart()

        def poll():
            while True:
                try:
                    t, xyz = phone.fetch_new()
                    with lock:
                        acc_buf.extend(np.column_stack([t, xyz]).tolist())
                except Exception as e:
                    print("\nphone read error:", e)
                time.sleep(0.1)
        threading.Thread(target=poll, daemon=True).start()
        print("Phone accelerometer running")
    else:
        print("No --phone given: vibration features will be empty")
    return mic_fs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phone", help="phyphox remote address")
    ap.add_argument("--buffers", default="accX,accY,accZ,acc_time")
    ap.add_argument("--mic", type=int, default=None)
    ap.add_argument("--backend", default="ws://localhost:8000/ws/device/QD-01")
    ap.add_argument("--vid", default="QD-01")
    ap.add_argument("--model", default=os.path.join(HERE, "models", "quietdrive_model.joblib"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fake", action="store_true", help="synthetic motor, no mic or phone")
    ap.add_argument("--fake-fault", default="normal", choices=LABELS)
    ap.add_argument("--fake-level", type=int, default=2)
    ap.add_argument("--fake-rpm", type=float, default=2400)
    a = ap.parse_args()

    model = None
    if os.path.exists(a.model):
        import joblib
        model = joblib.load(a.model)
        print(f"Model loaded: {model['labels']}  (accuracy {model['accuracy']*100:.1f}%, {model['evaluation']})")
    else:
        print(f"No model at {a.model} - streaming sensor data only (run train.py first).")

    sender = Sender(a.backend, a.dry_run)
    lock = threading.Lock()
    audio_buf = collections.deque(maxlen=2 * 48000)
    acc_buf = collections.deque(maxlen=4000)
    mic_fs = (start_fake if a.fake else start_real)(a, lock, audio_buf, acc_buf)

    time.sleep(1.2)
    last = dict(tel=0.0, spec=0.0, feat=0.0)
    history = collections.deque(maxlen=3)
    state = dict(rpm=None, spl=None, vib_rms=None, xyz=[None] * 3)
    while True:
        now = time.time()
        if now - last["feat"] >= HOP_S:            # feat + inf at 2 Hz
            last["feat"] = next_due(last["feat"], HOP_S, now)
            with lock:
                x = np.array(audio_buf)[-int(WIN_S * mic_fs):]
                acc = np.array(acc_buf) if acc_buf else np.zeros((0, 4))
            if len(x) >= int(WIN_S * mic_fs * 0.9):
                x16 = to_16k(x, mic_fs)
                if len(acc):
                    acc = acc[acc[:, 0] >= acc[-1, 0] - WIN_S]
                row, ex = feature_row(x16, acc[:, 0], acc[:, 1:4])
                has_vib = ex["vib_fs"] > 0
                state.update(rpm=ex["rpm"], vib_rms=ex["vib_rms"] if has_vib else None,
                             spl=ex["rms_db"] + 100,  # dBFS + 100: uncalibrated, comparable over time
                             xyz=list(acc[:, 1:4].std(axis=0)) if has_vib else [None] * 3)
                sender.send(dict(t="feat", ts=now_ms(), vid=a.vid, rpm=ex["rpm"], audio_bands=ex["audio_bands"],
                                 vib_bands=ex["vib_bands"], vib_1x=ex["vib_1x"], vib_2x=ex["vib_2x"],
                                 i_mean=None, i_ripple=None, temp_c=None))
                fault, conf, sev, level = predict(model, row, history)
                shaft = ex["shaft_hz"]
                protected = [] if fault == "normal" else [shaft] + ([2 * shaft] if fault == "loose_mount" else [])
                sender.send(dict(t="inf", ts=now_ms(), vid=a.vid, fault=fault, conf=conf,
                                 severity_raw=sev, protected_hz=protected))
                ca = "-" if conf["audio"] is None else f"{conf['audio']:.2f}"
                cv = "-" if conf["vib"] is None else f"{conf['vib']:.2f}"
                print(f"  rpm~{ex['rpm']:6.0f}  whine {ex['whine']:6.1f} Hz  vib {ex['vib_rms']:.3f} g  "
                      f"phone {len(acc):4d} pts -> {fault:11s} lvl {level:.1f}  conf a{ca} v{cv}   ", end="\r")
        if now - last["tel"] >= 0.1 and state["rpm"] is not None:    # tel at 10 Hz
            last["tel"] = next_due(last["tel"], 0.1, now)
            sender.send(dict(t="tel", ts=now_ms(), vid=a.vid, rpm=state["rpm"], current_a=None, temp_c=None,
                             vib_rms_g=state["vib_rms"], vib_xyz=state["xyz"], spl_ref_db=state["spl"],
                             spl_err_db=None, pwm_hz=None, pwm_mode="fixed", anc_on=False,
                             vib_cancel_on=False, vib_cancel_effort=0))
        if now - last["spec"] >= 0.2 and state["rpm"] is not None:   # spec at 5 Hz, last 0.25 s
            last["spec"] = next_due(last["spec"], 0.2, now)
            with lock:
                xs = np.array(audio_buf)[-int(0.25 * mic_fs):]
            f, p = audio_power_spectrum(prefilter_audio(to_16k(xs, mic_fs)))
            sender.send(dict(t="spec", ts=now_ms(), vid=a.vid, mic="ref", fmax=4000, bins=display_spectrum(f, p)))
        time.sleep(0.01)


if __name__ == "__main__":
    main()
