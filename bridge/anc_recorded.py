"""
QuietDrive - noise cancellation applied to YOUR recorded motor sound.

  py -3.12 bridge/anc_recorded.py bridge/data/normal_L0_S1_153012
  py -3.12 bridge/anc_recorded.py bridge/data/imbalance_L2_S1_154501 --seconds 12
  (or from the dashboard: Noise reduction page)

What is real and what is simulated (say this in the pitch):
  REAL      : the motor noise (your recording) and the whine frequency, tracked from it
  SIMULATED : the speaker and the speaker-to-ear sound path
The canceller targets the motor whine and its 2nd harmonic. The 1x shaft tone, where an
imbalance shows up, is PROTECTED: never cancelled, so the driver still hears a fault.
Outputs next to the recording: anc_before.wav, anc_after.wav, anc_result.png, anc_result.json
"""
import argparse
import json
import os

import numpy as np
import soundfile as sf

from common import AUDIO_FS, WHINE_ORDER

# MU 0.005: fast enough for ~25-30 dB, slow enough that a protected 1x tone in the error
# signal does not modulate the weights and leak back (0.02 made the 1x tone +3 dB louder).
MU, SEC_TAPS = 0.005, 64


def track_whine(x, fs, f_lo=80, f_hi=2000, hop=0.05, n=4096):
    """Whine frequency over time from the recording (stands in for the Hall/RPM reference)."""
    freqs = np.fft.rfftfreq(n, 1 / fs)
    m = (freqs >= f_lo) & (freqs <= f_hi)
    hops = np.arange(0, len(x) - n, int(hop * fs))
    f = []
    win = np.hanning(n)
    for h in hops:
        s = np.abs(np.fft.rfft(x[h:h + n] * win))
        k = np.argmax(s * m)
        # parabolic interpolation for a finer estimate
        if 0 < k < len(s) - 1:
            a, b, c = s[k - 1], s[k], s[k + 1]
            k = k + 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
        f.append(k * fs / n)
    f = np.array(f)
    f = np.convolve(np.pad(f, 3, mode="edge"), np.ones(7) / 7, mode="valid")   # smooth
    centers = hops + n // 2
    return np.interp(np.arange(len(x)), centers, f)


def tone_db(sig, fs, f):
    win = np.hanning(len(sig)); spec = np.abs(np.fft.rfft(sig * win)) / win.sum() * 2
    fr = np.fft.rfftfreq(len(sig), 1 / fs); i = np.argmin(np.abs(fr - f))
    return 20 * np.log10(spec[max(i - 3, 0):i + 4].max() + 1e-12)


def tone_present(sig, fs, f, margin_db=10.0):
    """Is there a real tone at f (peak well above the nearby noise floor)?"""
    win = np.hanning(len(sig)); spec = np.abs(np.fft.rfft(sig * win)) / win.sum() * 2
    fr = np.fft.rfftfreq(len(sig), 1 / fs); i = np.argmin(np.abs(fr - f))
    near = (np.abs(fr - f) < 25) & (np.abs(fr - f) > 4)
    return 20 * np.log10(spec[max(i - 3, 0):i + 4].max() + 1e-12) > 20 * np.log10(np.median(spec[near]) + 1e-12) + margin_db


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav", help="audio.wav or a session folder")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--anc-on", type=float, default=2.0, help="time ANC switches on (s)")
    a = ap.parse_args()

    if os.path.isdir(a.wav):
        a.wav = os.path.join(a.wav, "audio.wav")
    x, fs = sf.read(a.wav)
    if x.ndim > 1:
        x = x[:, 0]
    assert fs == AUDIO_FS
    x = x[int(1 * fs):int((1 + a.seconds) * fs)]
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.8
    N = len(x); t = np.arange(N) / fs
    rng = np.random.default_rng(1)

    # simulated speaker -> ear path, and its identification (what the firmware does at boot)
    S = np.zeros(SEC_TAPS); S[24], S[25], S[26], S[28], S[31], S[35] = 0.7, 0.45, 0.1, -0.25, 0.12, -0.05
    # 1) learn the speaker path with LMS on a 1 s noise burst
    v = 0.05 * rng.standard_normal(fs)
    d = np.convolve(v, S)[:fs] + 0.002 * rng.standard_normal(fs)
    vpad = np.concatenate([np.zeros(SEC_TAPS - 1), v])
    S_hat = np.zeros(SEC_TAPS)
    for n in range(fs):
        vb = vpad[n:n + SEC_TAPS][::-1]
        e = d[n] - S_hat @ vb
        S_hat += 0.01 * e * vb / (vb @ vb + 1e-6)

    # 2) narrowband FxLMS: cos/sin references at whine x1, x2 (from the tracked whine)
    f_whine = track_whine(x, fs)
    phase = 2 * np.pi * np.cumsum(f_whine) / fs
    targets = [1, 2]                      # the 1x shaft tone is NOT a target: protected
    ref = np.concatenate([[np.cos(h * phase), np.sin(h * phase)] for h in targets])     # (4, N)
    fref = np.array([np.convolve(r, S_hat)[:N] for r in ref])                          # filtered refs
    norm = np.sum(fref ** 2, axis=0) + 1e-6
    w = np.zeros(len(ref))
    y = np.zeros(N + SEC_TAPS)            # anti-noise, padded so y[n - k] works
    Sr = S[::-1]
    e_sig = np.zeros(N)
    on_n = int(a.anc_on * fs)
    for n in range(N):
        if n >= on_n:
            y[n + SEC_TAPS] = w @ ref[:, n]
        e = x[n] + Sr @ y[n + 1:n + SEC_TAPS + 1]      # what the ear hears
        if n >= on_n:
            w -= MU * e * fref[:, n] / norm[n]
        e_sig[n] = e

    base = os.path.dirname(os.path.abspath(a.wav))
    sf.write(os.path.join(base, "anc_before.wav"), x.astype(np.float32), fs)
    sf.write(os.path.join(base, "anc_after.wav"), np.clip(e_sig, -1, 1).astype(np.float32), fs)

    seg0 = slice(int(0.3 * fs), int(min(a.anc_on, 2.0) * fs))
    seg1 = slice(int((a.anc_on + 1.5) * fs), N)
    fw = float(np.median(f_whine[seg1])); fshaft = fw / WHINE_ORDER
    meta_path = os.path.join(base, "meta.json")
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    synthetic = meta.get("notes") == "synthetic"
    kind = "SYNTHETIC test motor" if synthetic else "REAL recorded motor noise"
    res = dict(synthetic=synthetic, session=os.path.basename(base), whine_hz=round(fw, 1), shaft_hz=round(fshaft, 1),
               rpm=round(fshaft * 60), anc_on_s=a.anc_on, tones=[],
               note=f"{kind}; simulated speaker path (identified by LMS first).")
    print(f"Whine tracked at about {fw:.0f} Hz (shaft {fshaft:.0f} Hz, about {fshaft*60:.0f} rpm)")
    for h in targets:
        b = tone_db(x[seg1], fs, h * fw); af = tone_db(e_sig[seg1], fs, h * fw)
        print(f"  whine x{h} ({h*fw:6.0f} Hz): {b:6.1f} -> {af:6.1f} dB   reduction {b - af:5.1f} dB")
        res["tones"].append(dict(name=f"whine x{h}", hz=round(h * fw, 1), before_db=round(b, 1),
                                 after_db=round(af, 1), reduction_db=round(b - af, 1), protected=False))
    b = tone_db(x[seg1], fs, fshaft); af = tone_db(e_sig[seg1], fs, fshaft)
    print(f"  shaft 1x  ({fshaft:6.0f} Hz): {b:6.1f} -> {af:6.1f} dB   (protected, should stay about the same)")
    res["tones"].append(dict(name="shaft 1x (protected)", hz=round(fshaft, 1), before_db=round(b, 1),
                             after_db=round(af, 1), reduction_db=round(b - af, 1), protected=True,
                             present=bool(tone_present(x[seg1], fs, fshaft))))
    r0 = np.sqrt(np.mean(x[seg1] ** 2)); r1 = np.sqrt(np.mean(e_sig[seg1] ** 2))
    print(f"  overall level change: {20*np.log10(r1/r0):.1f} dB")
    res["overall_db"] = round(float(20 * np.log10(r1 / r0)), 1)

    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 1, figsize=(10, 7))
    k = int(0.02 * fs)
    lvl = lambda s: 10 * np.log10(np.convolve(s ** 2, np.ones(k) / k, mode="same") + 1e-12)
    ax[0].plot(t, lvl(x), color="#9AA4AE", label="recorded motor noise")
    ax[0].plot(t, lvl(e_sig), color="#1D5FE0", label="with ANC")
    ax[0].axvline(a.anc_on, color="#D97706", ls="--"); ax[0].text(a.anc_on + .05, ax[0].get_ylim()[1] - 3, "ANC on", color="#D97706")
    ax[0].set_xlabel("time (s)"); ax[0].set_ylabel("level (dB)"); ax[0].legend(loc="lower left"); ax[0].grid(alpha=.3)
    ax[0].set_title(f"Fault-aware ANC (FxLMS) - {kind}, SIMULATED speaker path")
    for sig, c, lab in ((x[seg1], "#9AA4AE", "before"), (e_sig[seg1], "#1D5FE0", "after")):
        win = np.hanning(len(sig)); s = np.abs(np.fft.rfft(sig * win)) / win.sum() * 2
        ax[1].plot(np.fft.rfftfreq(len(sig), 1 / fs), 20 * np.log10(s + 1e-9), color=c, label=lab)
    ax[1].axvspan(fshaft - 10, fshaft + 10, color="#E11D48", alpha=.15)
    ax[1].text(fshaft + 12, -15, "protected 1x", color="#E11D48")
    ax[1].set_xlim(0, min(3 * fw, 4000)); ax[1].set_ylim(-110, 0)
    ax[1].set_xlabel("frequency (Hz)"); ax[1].set_ylabel("dB"); ax[1].legend(); ax[1].grid(alpha=.3)
    plt.tight_layout(); plt.savefig(os.path.join(base, "anc_result.png"), dpi=140)
    json.dump(res, open(os.path.join(base, "anc_result.json"), "w"), indent=2)
    print(f"Saved anc_before.wav, anc_after.wav, anc_result.png, anc_result.json in {base}")


if __name__ == "__main__":
    main()
