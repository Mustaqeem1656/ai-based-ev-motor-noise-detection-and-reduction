"""
QuietDrive - make a synthetic dataset with the fake motor (to test train.py / live.py
without a motor). Real recordings go to bridge/data; this writes bridge/data_fake.

  py -3.12 bridge/fake_dataset.py
  py -3.12 bridge/train.py --data bridge/data_fake
"""
import argparse
import os
from types import SimpleNamespace

from record import HERE, record_fake

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data_fake"))
    ap.add_argument("--seconds", type=float, default=20)
    a = ap.parse_args()
    plan = [("normal", 0, s) for s in (1, 2, 3)] + \
           [(f, lvl, s) for f in ("imbalance", "loose_mount", "bearing") for lvl in (1, 2, 3) for s in (1, 2)]
    for label, level, speed in plan:
        record_fake(SimpleNamespace(label=label, level=level, speed=speed, seconds=a.seconds,
                                    data=a.data, notes="synthetic"))
