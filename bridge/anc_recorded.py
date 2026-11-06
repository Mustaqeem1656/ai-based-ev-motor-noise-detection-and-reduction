"""
QuietDrive - noise cancellation applied to YOUR recorded motor sound.

  python anc_recorded.py data/normal_L0_S1_153012/audio.wav
  python anc_recorded.py data/imbalance_L2_S1_154501/audio.wav --seconds 12

What is real and what is simulated (say this in the pitch):
  REAL      : the motor noise (your recording) and the whine frequency, tracked from it
  SIMULATED : the speaker and the speaker-to-ear sound path
The canceller targets the motor whine and its 2nd harmonic. The 1x shaft tone, where an
imbalance shows up, is PROTECTED: never cancelled, so the driver still hears a fault.
Outputs next to the recording: anc_before.wav, anc_after.wav, anc_result.png
"""
import argparse
import os

import numpy as np
import soundfile as sf

from common import AUDIO_FS, WHINE_ORDER

MU, SEC_TAPS = 0.02, 64


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--anc-on", type=float, default=2.0, help="time ANC switches on (s)")
    a = ap.parse_args()

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
    v = 0.05 * rng.standard_normal(fs); S_hat = np.zeros(SEC_TAPS); vb = np.zeros(SEC_TAPS)
    for n in range(fs):
        vb = np.roll(vb, 1); vb[0] = v[n]
        e = S @ vb + 0.002 * rng.standard_normal() - S_hat @ vb
        S_hat += 0.01 * e * vb / (vb @ vb + 1e-6)

    f_whine = track_whine(x, fs)
    phase = 2 * np.pi * np.cumsum(f_whine) / fs
    targets = [1, 2]                                    # whine and its 2nd harmonic
    w = np.zeros((len(targets), 2)); xb = np.zeros((len(targets), 2, SEC_TAPS)); yb = np.zeros(SEC_TAPS)
    e_sig = np.zeros(N)
    for n in range(N):
        xr = np.array([[np.cos(h * phase[n]), np.sin(h * phase[n])] for h in targets])
        xb = np.roll(xb, 1, axis=2); xb[:, :, 0] = xr
        on = t[n] >= a.anc_on
        y = np.sum(w * xr) if on else 0.0
        yb = np.roll(yb, 1); yb[0] = y
        e = x[n] + S @ yb
        if on:
            xf = xb @ S_hat
            w -= MU * e * xf / (np.sum(xf ** 2) + 1e-6)
        e_sig[n] = e

    base = os.path.dirname(os.path.abspath(a.wav))
    sf.write(os.path.join(base, "anc_before.wav"), x.astype(np.float32), fs)
    sf.write(os.path.join(base, "anc_after.wav"), np.clip(e_sig, -1, 1).astype(np.float32), fs)

    seg0 = slice(int(0.3 * fs), int(min(a.anc_on, 2.0) * fs))
    seg1 = slice(int((a.anc_on + 1.5) * fs), N)
    fw = float(np.median(f_whine[seg1])); fshaft = fw / WHINE_ORDER
    print(f"Whine tracked at about {fw:.0f} Hz (shaft {fshaft:.0f} Hz, about {fshaft*60:.0f} rpm)")
    for h in targets:
        b = tone_db(x[seg1], fs, h * fw); af = tone_db(e_sig[seg1], fs, h * fw)
        print(f"  whine x{h} ({h*fw:6.0f} Hz): {b:6.1f} -> {af:6.1f} dB   reduction {b - af:5.1f} dB")
    b = tone_db(x[seg1], fs, fshaft); af = tone_db(e_sig[seg1], fs, fshaft)
    print(f"  shaft 1x  ({fshaft:6.0f} Hz): {b:6.1f} -> {af:6.1f} dB   (protected, should stay about the same)")
    r0 = np.sqrt(np.mean(x[seg1] ** 2)); r1 = np.sqrt(np.mean(e_sig[seg1] ** 2))
    print(f"  overall level change: {20*np.log10(r1/r0):.1f} dB")

    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 1, figsize=(10, 7))
    k = int(0.02 * fs)
    lvl = lambda s: 10 * np.log10(np.convolve(s ** 2, np.ones(k) / k, mode="same") + 1e-12)
    ax[0].plot(t, lvl(x), color="#9AA4AE", label="recorded motor noise")
    ax[0].plot(t, lvl(e_sig), color="#1D5FE0", label="with ANC")
    ax[0].axvline(a.anc_on, color="#D97706", ls="--"); ax[0].text(a.anc_on + .05, ax[0].get_ylim()[1] - 3, "ANC on", color="#D97706")
    ax[0].set_xlabel("time (s)"); ax[0].set_ylabel("level (dB)"); ax[0].legend(loc="lower left"); ax[0].grid(alpha=.3)
    ax[0].set_title("Real motor recording, simulated speaker path")
    for sig, c, lab in ((x[seg1], "#9AA4AE", "before"), (e_sig[seg1], "#1D5FE0", "after")):
        win = np.hanning(len(sig)); s = np.abs(np.fft.rfft(sig * win)) / win.sum() * 2
        ax[1].plot(np.fft.rfftfreq(len(sig), 1 / fs), 20 * np.log10(s + 1e-9), color=c, label=lab)
    ax[1].axvspan(fshaft - 10, fshaft + 10, color="#E11D48", alpha=.15)
    ax[1].text(fshaft + 12, -15, "protected 1x", color="#E11D48")
    ax[1].set_xlim(0, min(3 * fw, 4000)); ax[1].set_ylim(-110, 0)
    ax[1].set_xlabel("frequency (Hz)"); ax[1].set_ylabel("dB"); ax[1].legend(); ax[1].grid(alpha=.3)
    plt.tight_layout(); plt.savefig(os.path.join(base, "anc_result.png"), dpi=140)
    print(f"Saved anc_before.wav, anc_after.wav, anc_result.png in {base}")


if __name__ == "__main__":
    main()
