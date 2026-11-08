"""
QuietDrive - live cabin-mic noise removal (what an EV does for phone calls / voice assistants).

Tracks the motor whine every 50 ms and removes it, plus its 2nd and 3rd harmonics, with
narrow adaptive IIR notch filters whose frequencies glide smoothly. Speech is left intact
(each notch is only a few Hz wide). The 1x shaft frequency of an active fault is never
notched: protected frequencies come from the backend's live 'inf' (or --protect-hz).

  py -3.12 bridge/clean_voice.py                  # laptop mic, use HEADPHONES for playback
  py -3.12 bridge/clean_voice.py --mic 2
  py -3.12 bridge/clean_voice.py --fake --play    # synthetic motor + synthetic voice
  py -3.12 bridge/clean_voice.py --selftest       # offline check: whine reduction, speech kept

Keys: c = play CLEANED, r = play RAW, o = playback on/off, s = save last 10 s
      (raw.wav + clean.wav in bridge/recordings/), q = quit.
Dashboard: spec frames mic "ref" (raw) and "err" (cleaned) -> both waterfalls + reduction.
"""
import argparse
import collections
import json
import os
import threading
import time
from datetime import datetime

import numpy as np
import soundfile as sf
from scipy.signal import iirnotch, sosfilt

from common import (AUDIO_FS, WHINE_ORDER, FakeMotor, audio_power_spectrum, display_spectrum,
                    now_ms, prefilter_audio, to_16k)
from live import Sender

HERE = os.path.dirname(os.path.abspath(__file__))
N_HARM = 3            # notch whine x1, x2, x3
Q = 40.0              # narrow: bandwidth = f / Q (3 Hz at 120 Hz)
SUB_S = 0.010         # notch frequencies are updated every 10 ms
GLIDE = 0.25          # fraction of the way to the new target per update (smooth, no clicks)
TRACK_S = 0.050       # whine tracked every 50 ms
PASS = np.array([1.0, 0, 0, 1.0, 0, 0])


# ---------------- notch bank ----------------
class NotchBank:
    """Cascade of N_HARM second-order IIR notches with gliding centre frequencies."""

    def __init__(self, fs):
        self.fs = fs
        self.zi = np.zeros((N_HARM, 2))
        self.cur = np.zeros(N_HARM)        # current notch frequencies (0 = off)
        self.target = np.zeros(N_HARM)
        self.sub = max(1, int(SUB_S * fs))

    def set_target(self, freqs):
        self.target = np.asarray(freqs, dtype=float)

    def _sos(self):
        rows = []
        for f in self.cur:
            if f <= 0:
                rows.append(PASS)
            else:
                b, a = iirnotch(f, Q, fs=self.fs)
                rows.append(np.concatenate([b, a]))
        return np.array(rows)

    def process(self, x):
        out = np.empty_like(x)
        for i in range(0, len(x), self.sub):
            on = self.target > 0
            jump = on & (self.cur <= 0)               # a notch switching on starts at its target
            self.cur[jump] = self.target[jump]
            self.cur[on] += GLIDE * (self.target[on] - self.cur[on])
            self.cur[~on] = 0.0
            out[i:i + self.sub], self.zi = sosfilt(self._sos(), x[i:i + self.sub], zi=self.zi)
        return out


# ---------------- whine tracker ----------------
class WhineTracker:
    """Spectral peak 80-2000 Hz on the last 0.256 s, with jump rejection."""

    def __init__(self, fs):
        self.fs, self.f, self.cand, self.n_cand = fs, None, None, 0
        self.nfft = 1 << int(np.ceil(np.log2(fs * 0.5)))     # zero-padded: ~2 Hz bins

    def update(self, x):
        x = x - x.mean()
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), self.nfft)) ** 2
        freqs = np.fft.rfftfreq(self.nfft, 1 / self.fs)
        idx = np.where((freqs >= 80) & (freqs <= 2000))[0]
        i = idx[np.argmax(spec[idx])]
        if spec[i] < 30 * np.median(spec[idx]) + 1e-18:      # no clear tone (~15 dB): notches off
            self.f = None
            return None
        a, b, c = np.log(spec[i - 1:i + 2] + 1e-30)
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        f_new = freqs[i] + np.clip(d, -0.5, 0.5) * (freqs[1] - freqs[0])
        if self.f is None or abs(f_new - self.f) / self.f < 0.1:
            self.f = f_new if self.f is None else self.f + 0.5 * (f_new - self.f)
            self.n_cand = 0
        else:                                                 # big jump: accept only if it persists
            self.n_cand = self.n_cand + 1 if self.cand and abs(f_new - self.cand) / self.cand < 0.1 else 1
            self.cand = f_new
            if self.n_cand >= 3:
                self.f, self.n_cand = f_new, 0
        return self.f


def notch_targets(whine, fs, protected):
    """whine x1..x3, minus anything above 0.45 fs or near a protected (fault) frequency."""
    if not whine:
        return np.zeros(N_HARM)
    out = []
    for k in range(1, N_HARM + 1):
        f = k * whine
        near = any(abs(f - p) < max(5.0, 3 * f / Q) for p in protected)
        out.append(0.0 if (f > 0.45 * fs or near) else f)
    return np.array(out)


def band_reduction_db(raw, clean, fs, freqs):
    """Power removed in the notched bands (raw vs cleaned), in dB."""
    freqs = [f for f in freqs if f > 0]
    if not freqs:
        return 0.0
    n = 1 << int(np.ceil(np.log2(len(raw))))
    w = np.hanning(len(raw))
    pr = np.abs(np.fft.rfft(raw * w, n)) ** 2
    pc = np.abs(np.fft.rfft(clean * w, n)) ** 2
    fr = np.fft.rfftfreq(n, 1 / fs)
    m = np.zeros_like(fr, dtype=bool)
    for f in freqs:
        m |= np.abs(fr - f) < max(4.0, 2 * f / Q)
    return float(10 * np.log10((pr[m].sum() + 1e-18) / (pc[m].sum() + 1e-18)))


# ---------------- synthetic voice (for --fake / --selftest) ----------------
class FakeVoice:
    """Vowel-like voice: glottal pulses at a gliding pitch through 3 formants, in syllables."""

    def __init__(self, fs=AUDIO_FS, seed=1):
        from scipy.signal import butter
        self.fs, self.t, self.rng = fs, 0.0, np.random.default_rng(seed)
        self.formants = [butter(2, [f * 0.85, f * 1.15], "bandpass", fs=fs, output="sos") for f in (700, 1200, 2600)]
        self.zi = [np.zeros((s.shape[0], 2)) for s in self.formants]
        self.phase = 0.0

    def __call__(self, n):
        t = self.t + np.arange(n) / self.fs
        self.t += n / self.fs
        pitch = 160 + 30 * np.sin(2 * np.pi * 0.7 * t)
        ph = self.phase + np.cumsum(pitch) / self.fs
        self.phase = ph[-1] % 1.0
        pulses = (np.diff(np.floor(np.concatenate([[ph[0] - 1e-9], ph]))) > 0).astype(float)
        y = 0.0
        for k, (s, g) in enumerate(zip(self.formants, (1.0, 0.6, 0.3))):
            out, self.zi[k] = sosfilt(s, pulses, zi=self.zi[k])
            y = y + g * out
        gate = (np.sin(2 * np.pi * 2.5 * t) > -0.2).astype(float)     # syllables with pauses
        return 0.4 * y * gate


# ---------------- self test (offline, deterministic) ----------------
def selftest(seconds=8.0):
    fs = AUDIO_FS
    motor, voice = FakeMotor("normal", 0, rpm=2400, seed=0), FakeVoice(fs)
    blk = int(TRACK_S * fs)
    mix_bank, voice_bank, tracker = NotchBank(fs), NotchBank(fs), WhineTracker(fs)
    hist = np.zeros(int(0.256 * fs))
    raw, clean, v_in, v_out, m_in = [], [], [], [], []
    for _ in range(int(seconds / TRACK_S)):
        m, v = motor.audio(blk), voice(blk)
        x = m + v
        hist = np.concatenate([hist[blk:], x])
        tg = notch_targets(tracker.update(hist), fs, protected=[])
        mix_bank.set_target(tg); voice_bank.set_target(tg)
        raw.append(x); clean.append(mix_bank.process(x))
        v_in.append(v); v_out.append(voice_bank.process(v)); m_in.append(m)
    raw, clean, v_in, v_out = map(np.concatenate, (raw, clean, v_in, v_out))
    s = slice(fs, None)                                       # skip the first second (tracker warm-up)
    whine = tracker.f
    red = band_reduction_db(raw[s], clean[s], fs, [k * whine for k in (1, 2, 3)])
    keep = 10 * np.log10(np.sum(v_out[s] ** 2) / np.sum(v_in[s] ** 2))
    corr = np.corrcoef(v_in[s], v_out[s])[0, 1]
    print(f"whine tracked: {whine:.1f} Hz (true {2400 / 60 * WHINE_ORDER:.1f} Hz)")
    print(f"whine-band reduction: {red:.1f} dB")
    print(f"voice energy kept: {keep:+.2f} dB, voice waveform correlation: {corr:.3f}")
    # protection check: shaft 40 Hz protected must never be notched
    tg = notch_targets(whine, fs, protected=[whine / WHINE_ORDER, whine])
    print(f"with a fault protecting {whine:.0f} Hz the notches are {np.round(tg, 1)} (0 = off)")
    out = os.path.join(HERE, "recordings", "selftest")
    os.makedirs(out, exist_ok=True)
    sf.write(os.path.join(out, "raw.wav"), raw.astype(np.float32), fs)
    sf.write(os.path.join(out, "clean.wav"), clean.astype(np.float32), fs)
    print("saved", out)
    return red, keep, corr


# ---------------- live ----------------
class Ring:
    def __init__(self, n):
        self.buf, self.lock = np.zeros(n), threading.Lock()

    def push(self, x):
        with self.lock:
            k = min(len(x), len(self.buf))
            self.buf = np.roll(self.buf, -k); self.buf[-k:] = x[-k:]

    def last(self, n):
        with self.lock:
            return self.buf[-n:].copy()


def listen_protected(url, state):
    """Follow the backend's live inf: protected frequencies while a fault is active."""
    import websocket
    while True:
        try:
            ws = websocket.create_connection(url, timeout=5)
            while True:
                m = json.loads(ws.recv())
                if m.get("t") == "inf":
                    state["fault"] = m.get("fault", "normal")
                    state["protected"] = [float(p) for p in (m.get("protected_hz") or []) if p] \
                        if state["fault"] != "normal" else []
        except Exception:
            time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mic", type=int, default=None)
    ap.add_argument("--out", type=int, default=None, help="output device (headphones!)")
    ap.add_argument("--backend", default="ws://localhost:8000/ws/device/QD-01")
    ap.add_argument("--vid", default="QD-01")
    ap.add_argument("--protect-hz", type=float, nargs="*", default=[], help="always protect these")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fake", action="store_true", help="synthetic motor + voice instead of the mic")
    ap.add_argument("--fake-fault", default="normal")
    ap.add_argument("--play", action="store_true", help="start with playback on")
    ap.add_argument("--save-after", type=float, default=0, help="auto-save after N seconds (for tests)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); return

    fs = AUDIO_FS
    if not a.fake:
        import sounddevice as sd
        try:
            sd.check_input_settings(device=a.mic, samplerate=AUDIO_FS, channels=1)
        except Exception:
            fs = int(sd.query_devices(a.mic, "input")["default_samplerate"])
    bank, tracker = NotchBank(fs), WhineTracker(fs)
    raw_ring, clean_ring = Ring(10 * fs), Ring(10 * fs)
    st = dict(mode="clean", play=a.play, fault="normal", protected=[], whine=None, red=0.0)
    sender = Sender(a.backend, a.dry_run)
    if not a.dry_run:
        threading.Thread(target=listen_protected, daemon=True,
                         args=(a.backend.replace("/ws/device/", "/ws/live/"), st)).start()

    def process(x):
        y = bank.process(x)
        raw_ring.push(x); clean_ring.push(y)
        return x if st["mode"] == "raw" else y

    def save():
        out = os.path.join(HERE, "recordings", f"clean_{datetime.now():%H%M%S}")
        os.makedirs(out, exist_ok=True)
        sf.write(os.path.join(out, "raw.wav"), raw_ring.last(10 * fs).astype(np.float32), fs)
        sf.write(os.path.join(out, "clean.wav"), clean_ring.last(10 * fs).astype(np.float32), fs)
        print(f"\nsaved 10 s: {out}\\raw.wav, clean.wav")

    # ---- audio in/out ----
    if a.fake:
        import sounddevice as sd
        motor, voice = FakeMotor(a.fake_fault, 2, rpm=2400), FakeVoice(fs)
        out_stream = None

        def gen():
            nonlocal out_stream
            blk = int(0.05 * fs)
            t_next = time.time()
            while True:
                y = process(motor.audio(blk) + voice(blk))
                if st["play"]:
                    try:
                        if out_stream is None:
                            out_stream = sd.OutputStream(samplerate=fs, channels=1, device=a.out); out_stream.start()
                        out_stream.write(y.astype(np.float32)[:, None])
                    except Exception as e:
                        print("\nno audio output:", e); st["play"] = False
                t_next += 0.05
                time.sleep(max(0.0, t_next - time.time()))
        threading.Thread(target=gen, daemon=True).start()
        print("Fake motor + fake voice running.")
    else:
        import sounddevice as sd

        def cb(indata, outdata, frames, t, status):
            y = process(indata[:, 0].astype(np.float64))
            outdata[:, 0] = y if st["play"] else 0.0
        stream = sd.Stream(samplerate=fs, channels=1, device=(a.mic, a.out), callback=cb, blocksize=int(0.01 * fs))
        stream.start()
        print(f"Mic running at {fs} Hz. Use HEADPHONES for playback (speakers feed back into the mic).")

    # ---- keys ----
    def keys():
        try:
            import msvcrt
        except ImportError:
            return
        while True:
            k = msvcrt.getwch().lower()
            if k == "c": st["mode"] = "clean"
            elif k == "r": st["mode"] = "raw"
            elif k == "o": st["play"] = not st["play"]
            elif k == "s": save()
            elif k == "q": os._exit(0)
    threading.Thread(target=keys, daemon=True).start()
    print("Keys: c = CLEANED, r = RAW, o = playback on/off, s = save 10 s, q = quit")

    # ---- tracking (50 ms) + dashboard frames (5 Hz) ----
    t0, last_spec, saved = time.time(), 0.0, False
    while True:
        time.sleep(TRACK_S)
        whine = tracker.update(raw_ring.last(int(0.256 * fs)))
        tg = notch_targets(whine, fs, st["protected"] + list(a.protect_hz))
        bank.set_target(tg)
        st["whine"] = whine
        now = time.time()
        if now - last_spec >= 0.2:
            last_spec = now
            r, c = raw_ring.last(int(0.5 * fs)), clean_ring.last(int(0.5 * fs))
            st["red"] = band_reduction_db(r, c, fs, tg)
            for mic, sig in (("ref", r), ("err", c)):
                f, p = audio_power_spectrum(prefilter_audio(to_16k(sig[-int(0.25 * fs):], fs)))
                msg = dict(t="spec", ts=now_ms(), vid=a.vid, mic=mic, fmax=4000, bins=display_spectrum(f, p))
                if mic == "err":
                    msg.update(reduction_db=st["red"], whine_hz=whine, notch_hz=[x for x in tg if x > 0])
                sender.send(msg)
            prot = ",".join(f"{p:.0f}" for p in st["protected"]) or "-"
            print(f"  whine {whine or 0:6.1f} Hz  notches {np.round(tg).astype(int)}  "
                  f"reduction {st['red']:5.1f} dB  playing {st['mode'].upper() if st['play'] else 'off':5s}  "
                  f"protected {prot}   ", end="\r")
        if a.save_after and not saved and now - t0 > a.save_after:
            save(); saved = True


if __name__ == "__main__":
    main()
