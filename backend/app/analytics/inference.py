"""Fault / severity inference for the hardware edition (ESP32-S3 'feat' frames).

Same decision logic as bridge/live.py (software edition): Random Forest fault model, majority of
the last 3 votes, sound-only vs vibration-only tie-break (vibration wins), severity regressor with
a median of the last 3 levels, motor-stopped gate. The feature vector is assembled from the
device's feat frame in bridge/common.py FEATURE_NAMES order.

Models trained on the phone (laptop mic + phone accelerometer) are accepted as a stopgap, but the
sensors are different (INMP441 vs laptop mic level, ADXL345 1600 Hz vs phone ~200 Hz), so every
inf says so and loudness-based extras (dB over healthy, fix_db, fix_vib_pct) are only used with a
model trained on ESP32-S3 recordings.
"""
import logging
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np

from app.analytics.advice import assess
from app.config import REPO, settings

log = logging.getLogger("quietdrive.inference")

N_AUDIO, N_VIB = 32, 16
FEATURE_NAMES = ([f"audio_b{i}" for i in range(N_AUDIO)] + [f"vib_b{i}" for i in range(N_VIB)] +
                 ["vib_1x", "vib_2x", "vib_rms", "audio_rms_db", "whine_hz"])     # = bridge/common.py
AUDIO_IDX = [i for i, n in enumerate(FEATURE_NAMES) if n.startswith("audio") or n == "whine_hz"]
VIB_IDX = [i for i, n in enumerate(FEATURE_NAMES) if n.startswith("vib")]

PHONE_NOTE = "Phone-trained model: needs retraining on hardware recordings"


def default_model_paths():
    if settings.model_path:
        return [Path(settings.model_path)]
    return [REPO / "bridge" / "models_esp32s3" / "quietdrive_model.joblib",
            REPO / "bridge" / "models" / "quietdrive_model.joblib"]


def load_model(paths=None):
    for p in paths or default_model_paths():
        if p.exists():
            try:
                import joblib
                m = joblib.load(p)
                m.setdefault("source", "phone")
                m["path"] = str(p)
                log.info("Model loaded from %s (source %s)", p, m["source"])
                return m
            except Exception as exc:  # pragma: no cover
                log.warning("Model %s failed to load: %s", p, exc)
    log.warning("No model found: hardware inf will report 'normal' with no confidence")
    return None


def feature_row(feat: dict) -> Optional[tuple]:
    """feat frame -> (row[53], has_vib). None if the audio part is missing."""
    ab, vb = feat.get("audio_bands"), feat.get("vib_bands")
    if not isinstance(ab, list) or len(ab) != N_AUDIO:
        return None
    has_vib = bool(feat.get("vib_ok", True)) and isinstance(vb, list) and len(vb) == N_VIB and all(v is not None for v in vb)
    vb = vb if has_vib else [-120.0] * N_VIB
    num = lambda k, d=0.0: float(feat[k]) if isinstance(feat.get(k), (int, float)) else d
    row = list(map(float, ab)) + list(map(float, vb)) + [
        num("vib_1x") if has_vib else 0.0, num("vib_2x") if has_vib else 0.0, num("vib_rms") if has_vib else 0.0,
        num("audio_rms_db", -120.0), num("whine_hz")]
    if any(v is None for v in ab):
        return None
    return np.array(row, dtype=float), has_vib


class Inference:
    def __init__(self, model=None):
        self.model = model
        self.history = deque(maxlen=3)
        self.levels = deque(maxlen=3)

    @property
    def hardware_model(self) -> bool:
        return bool(self.model) and self.model.get("source") == "esp32s3"

    def _meta(self) -> dict:
        m = self.model
        if not m:
            return dict(model_source=None, model_note="No model loaded: record sessions and run bridge/train.py")
        return dict(model_source=m.get("source"), model_note=None if self.hardware_model else PHONE_NOTE)

    def predict(self, row, has_vib):
        m = self.model
        if m is None:
            return "normal", dict(audio=None, vib=None, current=None), 0.0
        p = m["clf"].predict_proba(row[None])[0]
        vote = list(m["clf"].classes_)[int(np.argmax(p))]
        if has_vib:
            a_pred = m["clf_audio"].predict(row[AUDIO_IDX][None])[0]
            v_pred = m["clf_vib"].predict(row[VIB_IDX][None])[0]
            if a_pred != v_pred:
                vote = v_pred
        self.history.append(vote)
        fault = max(set(self.history), key=list(self.history).count)

        def sensor_conf(clf, idx):
            pp = dict(zip(clf.classes_, clf.predict_proba(row[idx][None])[0]))
            return float(pp.get(fault, 0.0))
        conf = dict(audio=sensor_conf(m["clf_audio"], AUDIO_IDX),
                    vib=sensor_conf(m["clf_vib"], VIB_IDX) if has_vib else None, current=None)
        level = float(np.clip(m["reg"].predict(row[None])[0], 0, 3)) if fault != "normal" else 0.0
        return str(fault), conf, level

    def _healthy_db(self, rpm):
        ref = sorted((self.model or {}).get("normal_ref") or [])
        if not ref:
            return None
        r, d = np.array(ref).T
        return float(np.interp(rpm, r, d))

    def _fix_db(self, fault, level):
        table = (self.model or {}).get("fix_db", {}).get(fault)
        if not table:
            return None
        return table[min(table, key=lambda k: abs(float(k) - level))]

    def _fix_vib_pct(self, vib_rms):
        base = (self.model or {}).get("baseline")
        if base is None or not vib_rms:
            return None
        healthy = float(base[FEATURE_NAMES.index("vib_rms")])
        return 100.0 * (vib_rms - healthy) / vib_rms

    def on_feat(self, feat: dict) -> Optional[dict]:
        """feat frame -> inf frame (without t/ts/vid), or None if the frame is unusable."""
        rpm = feat.get("rpm")
        stopped = not isinstance(rpm, (int, float)) or rpm < settings.stopped_rpm
        if stopped:
            self.history.clear(); self.levels.clear()
            return dict(fault="stopped", conf=dict(audio=None, vib=None, current=None), severity_raw=0.0,
                        protected_hz=[], points=0, health=100, tier="HEALTHY", action="none",
                        advice="Motor is not running.", reason="No shaft rotation (Hall sensor).", **self._meta())
        fr = feature_row(feat)
        if fr is None:
            return None
        row, has_vib = fr
        fault, conf, level = self.predict(row, has_vib)
        self.levels.append(level)
        level = float(np.median(self.levels)) if fault != "normal" else 0.0
        db_over, fix_db, fix_vib = 0.0, None, None
        if self.hardware_model:                    # loudness / vibration scales only valid for hardware models
            base = self._healthy_db(rpm)
            db_over = max(0.0, float(feat.get("audio_rms_db", 0)) - base) if base is not None else 0.0
            fix_db = self._fix_db(fault, level)
            fix_vib = self._fix_vib_pct(feat.get("vib_rms")) if has_vib else None
        adv = assess(fault, level, conf, db_over, fix_db, fix_vib)
        shaft = feat.get("shaft_hz") or rpm / 60.0
        protected = [] if fault == "normal" else [shaft] + ([2 * shaft] if fault == "loose_mount" else [])
        return dict(fault=fault, conf=conf, severity_raw=level / 3, protected_hz=protected, **adv, **self._meta())
