"""Labelled session recording from the ESP32-S3 stream, in the bridge/record.py folder format.

  <ESP32_DATA_DIR>/<label>_L<level>_S<speed>_<HHMMSS>/
      audio.wav       reference mic, 16 kHz mono int16      (record.py: same name, same rate)
      accel.csv       t_s,x_g,y_g,z_g  ADXL345 at 1600 Hz   (record.py: same header)
      meta.json       label, level, speed, seconds, audio_fs, source "esp32s3", sensor rates, drops
      features.jsonl  the device's own feat frames (t_s added): bridge/train.py trains on these by default
      telemetry.csv   t_s,rpm,current_a,temp_c,spl_ref_db,spl_err_db,anc_on

Times are seconds from the first audio sample, on the device clock (esp_timer / millis share one base).
"""
import base64
import csv
import json
import os
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

LABELS = ["normal", "imbalance", "loose_mount", "bearing"]


class Recorder:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.lock = threading.Lock()
        self.active = False
        self.last_result: Optional[dict] = None
        self._reset()

    def _reset(self):
        self.audio = []; self.acc = []; self.feats = []; self.tel = []
        self.t0_us = None; self.next_i = None; self.audio_n = 0
        self.gap_audio = 0; self.gap_vib = 0; self.vib_n = 0
        self.meta = {}; self.anc_seen = False; self.whine_order = None

    # ---------------- control ----------------
    def start(self, label: str, level: int, speed: int = 1, seconds: float = 90, notes: str = "") -> dict:
        if label not in LABELS:
            raise ValueError(f"label must be one of {LABELS}")
        level = 0 if label == "normal" else int(level)
        if label != "normal" and level not in (1, 2, 3):
            raise ValueError("level must be 1, 2 or 3 for a fault")
        with self.lock:
            self._reset()
            self.meta = dict(label=label, level=level, speed=int(speed), seconds=float(seconds), notes=notes,
                             started=datetime.now().isoformat(timespec="seconds"))
            self.active = True
        return self.status()

    def status(self) -> dict:
        s = self.audio_n / 16000.0
        return dict(active=self.active, label=self.meta.get("label"), level=self.meta.get("level"),
                    seconds=self.meta.get("seconds"), recorded_s=round(s, 1),
                    dropped_audio=self.gap_audio, dropped_vib=self.gap_vib, last=self.last_result)

    def due(self) -> bool:
        return self.active and self.audio_n >= self.meta.get("seconds", 0) * 16000

    # ---------------- stream input ----------------
    def feed(self, msg: dict):
        if not self.active:
            return
        t = msg.get("t")
        with self.lock:
            if t == "raw":
                self._raw_audio(msg)
            elif self.t0_us is None:
                return                                   # nothing before the first audio sample
            elif t == "rawv":
                self._raw_vib(msg)
            elif t == "feat":
                self.feats.append({**msg, "t_s": round(msg.get("ts", 0) / 1000 - self.t0_us / 1e6, 4)})
            elif t == "tel":
                self.anc_seen |= bool(msg.get("anc_on"))
                self.whine_order = msg.get("whine_order", self.whine_order)
                self.tel.append([round(msg.get("ts", 0) / 1000 - self.t0_us / 1e6, 4)] +
                                [msg.get(k) for k in ("rpm", "current_a", "temp_c", "spl_ref_db", "spl_err_db", "anc_on")])

    def _raw_audio(self, m):
        x = np.frombuffer(base64.b64decode(m["a"]), dtype="<i2")
        i0, n_orig = int(m["i0"]), len(x)
        if self.t0_us is None:
            self.t0_us = int(m["t0_us"]); self.next_i = i0
        gap = i0 - self.next_i
        if gap > 0:                                      # samples lost on the link: keep timing with silence
            self.audio.append(np.zeros(min(gap, 16000 * 5), dtype="<i2")); self.gap_audio += gap; self.audio_n += gap
        elif gap < 0:
            x = x[-gap:] if -gap < len(x) else x[:0]
        self.audio.append(x); self.audio_n += len(x)
        self.next_i = i0 + n_orig

    def _raw_vib(self, m):
        v = np.frombuffer(base64.b64decode(m["v"]), dtype="<i2").reshape(-1, 3).astype(float) * float(m.get("g_per_lsb", 0.0039))
        fs = float(m.get("fs", 1600)); n = len(v)
        t = (int(m["t_last_us"]) - (n - 1 - np.arange(n)) * 1e6 / fs - self.t0_us) / 1e6
        self.acc.append(np.column_stack([t, v])); self.vib_n += n
        self.gap_vib += int(m.get("gap", 0))

    # ---------------- save ----------------
    def stop(self) -> dict:
        with self.lock:
            if not self.active:
                return self.status()
            self.active = False
            res = self._save()
            self.last_result = res
            return res

    def _save(self) -> dict:
        if self.audio_n == 0:
            return dict(ok=False, reason="no raw audio received (is the ESP32-S3 streaming?)")
        meta = self.meta
        name = f"{meta['label']}_L{meta['level']}_S{meta['speed']}_{datetime.now():%H%M%S}"
        out = self.data_dir / name
        os.makedirs(out, exist_ok=True)
        x = np.concatenate(self.audio)
        with wave.open(str(out / "audio.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(x.tobytes())
        seconds = len(x) / 16000
        acc = np.vstack(self.acc) if self.acc else np.zeros((0, 4))
        if len(acc):
            acc = acc[acc[:, 0] >= 0]
            np.savetxt(out / "accel.csv", acc, delimiter=",", header="t_s,x_g,y_g,z_g", comments="", fmt="%.6f")
        with open(out / "features.jsonl", "w", encoding="utf-8") as f:
            for r in self.feats:
                f.write(json.dumps(r) + "\n")
        with open(out / "telemetry.csv", "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f); wr.writerow(["t_s", "rpm", "current_a", "temp_c", "spl_ref_db", "spl_err_db", "anc_on"])
            wr.writerows(self.tel)
        acc_rate = (len(acc) / (acc[-1, 0] - acc[0, 0])) if len(acc) > 1 and acc[-1, 0] > acc[0, 0] else 0.0
        rpms = [r[1] for r in self.tel if isinstance(r[1], (int, float))]
        full = dict(label=meta["label"], level=meta["level"], speed=meta["speed"], seconds=round(seconds, 2),
                    audio_fs=16000, source="esp32s3", notes=meta.get("notes", ""),
                    recorded=meta["started"],
                    sensors=dict(audio="INMP441 reference mic (left slot), int16", accel="ADXL345 +-4 g full-res",
                                 rpm="A3144 Hall", current="ACS712-5A", temp="DS18B20"),
                    audio_rate_hz=16000, accel_rate_hz=round(acc_rate, 1), accel_nominal_hz=1600,
                    feature_rate_hz=round(len(self.feats) / seconds, 2) if seconds else 0,
                    telemetry_rate_hz=round(len(self.tel) / seconds, 2) if seconds else 0,
                    dropped_audio_samples=self.gap_audio, dropped_accel_samples=self.gap_vib,
                    rpm_median=round(float(np.median(rpms)), 1) if rpms else None,
                    whine_order_device=self.whine_order, anc_on_during_recording=self.anc_seen)
        with open(out / "meta.json", "w", encoding="utf-8") as f:
            json.dump(full, f, indent=2)
        return dict(ok=True, folder=str(out), seconds=round(seconds, 1), accel_samples=len(acc), features=len(self.feats),
                    dropped_audio=self.gap_audio, dropped_vib=self.gap_vib,
                    warning="ANC was on during the recording" if self.anc_seen else None)
