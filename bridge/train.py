"""
QuietDrive - train the fault and severity models from recorded sessions.

  python train.py                 # uses ./data, saves models/quietdrive_model.joblib
  python train.py --data data --out models

Evaluation keeps whole recordings apart (a session is either in training or in testing,
never both), so the accuracy is honest. Needs at least 2 sessions per label for that;
with fewer it falls back to a time split inside each session and says so.
Outputs: accuracy, confusion matrix, models/confusion_matrix.png, models/feature_importance.png
"""
import argparse
import glob
import json
import os

import joblib
import numpy as np
import soundfile as sf
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, mean_absolute_error
from sklearn.model_selection import GroupKFold

HERE = os.path.dirname(os.path.abspath(__file__))
from common import AUDIO_FS, AUDIO_IDX, FEATURE_NAMES, HOP_S, VIB_IDX, WIN_S, feature_row


def session_features(folder):
    meta = json.load(open(os.path.join(folder, "meta.json")))
    x, fs = sf.read(os.path.join(folder, "audio.wav"))
    if x.ndim > 1:
        x = x[:, 0]
    assert fs == AUDIO_FS, f"{folder}: expected {AUDIO_FS} Hz audio"
    acc_path = os.path.join(folder, "accel.csv")
    acc = np.loadtxt(acc_path, delimiter=",", skiprows=1) if os.path.exists(acc_path) else np.zeros((0, 4))
    acc = acc.reshape(-1, 4)
    rows = []
    w, h = int(WIN_S * fs), int(HOP_S * fs)
    for start in range(int(1.0 * fs), len(x) - w, h):       # skip the first second
        t0, t1 = start / fs, (start + w) / fs
        m = (acc[:, 0] >= t0) & (acc[:, 0] < t1)
        row, _ = feature_row(x[start:start + w], acc[m, 0], acc[m, 1:4])
        rows.append(row)
    return np.array(rows), meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "data"))
    ap.add_argument("--out", default=os.path.join(HERE, "models"))
    a = ap.parse_args()

    folders = sorted(f for f in glob.glob(os.path.join(a.data, "*")) if os.path.exists(os.path.join(f, "meta.json")))
    if not folders:
        raise SystemExit(f"No sessions found in {a.data}. Record some with record.py first.")
    X, y, lvl, groups, tpos = [], [], [], [], []
    for gi, f in enumerate(folders):
        feats, meta = session_features(f)
        if len(feats) == 0:
            print("  skipped (too short):", f); continue
        X.append(feats); y += [meta["label"]] * len(feats); lvl += [meta["level"]] * len(feats)
        groups += [gi] * len(feats); tpos += list(np.linspace(0, 1, len(feats)))
        print(f"  {os.path.basename(f):40s} {len(feats):4d} rows")
    X = np.vstack(X); y = np.array(y); lvl = np.array(lvl, float); groups = np.array(groups); tpos = np.array(tpos)
    labels = sorted(set(y.tolist()))
    print(f"\n{len(X)} rows, {len(folders)} sessions, labels: {labels}")

    sessions_per_label = {l: len(set(groups[y == l])) for l in labels}
    if min(sessions_per_label.values()) >= 2:
        k = min(5, min(sessions_per_label.values()))
        splits = list(GroupKFold(n_splits=k).split(X, y, groups))
        how = f"{k}-fold, whole sessions held out"
    else:
        test = tpos >= 0.7
        splits = [(np.where(~test)[0], np.where(test)[0])]
        how = "time split (last 30% of each session) - record a 2nd session per label for a fairer test"
    print("Evaluation:", how)

    pred, true, lp, lt = [], [], [], []
    for tr, te in splits:
        clf = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=0, n_jobs=-1).fit(X[tr], y[tr])
        reg = RandomForestRegressor(n_estimators=200, random_state=0, n_jobs=-1).fit(X[tr], lvl[tr])
        pred += list(clf.predict(X[te])); true += list(y[te]); lp += list(reg.predict(X[te])); lt += list(lvl[te])
    acc = accuracy_score(true, pred)
    cm = confusion_matrix(true, pred, labels=labels)
    print(f"\nFault accuracy: {acc*100:.1f}%")
    print(classification_report(true, pred, labels=labels, zero_division=0))
    print("Confusion matrix (rows = true, cols = predicted):", labels)
    print(cm)
    print(f"Severity error: {mean_absolute_error(lt, lp):.2f} levels (0 = normal ... 3 = large)")

    # final models on all data
    clf = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=0, n_jobs=-1).fit(X, y)
    reg = RandomForestRegressor(n_estimators=200, random_state=0, n_jobs=-1).fit(X, lvl)
    clf_audio = RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=0, n_jobs=-1).fit(X[:, AUDIO_IDX], y)
    clf_vib = RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=0, n_jobs=-1).fit(X[:, VIB_IDX], y)
    os.makedirs(a.out, exist_ok=True)
    joblib.dump(dict(clf=clf, reg=reg, clf_audio=clf_audio, clf_vib=clf_vib, features=FEATURE_NAMES,
                     labels=labels, accuracy=acc, evaluation=how,
                     baseline=X[y == "normal"].mean(axis=0) if "normal" in labels else None),
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
    imp = clf.feature_importances_; top = np.argsort(imp)[::-1][:15]
    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.barh([FEATURE_NAMES[i] for i in top][::-1], imp[top][::-1], color="#1D5FE0")
    ax.set_title("What the model listens to (top 15 features)"); plt.tight_layout()
    plt.savefig(os.path.join(a.out, "feature_importance.png"), dpi=150); plt.close()
    print(f"\nSaved {a.out}/quietdrive_model.joblib, confusion_matrix.png, feature_importance.png")


if __name__ == "__main__":
    main()
