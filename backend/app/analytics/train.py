import os
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import train_test_split


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MODEL_DIR = Path(__file__).resolve().parents[1] / "models"


def train_placeholder() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    X = np.random.rand(200, 8)
    y = np.array([0, 1, 2, 3] * 50)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    classifier = RandomForestClassifier(n_estimators=50, random_state=42)
    classifier.fit(X_train, y_train)
    regressor = RandomForestRegressor(n_estimators=50, random_state=42)
    regressor.fit(X_train, np.random.rand(len(X_train)) * 100)

    joblib.dump(classifier, MODEL_DIR / "fault_classifier.joblib")
    joblib.dump(regressor, MODEL_DIR / "severity_regressor.joblib")


if __name__ == "__main__":
    train_placeholder()
    print(f"Saved models to {MODEL_DIR}")
