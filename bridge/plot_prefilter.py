"""
QuietDrive - spectrum of one recording before vs after the prefilter (PNG for slides).

  py -3.12 bridge/plot_prefilter.py bridge/data/normal_L0_S1_153012
Saves prefilter.png inside the session folder.
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf
from scipy.signal import welch

from common import prefilter_audio, prefilter_vib

folder = sys.argv[1] if len(sys.argv) > 1 else sys.exit(__doc__)
x, fs = sf.read(os.path.join(folder, "audio.wav"))
x = x if x.ndim == 1 else x[:, 0]
fig, axes = plt.subplots(2, 1, figsize=(8, 6))
for sig, name in ((x, "raw"), (prefilter_audio(x), "prefiltered (HP 50 Hz + LP 7 kHz)")):
    f, p = welch(sig, fs, nperseg=4096)
    axes[0].semilogx(f[1:], 10 * np.log10(p[1:] + 1e-20), label=name)
axes[0].set(title="Audio spectrum", xlabel="Hz", ylabel="dB"); axes[0].legend(); axes[0].grid(alpha=.3)

acc_path = os.path.join(folder, "accel.csv")
if os.path.exists(acc_path):
    acc = np.loadtxt(acc_path, delimiter=",", skiprows=1).reshape(-1, 4)
    t = acc[:, 0]; afs = 1 / np.median(np.diff(t))
    tu = t[0] + np.arange(int((t[-1] - t[0]) * afs)) / afs
    raw = np.stack([np.interp(tu, t, acc[:, k]) for k in (1, 2, 3)], 1)
    filt, _ = prefilter_vib(t, acc[:, 1:4])
    for sig, name in ((raw, "raw (with gravity)"), (filt, "prefiltered (BP 3 Hz..0.45 fs)")):
        f, p = welch(sig, afs, nperseg=min(1024, len(sig)), axis=0)
        axes[1].semilogx(f[1:], 10 * np.log10(p[1:].sum(1) + 1e-20), label=name)
    axes[1].set(title=f"Phone vibration spectrum ({afs:.0f} Hz)", xlabel="Hz", ylabel="dB"); axes[1].legend(); axes[1].grid(alpha=.3)
plt.tight_layout()
out = os.path.join(folder, "prefilter.png"); plt.savefig(out, dpi=150)
print("Saved", out)
