"""
QuietDrive - train the fault and severity models from recorded sessions.

  python train.py                 # uses ./data, saves models/quietdrive_model.joblib
  python train.py --data data --out models

Evaluation keeps whole recordings apart (a session is either in training or in testing,
never both), so the accuracy is honest. Needs at least 2 sessions per label for that;
with fewer it falls back to a time split inside each session and says so.
Augmentation: each training session gets extra copies mixed with venue noise
(bridge/venue_noise, any .wav/.flac/.ogg/.mp3) at SNR 0/5/10/20 dB with small gain changes and
time shifts. Testing uses the original held-out sessions, plus the same sessions with venue noise.
Trains RandomForest and HistGradientBoosting and keeps the better one.
Outputs: accuracy, confusion matrix, models/confusion_matrix.png, models/feature_importance.png
"""
import argparse
import glob
import json
import os

import joblib
import numpy as np
import soundfile as sf
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, mean_absolute_error
from sklearn.model_selection import GroupKFold

HERE = os.path.dirname(os.path.abspath(__file__))
import common
from common import AUDIO_FS, AUDIO_IDX, FEATURE_NAMES, HOP_S, I_AUDIO_RMS, VIB_IDX, WIN_S, feature_row, to_16k

I_WHINE = FEATURE_NAMES.index("whine_hz")


SNRS_DB = (0, 5, 10, 20)        # venue noise loudness levels used for augmentation
NOISE_EXT = (".wav", ".flac", ".ogg", ".mp3")


def load_session(folder):
    meta = json.load(open(os.path.join(folder, "meta.json")))
    x, fs = sf.read(os.path.join(folder, "audio.wav"))
    if x.ndim > 1:
        x = x[:, 0]
    assert fs == AUDIO_FS, f"{folder}: expected {AUDIO_FS} Hz audio"
    acc_path = os.path.join(folder, "accel.csv")
    acc = np.loadtxt(acc_path, delimiter=",", skiprows=1) if os.path.exists(acc_path) else np.zeros((0, 4))
    return x, acc.reshape(-1, 4), meta


def window_features(x, acc, fs=AUDIO_FS, offset_s=0.0):
    """Feature rows over the session; offset_s shifts the window grid (time-shift augmentation)."""
    rows = []
    w, h = int(WIN_S * fs), int(HOP_S * fs)
    for start in range(int((1.0 + offset_s) * fs), len(x) - w, h):       # skip the first second
        t0, t1 = start / fs, (start + w) / fs
        m = (acc[:, 0] >= t0) & (acc[:, 0] < t1)
        row, _ = feature_row(x[start:start + w], acc[m, 0], acc[m, 1:4])
        rows.append(row)
    return np.array(rows)


def session_features(folder):
    x, acc, meta = load_session(folder)
    return window_features(x, acc), meta


def load_venue_noise(path):
    """All audio under path (a folder or one file), mono 16 kHz, concatenated. None if missing."""
    files = [path] if os.path.isfile(path) else sorted(
        f for f in glob.glob(os.path.join(path, "**", "*"), recursive=True) if f.lower().endswith(NOISE_EXT))
    parts = []
    for f in files:
        n, fs = sf.read(f)
        if n.ndim > 1:
            n = n.mean(axis=1)
        parts.append(to_16k(n, fs))
    return np.concatenate(parts) if parts else None


def mix_noise(x, noise, snr_db, rng):
    """x + a random stretch of venue noise scaled to the requested SNR."""
    if len(noise) < len(x):
        noise = np.tile(noise, int(np.ceil(len(x) / len(noise))) + 1)
    s = rng.integers(0, len(noise) - len(x) + 1)
    n = noise[s:s + len(x)]
    px, pn = np.mean(x ** 2) + 1e-12, np.mean(n ** 2) + 1e-12
    y = x + n * np.sqrt(px / (pn * 10 ** (snr_db / 10)))
    return np.clip(y, -1.0, 1.0)


def augmented_features(x, acc, noise, rng):
    """One extra copy per SNR level, each with a small gain change and time shift."""
    out = []
    for snr in SNRS_DB:
        gain = 10 ** (rng.uniform(-3, 3) / 20)                          # +-3 dB
        xa = mix_noise(x * gain, noise, snr, rng)
        f = window_features(xa, acc, offset_s=rng.uniform(0, HOP_S))    # shift the window grid
        if len(f):
            out.append(f)
    return np.vstack(out) if out else np.zeros((0, len(FEATURE_NAMES)))


def noise_table(sessions):
    """Healthy loudness per speed, and how much louder each fault/level is than normal
    at the same speed = how much quieter the motor should get once fixed."""
    normal = [(rpm, db, sp) for lab, _, sp, rpm, db in sessions if lab == "normal"]
    normal_ref = [(rpm, db) for rpm, db, _ in normal]
    if not normal:
        print("No normal sessions: cannot compute the 'quieter after fix' table.")
        return normal_ref, {}

    def healthy_db(speed, rpm):
        same = [db for _, db, sp in normal if sp == speed]
        if same:
            return float(np.mean(same))
        return min(normal, key=lambda n: abs(n[0] - rpm))[1]    # nearest speed
    extra = {}
    for lab, lvl, sp, rpm, db in sessions:
        if lab != "normal":
            extra.setdefault(lab, {}).setdefault(lvl, []).append(db - healthy_db(sp, rpm))
    fix_db = {lab: {lvl: round(float(np.mean(v)), 1) for lvl, v in d.items()} for lab, d in extra.items()}
    print("\nExtra noise vs healthy at the same speed (dB) = expected reduction after the fix:")
    for lab, d in sorted(fix_db.items()):
        print(f"  {lab:12s} " + "  ".join(f"L{l}: {v:+.1f}" for l, v in sorted(d.items())))
    return normal_ref, fix_db


def make_clf(kind, n=300):
    if kind == "hgb":
        return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.1, class_weight="balanced", random_state=0)
    return RandomForestClassifier(n_estimators=n, class_weight="balanced", random_state=0, n_jobs=-1)


MODEL_NAMES = {"rf": "RandomForest", "hgb": "HistGradientBoosting"}


def scores(true, pred, grp):
    """Window accuracy, fault-vs-normal accuracy, per-recording majority-vote accuracy."""
    true, pred, grp = np.asarray(true), np.asarray(pred), np.asarray(grp)
    acc = accuracy_score(true, pred)
    binary = accuracy_score(true != "normal", pred != "normal")
    votes = []
    for g in np.unique(grp):
        m = grp == g
        vals, counts = np.unique(pred[m], return_counts=True)
        votes.append(vals[np.argmax(counts)] == true[m][0])
    return acc, binary, float(np.mean(votes))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data"))
    ap.add_argument("--out", default=os.path.join(HERE, "models"))
    ap.add_argument("--noise", default=None, help="venue noise folder or file (default bridge/venue_noise)")
    ap.add_argument("--test-snr", type=float, default=5.0, help="SNR (dB) of venue noise mixed into the noisy test")
    a = ap.parse_args()
    rng = np.random.default_rng(0)

    cal = common.calibrate_whine_order(a.data)
    if cal is None:
        print(f"No imbalance level-3 recordings with phone data: keeping WHINE_ORDER = {common.WHINE_ORDER}")
    else:
        print(f"Calibration ({cal['source']}): whine {cal['whine_hz']:.0f} Hz, shaft {cal['shaft_hz']:.1f} Hz "
              f"(~{cal['rpm']} rpm) -> ratio {cal['ratio']:.2f}")
        print(f"  per recording: " + ", ".join(f"{k} {v:.2f}" for k, v in cal["per_session_ratio"].items()))
        if abs(cal["ratio"] - round(cal["ratio"])) > 0.3:
            print("  WARNING: ratio is far from a whole number - check the recordings")
    print(f"WHINE_ORDER = {common.WHINE_ORDER}")

    noise_path = a.noise or os.path.join(HERE, "venue_noise")
    noise = load_venue_noise(noise_path) if os.path.exists(noise_path) else None
    if noise is None:
        print(f"No venue noise audio at {noise_path}: training without augmentation, no noisy test.")
    else:
        print(f"Venue noise: {len(noise) / AUDIO_FS:.1f} s from {noise_path}")
    has_aug = noise is not None

    folders = sorted(f for f in glob.glob(os.path.join(a.data, "*"))
                     if os.path.exists(os.path.join(f, "meta.json"))
                     and os.path.abspath(f) != os.path.abspath(noise_path))
    if not folders:
        raise SystemExit(f"No sessions found in {a.data}. Record some with record.py first.")
    X, y, lvl, groups, tpos = [], [], [], [], []
    XA, yA, lvlA, gA = [], [], [], []       # augmented training copies (venue noise, gain, shift)
    XN = []                                 # noisy copy of every session, same windows, for the noisy test
    sessions = []   # (label, level, speed, mean rpm, mean audio dB) for the noise table
    for gi, f in enumerate(folders):
        x, acc_raw, meta = load_session(f)
        feats = window_features(x, acc_raw)
        if len(feats) == 0:
            print("  skipped (too short):", f); continue
        X.append(feats); y += [meta["label"]] * len(feats); lvl += [meta["level"]] * len(feats)
        groups += [gi] * len(feats); tpos += list(np.linspace(0, 1, len(feats)))
        sessions.append((meta["label"], int(meta["level"]), meta.get("speed"),
                         float(np.median(feats[:, I_WHINE])) / common.WHINE_ORDER * 60, float(np.mean(feats[:, I_AUDIO_RMS]))))
        n_aug = 0
        if has_aug:
            fa = augmented_features(x, acc_raw, noise, rng)
            XA.append(fa); yA += [meta["label"]] * len(fa); lvlA += [meta["level"]] * len(fa); gA += [gi] * len(fa)
            XN.append(window_features(mix_noise(x, noise, a.test_snr, rng), acc_raw))
            n_aug = len(fa)
        print(f"  {os.path.basename(f):40s} {len(feats):4d} rows  (+{n_aug} augmented)")
    X = np.vstack(X); y = np.array(y); lvl = np.array(lvl, float); groups = np.array(groups); tpos = np.array(tpos)
    if has_aug:
        XA = np.vstack(XA); yA = np.array(yA); lvlA = np.array(lvlA, float); gA = np.array(gA)
        XN = np.vstack(XN)
        assert len(XN) == len(X), "noisy test rows must line up with the clean rows"
    labels = sorted(set(y.tolist()))
    print(f"\n{len(X)} rows (+{len(XA) if has_aug else 0} augmented), {len(folders)} sessions, labels: {labels}")

    sessions_per_label = {l: len(set(groups[y == l])) for l in labels}
    time_split = min(sessions_per_label.values()) < 2
    if not time_split:
        k = min(5, min(sessions_per_label.values()))
        splits = list(GroupKFold(n_splits=k).split(X, y, groups))
        how = f"{k}-fold, whole sessions held out"
    else:
        test = tpos >= 0.7
        splits = [(np.where(~test)[0], np.where(test)[0])]
        how = "time split (last 30% of each session) - record a 2nd session per label for a fairer test"
    print("Evaluation:", how)

    def train_set(tr, final=False):
        """Clean training rows + augmented copies of the training sessions only.
        (With a time split the same sessions are on both sides, so augmented copies
        would leak test audio - they are only used for the final model then.)"""
        if not has_aug or (time_split and not final):
            return X[tr], y[tr], lvl[tr]
        m = np.isin(gA, np.unique(groups[tr]))
        return np.vstack([X[tr], XA[m]]), np.concatenate([y[tr], yA[m]]), np.concatenate([lvl[tr], lvlA[m]])

    results = {}
    for kind in ("rf", "hgb"):
        pred, predn, true, grp, lp, lt = [], [], [], [], [], []
        for tr, te in splits:
            Xt, yt, lvt = train_set(tr)
            clf = make_clf(kind).fit(Xt, yt)
            pred += list(clf.predict(X[te])); true += list(y[te]); grp += list(groups[te])
            if has_aug:
                predn += list(clf.predict(XN[te]))
            if kind == "rf":
                reg = RandomForestRegressor(n_estimators=200, random_state=0, n_jobs=-1).fit(Xt, lvt)
                lp += list(reg.predict(X[te])); lt += list(lvl[te])
        r = dict(clean=scores(true, pred, grp), true=true, pred=pred)
        if has_aug:
            r["noisy"] = scores(true, predn, grp)
        if lp:
            r["sev_mae"] = mean_absolute_error(lt, lp)
        results[kind] = r
        print(f"\n== {MODEL_NAMES[kind]} ==")
        for which in ("clean", "noisy"):
            if which in r:
                acc_, bin_, vote_ = r[which]
                tag = "original test audio" if which == "clean" else f"test audio + venue noise @ {a.test_snr:g} dB"
                print(f"  {tag:36s} fault acc {acc_*100:5.1f}%   fault-vs-normal {bin_*100:5.1f}%   "
                      f"per-recording vote {vote_*100:5.1f}%")

    def score_of(k):          # pick on the mean of clean and noisy window accuracy
        r = results[k]
        return np.mean([r["clean"][0]] + ([r["noisy"][0]] if "noisy" in r else []))
    best = max(results, key=score_of)
    true, pred = results[best]["true"], results[best]["pred"]
    acc = results[best]["clean"][0]
    acc_noisy = results[best]["noisy"][0] if has_aug else None
    cm = confusion_matrix(true, pred, labels=labels)
    print(f"\nKeeping {MODEL_NAMES[best]}: fault accuracy {acc*100:.1f}% on original test audio"
          + (f", {acc_noisy*100:.1f}% with venue noise" if has_aug else ""))
    print(classification_report(true, pred, labels=labels, zero_division=0))
    print("Confusion matrix (rows = true, cols = predicted):", labels)
    print(cm)
    print(f"Severity error: {results['rf']['sev_mae']:.2f} levels (0 = normal ... 3 = large)")

    normal_ref, fix_db = noise_table(sessions)

    # final models on all data (clean + augmented)
    Xall, yall, lvlall = train_set(np.arange(len(X)), final=True)
    clf = make_clf(best).fit(Xall, yall)
    reg = RandomForestRegressor(n_estimators=200, random_state=0, n_jobs=-1).fit(Xall, lvlall)
    clf_audio = make_clf(best, 200).fit(Xall[:, AUDIO_IDX], yall)
    clf_vib = make_clf(best, 200).fit(Xall[:, VIB_IDX], yall)
    os.makedirs(a.out, exist_ok=True)
    joblib.dump(dict(clf=clf, reg=reg, clf_audio=clf_audio, clf_vib=clf_vib, features=FEATURE_NAMES,
                     labels=labels, whine_order=common.WHINE_ORDER, accuracy=acc, accuracy_noisy=acc_noisy, model_type=MODEL_NAMES[best],
                     evaluation=how,
                     baseline=X[y == "normal"].mean(axis=0) if "normal" in labels else None,
                     normal_ref=normal_ref, fix_db=fix_db),
                os.path.join(a.out, "quietdrive_model.joblib"))

    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5.5, 4.5))
    ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(labels)), labels, rotation=30); ax.set_yticks(range(len(labels)), labels)
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, cm[i, j], ha="center", va="center", color="white" if cm[i, j] > cm.max() / 2 else "black")
    ax.set_xlabel("predicted"); ax.set_ylabel("true"); ax.set_title(f"Fault detection - {acc*100:.1f}% accuracy")
    plt.tight_layout(); plt.savefig(os.path.join(a.out, "confusion_matrix.png"), dpi=150); plt.close()
    if hasattr(clf, "feature_importances_"):
        imp = clf.feature_importances_
    else:                                                    # HistGradientBoosting: permutation importance
        from sklearn.inspection import permutation_importance
        sub = np.random.default_rng(0).choice(len(X), min(len(X), 1500), replace=False)
        imp = permutation_importance(clf, X[sub], y[sub], n_repeats=3, random_state=0, n_jobs=-1).importances_mean
    top = np.argsort(imp)[::-1][:15]
    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.barh([FEATURE_NAMES[i] for i in top][::-1], imp[top][::-1], color="#1D5FE0")
    ax.set_title("What the model listens to (top 15 features)"); plt.tight_layout()
    plt.savefig(os.path.join(a.out, "feature_importance.png"), dpi=150); plt.close()
    print(f"\nSaved {a.out}/quietdrive_model.joblib, confusion_matrix.png, feature_importance.png")


if __name__ == "__main__":
    main()
