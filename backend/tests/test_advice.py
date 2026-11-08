from app.analytics.advice import assess, compute_points
from app.ingest.processing import process_frame

BOTH = {"audio": 0.9, "vib": 0.8}


def test_healthy_is_zero_points():
    a = assess("normal", 0, BOTH, 0)
    assert a["points"] == 0 and a["health"] == 100 and a["tier"] == "HEALTHY" and a["action"] == "ok"


def test_points_formula():
    assert compute_points("imbalance", 3, BOTH, 0) == 75
    assert compute_points("imbalance", 3, BOTH, 20) == 90          # loudness capped at 15
    assert compute_points("imbalance", 2, {"audio": 0.9, "vib": 0.3}, 0) == 50 * 0.7
    assert compute_points("bearing", 0.1, BOTH, 0) == 21            # confirmed fault floor


def test_tiers_and_advice():
    a = assess("loose_mount", 1, BOTH, 2)                           # 25 + 3 = 28
    assert a["tier"] == "MILD" and a["action"] == "diy" and "bolts" in a["advice"]
    a = assess("imbalance", 2, BOTH, 4)                             # 50 + 6 = 56
    assert a["tier"] == "MODERATE" and a["action"] == "service_soon" and "4 dB louder" in a["reason"]
    a = assess("bearing", 3, BOTH, 10, fix_db=6.3)
    assert a["tier"] == "SEVERE" and a["action"] == "service_now" and a["fix_db"] == 6.3


def test_backend_enriches_esp32_inf_but_keeps_bridge_fields():
    tel = {"t": "tel", "vid": "T1", "spl_ref_db": 70.0}
    process_frame(tel)
    out = process_frame({"t": "inf", "vid": "T1", "fault": "normal", "conf": BOTH, "severity_raw": 0})
    assert out["tier"] == "HEALTHY"
    process_frame({**tel, "spl_ref_db": 76.0})
    out = process_frame({"t": "inf", "vid": "T1", "fault": "imbalance", "conf": BOTH, "severity_raw": 0.5})
    assert out["points"] == round(37.5 + 9) and "6 dB louder" in out["reason"]
    keep = process_frame({"t": "inf", "vid": "T1", "fault": "normal", "points": 3})
    assert keep["points"] == 3 and "tier" not in keep
