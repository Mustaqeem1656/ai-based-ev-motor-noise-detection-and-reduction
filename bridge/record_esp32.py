"""
QuietDrive - record one labelled session from the ESP32-S3 (hardware edition).

The backend owns the serial port (SOURCE=serial), so this asks the backend to record:
  py -3.12 bridge/record_esp32.py --label normal --seconds 90
  py -3.12 bridge/record_esp32.py --label imbalance --level 2 --speed 1 --seconds 90

Saved to bridge/data_esp32s3/<label>_L<level>_S<speed>_<time>/ (audio.wav, accel.csv, meta.json,
features.jsonl, telemetry.csv). Train with:  py -3.12 bridge/train.py --data bridge/data_esp32s3
Keep ANC OFF while recording training data.
"""
import argparse
import sys
import time

import requests

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--backend", default="http://localhost:8000")
ap.add_argument("--label", required=True, choices=["normal", "imbalance", "loose_mount", "bearing"])
ap.add_argument("--level", type=int, default=0)
ap.add_argument("--speed", type=int, default=1)
ap.add_argument("--seconds", type=float, default=90)
ap.add_argument("--notes", default="")
a = ap.parse_args()

r = requests.post(f"{a.backend}/api/device/record/start", json=dict(label=a.label, level=a.level, speed=a.speed,
                                                                     seconds=a.seconds, notes=a.notes), timeout=5)
if r.status_code != 200:
    sys.exit(f"backend refused: {r.status_code} {r.text}")
print(f"Recording {a.label} L{a.level} for {a.seconds:.0f} s (motor running, ANC off)...")
try:
    while True:
        time.sleep(1)
        s = requests.get(f"{a.backend}/api/device/record", timeout=5).json()
        print(f"  {s['recorded_s']:6.1f} / {a.seconds:.0f} s   dropped audio {s['dropped_audio']}  vib {s['dropped_vib']}   ", end="\r")
        if not s["active"]:
            break
except KeyboardInterrupt:
    s = requests.post(f"{a.backend}/api/device/record/stop", timeout=10).json()
print()
res = s.get("last") or s
print("Saved:", res)
if res.get("dropped_audio"):
    print("WARNING: audio samples were lost on the serial link (filled with silence). Check the USB cable / port.")
