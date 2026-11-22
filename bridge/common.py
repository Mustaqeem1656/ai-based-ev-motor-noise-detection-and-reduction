"""
QuietDrive bridge - shared code.
Features are computed the same way as the ESP32 firmware (audio bands, vibration
bands, vibration at 1x/2x shaft speed), so models trained here carry over to the
hardware later. The PREFILTER below is the single place where raw sensor data is
cleaned; recording checks, training and live all go through feature_row().
"""
import json
import time

import numpy as np
import requests
from scipy.signal import butter, resample_poly, sosfiltfilt

AUDIO_FS = 16000          # same as the firmware
FFT_N = 1024              # 15.6 Hz per bin
WIN_S = 1.0               # analysis window
HOP_S = 0.5               # a feature row every 0.5 s
DEFAULT_WHINE_ORDER = 3   # fallback before calibration: whine = 3 x shaft speed (3-slot brushed motor)
CALIB_PATH = __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)),
                                        "models", "calibration.json")
SHAFT_SEARCH_HZ = (20.0, 100.0)   # where the 1x shaft peak is searched in the phone vibration


def load_whine_order():
    """WHINE_ORDER measured by calibrate_whine_order() (train.py), else the default."""
    try:
        return int(json.load(open(CALIB_PATH))["whine_order"])
    except Exception:
        return DEFAULT_WHINE_ORDER


WHINE_ORDER = load_whine_order()  # whine_hz / WHINE_ORDER = shaft_hz (protected band, rpm display)
G = 9.81
LABELS = ["normal", "imbalance", "loose_mount", "bearing"]

N_AUDIO_BANDS, N_VIB_BANDS = 32, 16
FEATURE_NAMES = ([f"audio_b{i}" for i in range(N_AUDIO_BANDS)] +
                 [f"vib_b{i}" for i in range(N_VIB_BANDS)] +
                 ["vib_1x", "vib_2x", "vib_rms", "audio_rms_db", "whine_hz"])
AUDIO_IDX = [i for i, n in enumerate(FEATURE_NAMES) if n.startswith("audio") or n == "whine_hz"]
VIB_IDX = [i for i, n in enumerate(FEATURE_NAMES) if n.startswith("vib")]
I_AUDIO_RMS = FEATURE_NAMES.index("audio_rms_db")


# ---------------- PREFILTER (the only place raw data is filtered) ----------------
_AUDIO_SOS = (butter(4, 50, "highpass", fs=AUDIO_FS, output="sos"),
              butter(4, 7000, "lowpass", fs=AUDIO_FS, output="sos"))


def prefilter_audio(x):
    """Zero-phase high-pass 50 Hz + low-pass 7 kHz (order 4) at 16 kHz."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 64:
        return x - x.mean() if len(x) else x
    for sos in _AUDIO_SOS:
        x = sosfiltfilt(sos, x)
    return x


def prefilter_vib(t, xyz):
    """Irregular phone samples -> uniform time, band-pass 3 Hz..0.45*fs, gravity/DC removed.
    Returns (y [n,3] in g, fs) or (None, 0) if there is not enough data."""
    t = np.asarray(t, dtype=np.float64); xyz = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    if len(t) < 32:
        return None, 0.0
    o = np.argsort(t); t, xyz = t[o], xyz[o]
    fs = 1.0 / np.median(np.diff(t))
    if not np.isfinite(fs) or fs < 20:
        return None, 0.0
    n = int((t[-1] - t[0]) * fs) + 1
    tu = t[0] + np.arange(n) / fs
    y = np.stack([np.interp(tu, t, xyz[:, a]) for a in range(3)], axis=1)
    y -= y.mean(axis=0)                                   # gravity / DC
    sos = butter(4, [3.0, 0.45 * fs], "bandpass", fs=fs, output="sos")
    if n > 3 * (2 * len(sos) + 1):
        y = sosfiltfilt(sos, y, axis=0)
    return y, fs


# ---------------- audio ----------------
def audio_power_spectrum(x):
    """Averaged power spectrum (Welch style) of a mono window. Returns freqs, power."""
    x = np.asarray(x, dtype=np.float64)
    win = np.hanning(FFT_N)
    frames = [x[i:i + FFT_N] for i in range(0, len(x) - FFT_N + 1, FFT_N // 2)]
    if not frames:
        frames = [np.pad(x, (0, FFT_N - len(x)))]
    p = np.zeros(FFT_N // 2 + 1)
    for f in frames:
        spec = np.fft.rfft((f - f.mean()) * win)
        p += (2 * np.abs(spec) / win.sum()) ** 2
    p /= len(frames)
    return np.fft.rfftfreq(FFT_N, 1 / AUDIO_FS), p


def log_bands(freqs, power, n, f_lo, f_hi):
    edges = f_lo * (f_hi / f_lo) ** (np.arange(n + 1) / n)
    out = np.empty(n)
    for b in range(n):
        m = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if not m.any():
            m = np.zeros_like(freqs, dtype=bool); m[np.argmin(np.abs(freqs - edges[b]))] = True
        out[b] = 10 * np.log10(power[m].mean() + 1e-14)
    return out


def whine_frequency(freqs, power, f_lo=80, f_hi=2000):
    """Spectral peak 80-2000 Hz, refined with parabolic interpolation."""
    idx = np.where((freqs >= f_lo) & (freqs <= f_hi))[0]
    i = idx[np.argmax(power[idx])]
    if 0 < i < len(power) - 1:
        a, b, c = np.log(power[i - 1:i + 2] + 1e-20)
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        return float(freqs[i] + np.clip(d, -0.5, 0.5) * (freqs[1] - freqs[0]))
    return float(freqs[i])


def display_spectrum(freqs, power, bins=128, fmax=4000):
    """128 bins, 0-4000 Hz, in dB - the dashboard 'spec' message."""
    m = freqs < fmax
    p = power[m][: bins * 2].reshape(bins, -1).mean(axis=1)
    return 10 * np.log10(p + 1e-14) + 100   # +100: rough offset, laptop mics are not calibrated


# ---------------- vibration ----------------
def vib_features(y, fs, shaft_hz):
    """Prefiltered uniform vibration (g) -> 16 bands, amplitude at 1x / 2x shaft, RMS."""
    empty = (np.full(N_VIB_BANDS, -120.0), 0.0, 0.0, 0.0)
    if y is None or len(y) < 32:
        return empty
    n = len(y)
    win = np.hanning(n)
    power = sum((2 * np.abs(np.fft.rfft(y[:, a] * win)) / win.sum()) ** 2 for a in range(3))
    freqs = np.fft.rfftfreq(n, 1 / fs)
    nyq = freqs[-1]
    bands = log_bands(freqs, power, N_VIB_BANDS, 2.0, max(4.0, 0.9 * nyq))

    def amp_at(f):
        if f <= 0 or f >= 0.45 * fs:
            return 0.0
        i = np.argmin(np.abs(freqs - f))
        return float(np.sqrt(power[max(i - 1, 0):i + 2].max()))
    vrms = float(np.sqrt(np.mean(np.sum(y ** 2, axis=1))))
    return bands, amp_at(shaft_hz), amp_at(2 * shaft_hz), vrms


# ---------------- whine-order calibration ----------------
def _vib_peak_hz(t, xyz, f_lo, f_hi):
    """Strongest phone-vibration peak between f_lo and f_hi (zero-padded FFT, ~0.03 Hz steps)."""
    y, fs = prefilter_vib(t, xyz)
    if y is None:
        return None
    n = len(y); w = np.hanning(n)
    power = sum(np.abs(np.fft.rfft(y[:, a] * w, 8 * n)) ** 2 for a in range(3))
    freqs = np.fft.rfftfreq(8 * n, 1 / fs)
    m = (freqs >= f_lo) & (freqs <= min(f_hi, 0.45 * fs))
    return float(freqs[m][np.argmax(power[m])]) if m.any() else None


def calibrate_whine_order(data_dir, label="imbalance", level=3, win_s=4.0, save=True):
    """Measure how many whine cycles there are per shaft turn from the given fault recordings
    (imbalance level 3 shakes the phone strongly at exactly 1x shaft speed).
    Per 4 s window: whine = audio peak, shaft = strongest vibration peak 20-100 Hz.
    Sets WHINE_ORDER = round(median whine / median shaft), saves models/calibration.json.
    Returns the result dict, or None if there are no suitable recordings."""
    global WHINE_ORDER
    import glob, os
    import soundfile as sf
    whines, shafts, per_session = [], [], {}
    for f in sorted(glob.glob(os.path.join(data_dir, "*"))):
        try:
            meta = json.load(open(os.path.join(f, "meta.json")))
        except Exception:
            continue
        if meta.get("label") != label or int(meta.get("level", -1)) != level:
            continue
        acc_path = os.path.join(f, "accel.csv")
        if not os.path.exists(acc_path):
            continue
        x, fs = sf.read(os.path.join(f, "audio.wav"))
        x = x[:, 0] if x.ndim > 1 else x
        acc = np.loadtxt(acc_path, delimiter=",", skiprows=1).reshape(-1, 4)
        ratios = []
        for t0 in np.arange(1.0, len(x) / fs - win_s, win_s / 2):
            freqs, p = audio_power_spectrum(prefilter_audio(x[int(t0 * fs):int((t0 + win_s) * fs)]))
            m = (acc[:, 0] >= t0) & (acc[:, 0] < t0 + win_s)
            sh = _vib_peak_hz(acc[m, 0], acc[m, 1:4], *SHAFT_SEARCH_HZ)
            if sh:
                wh = whine_frequency(freqs, p)
                whines.append(wh); shafts.append(sh); ratios.append(wh / sh)
        if ratios:
            per_session[os.path.basename(f)] = round(float(np.median(ratios)), 2)
    if not shafts:
        return None
    whine_hz, shaft_hz = float(np.median(whines)), float(np.median(shafts))
    ratio = whine_hz / shaft_hz
    res = dict(whine_order=int(round(ratio)), ratio=round(ratio, 2), whine_hz=round(whine_hz, 1),
               shaft_hz=round(shaft_hz, 2), rpm=round(shaft_hz * 60), per_session_ratio=per_session,
               source=f"{label} level {level}")
    WHINE_ORDER = res["whine_order"]
    if save:
        os.makedirs(os.path.dirname(CALIB_PATH), exist_ok=True)
        json.dump(res, open(CALIB_PATH, "w"), indent=2)
    return res


# ---------------- one feature row ----------------
def feature_row(audio, acc_t, acc_xyz, shaft_hz=None):
    """Raw 16 kHz audio window + raw accelerometer samples -> (feature vector, extras).
    shaft_hz: measured shaft speed (ESP32-S3 Hall sensor); default = whine / WHINE_ORDER (phone)."""
    audio = prefilter_audio(audio)
    y, vfs = prefilter_vib(acc_t, acc_xyz)
    freqs, p = audio_power_spectrum(audio)
    a_bands = log_bands(freqs, p, N_AUDIO_BANDS, 50, 8000)
    whine = whine_frequency(freqs, p)
    shaft = shaft_hz if shaft_hz else whine / WHINE_ORDER
    v_bands, v1, v2, vrms = vib_features(y, vfs, shaft)
    rms_db = 20 * np.log10(np.sqrt(np.mean(np.square(audio))) + 1e-9)
    row = np.concatenate([a_bands, v_bands, [v1, v2, vrms, rms_db, whine]])
    extras = dict(freqs=freqs, power=p, whine=whine, shaft_hz=shaft, rpm=shaft * 60, vib_rms=vrms,
                  audio_bands=a_bands, vib_bands=v_bands, vib_1x=v1, vib_2x=v2, rms_db=rms_db, vib_fs=vfs)
    return row, extras


def to_16k(x, fs):
    if fs == AUDIO_FS:
        return x
    from math import gcd
    g = gcd(int(fs), AUDIO_FS)
    return resample_poly(x, AUDIO_FS // g, int(fs) // g)


# ---------------- synthetic motor (for --fake modes) ----------------
class FakeMotor:
    """A rough synthetic brushed motor: whine at WHINE_ORDER x shaft plus fault signatures.
    imbalance -> 1x shaft shaking (+ whine modulated once per turn)
    loose_mount -> 2x shaft rattle (clicks twice per turn)
    bearing -> high-pitched grinding (3-6 kHz noise) and high-frequency vibration."""

    def __init__(self, fault="normal", level=0, rpm=2400, acc_fs=400, seed=None):
        self.fault, self.level, self.rpm, self.acc_fs = fault, level, rpm, acc_fs
        self.rng = np.random.default_rng(seed)
        self.t_audio = 0.0
        self.t_acc = 0.0
        from scipy.signal import butter as _b
        self._bear_sos = _b(4, [3000, 6000], "bandpass", fs=AUDIO_FS, output="sos")

    def set(self, fault, level):
        self.fault, self.level = fault, (0 if fault == "normal" else max(1, min(3, level)))

    def audio(self, n):
        from scipy.signal import sosfilt
        t = self.t_audio + np.arange(n) / AUDIO_FS
        self.t_audio += n / AUDIO_FS
        rpm = self.rpm * (1 + 0.01 * np.sin(2 * np.pi * 0.2 * t))       # slight speed wobble
        shaft = rpm / 60
        ph = getattr(self, "_ph", 0.0) + 2 * np.pi * np.cumsum(shaft) / AUDIO_FS   # shaft angle
        self._ph = float(ph[-1] % (2 * np.pi))
        k = self.level / 3
        env = 1.0
        if self.fault == "imbalance":
            env = 1 + 0.6 * k * np.sin(ph)
        x = 0.08 * env * np.sin(WHINE_ORDER * ph) + 0.03 * np.sin(2 * WHINE_ORDER * ph)
        x += 0.004 * self.rng.standard_normal(n)
        if self.fault == "imbalance":
            x += 0.05 * k * np.sin(2 * ph)
        if self.fault == "loose_mount":
            clicks = (np.sin(2 * ph) > 0.97).astype(float)
            x += 0.12 * k * clicks * self.rng.standard_normal(n) + 0.03 * k * np.sin(4 * ph)
        if self.fault == "bearing":
            x += 0.05 * k * sosfilt(self._bear_sos, self.rng.standard_normal(n)) * 4
        return x

    def accel(self, seconds):
        """Phone-like samples (t, xyz in g, gravity on z) with timing jitter."""
        n = int(seconds * self.acc_fs)
        t = self.t_acc + np.arange(n) / self.acc_fs + self.rng.normal(0, 0.0003, n)
        self.t_acc += n / self.acc_fs
        shaft = self.rpm / 60
        k = self.level / 3
        xyz = 0.01 * self.rng.standard_normal((n, 3))
        xyz[:, 2] += 1.0
        xyz[:, 0] += 0.02 * np.sin(2 * np.pi * shaft * t)                 # residual imbalance
        if self.fault == "imbalance":
            xyz[:, 0] += 0.25 * k * np.sin(2 * np.pi * shaft * t)
            xyz[:, 1] += 0.25 * k * np.cos(2 * np.pi * shaft * t)
        if self.fault == "loose_mount":
            xyz[:, 2] += 0.2 * k * np.sin(2 * np.pi * 2 * shaft * t) + 0.05 * k * self.rng.standard_normal(n)
        if self.fault == "bearing":
            xyz += 0.06 * k * self.rng.standard_normal((n, 3))
        return t, xyz


# ---------------- phyphox (phone accelerometer over WiFi) ----------------
class Phyphox:
    """Talks to the phyphox app's Remote Access server.
    In phyphox open 'Acceleration (without g)', tap the three dots, Enable remote access,
    and use the address it shows (e.g. 192.168.43.20:8080). Phone and laptop on the same WiFi."""

    def __init__(self, address, bx="accX", by="accY", bz="accZ", bt="acc_time"):
        self.base = address if address.startswith("http") else "http://" + address
        self.bx, self.by, self.bz, self.bt = bx, by, bz, bt
        self.last_t = None

    def _get(self, path, timeout=2):
        r = requests.get(self.base + path, timeout=timeout)
        r.raise_for_status()
        return r.json() if "json" in r.headers.get("content-type", "") or r.text.startswith("{") else r.text

    def buffers(self):
        cfg = self._get("/config")
        return [b["name"] for b in cfg.get("buffers", [])]

    def control(self, cmd):
        self._get(f"/control?cmd={cmd}")

    def restart(self):
        self.control("stop"); self.control("clear"); self.control("start")
        self.last_t = None

    def fetch_new(self):
        """New samples since the last call: (t[s], xyz[g])."""
        th = "full" if self.last_t is None else f"{self.last_t:.6f}|{self.bt}"
        q = "&".join([f"{b}={th}" for b in (self.bx, self.by, self.bz)] +
                     [f"{self.bt}={'full' if self.last_t is None else f'{self.last_t:.6f}'}"])
        data = self._get("/get?" + q)["buffer"]
        t = np.array(data[self.bt]["buffer"], dtype=float)
        cols = [np.array(data[b]["buffer"], dtype=float) for b in (self.bx, self.by, self.bz)]
        n = min([len(t)] + [len(c) for c in cols])
        t = t[:n]
        xyz = np.stack([c[:n] for c in cols], axis=1) / G      # m/s2 -> g
        ok = np.isfinite(t) & np.isfinite(xyz).all(axis=1)
        t, xyz = t[ok], xyz[ok]
        if len(t):
            self.last_t = float(t[-1])
        return t, xyz


def now_ms():
    return int(time.time() * 1000)


def dumps(obj):
    """JSON with numpy types converted and NaN/inf sent as null."""
    def clean(v):
        if isinstance(v, (np.floating, float)):
            v = float(v)
            return round(v, 4) if np.isfinite(v) else None
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.bool_,)):
            return bool(v)
        if isinstance(v, (np.ndarray, list, tuple)):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v
    return json.dumps(clean(obj), separators=(",", ":"))
