"""
Problem points and owner advice. Pure Python, no dependencies: used by the backend for
every device (ESP32 or bridge) and imported by bridge/live.py, so both give identical results.

points 0-100 (higher = worse):
  severity level 0-3 -> 0-75 points
  + 1.5 points per dB louder than the healthy baseline (max 15)
  x 0.7 if sound and vibration do not both have confidence >= 0.5
  any confirmed fault is at least 21 (never shown as HEALTHY)
health = 100 - points
"""
from typing import Dict, Optional

TIERS = [  # (max points, tier, action, headline)
    (20, "HEALTHY", "ok", "All good. No action needed."),
    (45, "MILD", "diy", "Minor issue. You can tend to it yourself:"),
    (75, "MODERATE", "service_soon", "Get it checked at a service centre within a week."),
    (100, "SEVERE", "service_now", "Get it checked now. Avoid long drives."),
]

DIY_TIPS = {
    "imbalance": "check the shaft and wheel for stuck debris, mud or loose parts.",
    "loose_mount": "tighten the motor mounting bolts and check the rubber mounts for cracks.",
    "bearing": "turn the shaft by hand; if it grinds or feels rough, the bearing needs a mechanic.",
}

REASONS = {
    "imbalance": "Shaking once per motor turn (1x shaft speed), typical of an imbalance",
    "loose_mount": "Rattle at twice the motor speed (2x), typical of a loose mount",
    "bearing": "High-pitched grinding noise, typical of a worn bearing",
}


def _ok(v: Optional[float]) -> bool:
    return v is not None and v >= 0.5


def compute_points(fault: str, level: float, conf: Dict[str, Optional[float]], db_over: float) -> float:
    level = max(0.0, min(3.0, level or 0.0)) if fault != "normal" else 0.0
    pts = level / 3 * 75 + min(15.0, max(0.0, 1.5 * (db_over or 0.0)))
    if not (_ok(conf.get("audio")) and _ok(conf.get("vib"))):
        pts *= 0.7
    if fault != "normal":
        pts = max(21.0, pts)
    return max(0.0, min(100.0, pts))


def tier_for_points(points: float):
    for limit, tier, action, headline in TIERS:
        if points <= limit:
            return tier, action, headline
    return TIERS[-1][1:]


def assess(fault: str, level: float, conf: Dict[str, Optional[float]], db_over: float,
           fix_db: Optional[float] = None, fix_vib_pct: Optional[float] = None) -> dict:
    """All advice fields for an inf message.
    fix_db: expected dB quieter after the fix (only sent when >= 0.5 dB).
    fix_vib_pct: expected % less vibration after the fix (sent when >= 5 %)."""
    conf = conf or {}
    points = round(compute_points(fault, level, conf, db_over))
    tier, action, headline = tier_for_points(points)
    tip = DIY_TIPS.get(fault)
    if action == "diy" and tip:
        advice = f"{headline} {tip[0].upper()}{tip[1:]}"
    elif action == "service_soon" and tip:
        advice = f"{headline} Quick check: {tip}"
    else:
        advice = headline
    if fault == "normal":
        reason = "Sound and vibration match the healthy motor"
    else:
        reason = REASONS.get(fault, "The motor is outside its healthy pattern")
    if db_over is not None and db_over >= 1.0:
        reason += f"; {db_over:.0f} dB louder than when healthy"
    reason += "."
    out = dict(points=points, health=100 - points, tier=tier, action=action, advice=advice, reason=reason,
               sensors_agree=_ok(conf.get("audio")) and _ok(conf.get("vib")))
    if fix_db is not None and fault != "normal" and fix_db >= 0.5:
        out["fix_db"] = round(fix_db, 1)
    if fix_vib_pct is not None and fault != "normal" and fix_vib_pct >= 5:
        out["fix_vib_pct"] = round(min(fix_vib_pct, 95.0))
    return out
