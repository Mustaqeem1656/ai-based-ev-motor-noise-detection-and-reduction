from app.analytics.health import health_message_for_score, level_for_score, score_from_inference, time_to_severe


def test_score_from_inference_is_clamped():
    score = score_from_inference({"audio": 0.92, "vib": 0.9, "current": 0.88}, vib_cancel_effort=0.4)
    assert 0 <= score <= 100


def test_level_mapping_is_correct():
    assert level_for_score(90) == "Healthy"
    assert level_for_score(65) == "Mild"
    assert level_for_score(40) == "Moderate"
    assert level_for_score(10) == "Severe"


def test_health_message_mapping():
    assert "All good" in health_message_for_score(90)
    assert "Service" in health_message_for_score(10)


def test_time_to_severe_returns_estimate_for_declining_trend():
    estimate, ok = time_to_severe([88, 72, 61, 44, 29, 17])
    assert ok is True
    assert "h" in estimate
