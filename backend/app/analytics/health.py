import math
from typing import Dict, List, Tuple


def normalize_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def level_for_score(score: float) -> str:
    if score >= 80:
        return "Healthy"
    if score >= 50:
        return "Mild"
    if score >= 25:
        return "Moderate"
    return "Severe"


def health_message_for_score(score: float) -> str:
    level = level_for_score(score)
    mapping = {
        "Healthy": "All good",
        "Mild": "Check at your next service",
        "Moderate": "Service within a week",
        "Severe": "Service immediately, avoid long drives",
    }
    return mapping.get(level, "All good")


def score_from_inference(conf: Dict[str, float], vib_cancel_effort: float = 0.0) -> float:
    audio = conf.get("audio", 0.0)
    vib = conf.get("vib", 0.0)
    current = conf.get("current", 0.0)
    agreement = (audio + vib + current) / 3.0
    score = 100.0 - (agreement * 70.0)
    score -= vib_cancel_effort * 25.0
    return normalize_score(score)


def explain_fault_bucket(vid: str, fault: str, conf: Dict[str, float], vib_rms: float, rpm: float) -> str:
    if fault == "normal":
        return "System is stable. Measurements are within normal EV motor operating bands."
    if fault == "imbalance":
        return f"Vibration at 1x motor speed is {vib_rms / max(0.4, rpm / 1000.0):.1f}x above baseline - typical of imbalance."
    if fault == "loose_mount":
        return "Broadband rattle and a 2x motor-order peak indicate a loose mount or bracket resonance."
    if fault == "bearing":
        return "High-frequency vibration bands are rising with temperature, consistent with a bearing fault."
    return "The motor appears to be operating outside its healthy baseline."


def time_to_severe(history: List[float]) -> Tuple[str, bool]:
    if len(history) < 3:
        return "insufficient data", False
    if len(history) == 3:
        history = history[:]
    values = [max(0.0, min(100.0, x)) for x in history]
    trend = values[-1] - values[0]
    if trend >= -5:
        return "insufficient data", False
    slope = (values[-1] - values[0]) / max(1, len(values) - 1)
    if slope == 0:
        return "insufficient data", False
    hours = max(0.0, (values[-1] - 25.0) / abs(slope))
    if hours <= 0:
        return "0.0h", True
    return f"{hours:.1f}h", True
