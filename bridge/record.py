"""
QuietDrive - record one labelled session: laptop microphone + phone accelerometer (phyphox).

Examples
  python record.py --list-mics
  python record.py --phone 192.168.43.20:8080 --check
  python record.py --phone 192.168.43.20:8080 --label normal --level 0 --speed 1 --seconds 90
  python record.py --phone 192.168.43.20:8080 --label imbalance --level 2 --speed 1 --seconds 90
  python record.py --fake --label bearing --level 2 --seconds 30   # synthetic session (no mic/phone)

Labels: normal (level 0), imbalance / loose_mount / bearing (level 1 small, 2 medium, 3 large).
Each session is saved to data/<label>_L<level>_S<speed>_<time>/ with audio.wav, accel.csv, meta.json.
"""
import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
from common import AUDIO_FS, LABELS, FakeMotor, Phyphox, feature_row, to_16k


def list_mics():
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"  [{i}] {d['name']}  ({int(d['default_samplerate'])} Hz)")


def check_phone(addr):
    p = Phyphox(addr)
    try:
        names = p.buffers()
    except Exception as e:
        sys.exit(f"Cannot reach phyphox at {addr}: {e}\n-> same WiFi/hotspot? remote access enabled? address correct?")
    print("phyphox buffers:", names)
    if "accX" not in names:
        print("-> Open the 'Acceleration (without g)' experiment in phyphox, or pass --buffers with the names above.")
        return
    p.restart(); time.sleep(2)
    t, xyz = p.fetch_new(); p.control("stop")
    if len(t) < 10:
        print("Got almost no samples. Is the experiment running (play button) on the phone?")
        return
    print(f"OK: {len(t)} samples in 2 s, about {1/np.median(np.diff(t)):.0f} Hz, last x/y/z = {np.round(xyz[-1], 3)} g")


def check_label(args):
    if args.label not in LABELS:
        sys.exit(f"--label must be one of {LABELS}")
    if args.label == "normal":
        args.level = 0
    elif args.level not in (1, 2, 3):
        sys.exit("--level must be 1, 2 or 3 for a fault")


def record_fake(args):
    """Synthetic session from the fake motor (speed setting changes the RPM)."""
    m = FakeMotor(args.label, args.level, rpm=1800 + 300 * args.speed, seed=None)
    x = m.audio(int(args.seconds * AUDIO_FS))
    t, xyz = m.accel(args.seconds)
    save(args, x, [(ti, *v) for ti, v in zip(t, xyz)])


def record(args):
    import sounddevice as sd

    dev = sd.query_devices(args.mic, "input") if args.mic is not None else sd.query_devices(kind="input")
    mic_fs = AUDIO_FS
    try:
        sd.check_input_settings(device=args.mic, samplerate=AUDIO_FS, channels=1)
    except Exception:
        mic_fs = int(dev["default_samplerate"])
    print(f"Mic: {dev['name']} at {mic_fs} Hz")

    phone = Phyphox(args.phone, *args.buffers.split(",")) if args.phone else None
    acc_rows, stop = [], threading.Event()

    def poll_phone():
        while not stop.is_set():
            try:
                t, xyz = phone.fetch_new()
                for ti, v in zip(t, xyz):
                    acc_rows.append((ti, *v))
            except Exception as e:
                print("  phone read error:", e)
            time.sleep(0.1)

    for s in range(3, 0, -1):
        print(f"Starting in {s}... (motor running, phone taped down, room quiet)"); time.sleep(1)

    if phone:
        phone.restart()
    audio = sd.rec(int(args.seconds * mic_fs), samplerate=mic_fs, channels=1, dtype="float32", device=args.mic)
    if phone:
        th = threading.Thread(target=poll_phone, daemon=True); th.start()
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        left = args.seconds - (time.time() - t0)
        print(f"  recording... {left:5.1f} s left, phone samples: {len(acc_rows)}", end="\r")
        time.sleep(0.5)
    sd.wait()
    stop.set()
    if phone:
        th.join(timeout=2); phone.control("stop")
    print()

    x = to_16k(audio[:, 0].astype(np.float64), mic_fs)
    save(args, x, acc_rows, phone is not None)


def save(args, x, acc_rows, phone=True):
    peak = np.max(np.abs(x))
    if peak > 0.99:
        print("WARNING: audio clipped. Move the laptop further away or lower the mic volume.")
    if peak < 0.005:
        print("WARNING: audio almost silent. Check the mic and Windows input volume.")

    name = f"{args.label}_L{args.level}_S{args.speed}_{datetime.now():%H%M%S}"
    out = os.path.join(args.data, name)
    os.makedirs(out, exist_ok=True)
    sf.write(os.path.join(out, "audio.wav"), x.astype(np.float32), AUDIO_FS)
    if acc_rows:
        np.savetxt(os.path.join(out, "accel.csv"), np.array(acc_rows), delimiter=",",
                   header="t_s,x_g,y_g,z_g", comments="", fmt="%.6f")
    rate = (len(acc_rows) / args.seconds) if acc_rows else 0
    meta = dict(label=args.label, level=args.level, speed=args.speed, seconds=args.seconds,
                audio_fs=AUDIO_FS, phone_rate_hz=round(rate, 1), notes=args.notes,
                recorded=datetime.now().isoformat(timespec="seconds"))
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=2)
    print(f"Saved {out}  (audio {len(x)/AUDIO_FS:.0f} s, phone {len(acc_rows)} samples ~{rate:.0f} Hz)")
    if phone and rate < 150:
        print("Note: phone sample rate is low. Run the motor slower so its shaking stays below half this rate.")
    # quick sanity check through the same prefilter + features as training/live
    acc = np.array(acc_rows).reshape(-1, 4)
    mid = len(x) // 2
    m = (acc[:, 0] >= acc[0, 0] + mid / AUDIO_FS - 0.5) & (acc[:, 0] < acc[0, 0] + mid / AUDIO_FS + 0.5) if len(acc) else []
    _, ex = feature_row(x[mid - AUDIO_FS // 2: mid + AUDIO_FS // 2], acc[m, 0] if len(acc) else [], acc[m, 1:4] if len(acc) else np.zeros((0, 3)))
    print(f"Check: whine {ex['whine']:.0f} Hz -> ~{ex['rpm']:.0f} rpm, audio {ex['rms_db']:.1f} dBFS, vibration {ex['vib_rms']:.3f} g")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-mics", action="store_true")
    ap.add_argument("--check", action="store_true", help="test the phone connection only")
    ap.add_argument("--phone", help="phyphox remote address, e.g. 192.168.43.20:8080")
    ap.add_argument("--buffers", default="accX,accY,accZ,acc_time")
    ap.add_argument("--mic", type=int, default=None, help="mic index from --list-mics")
    ap.add_argument("--label", default="normal")
    ap.add_argument("--level", type=int, default=0)
    ap.add_argument("--speed", type=int, default=1, help="1, 2, 3 = which LM2596 setting you used")
    ap.add_argument("--seconds", type=float, default=90)
    ap.add_argument("--notes", default="")
    ap.add_argument("--data", default=os.path.join(HERE, "data"))
    ap.add_argument("--fake", action="store_true", help="synthetic motor instead of mic + phone")
    a = ap.parse_args()
    if a.list_mics:
        list_mics()
    elif a.check:
        if not a.phone:
            sys.exit("--check needs --phone")
        check_phone(a.phone)
    else:
        check_label(a)
        record_fake(a) if a.fake else record(a)
