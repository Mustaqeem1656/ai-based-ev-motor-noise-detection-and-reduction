"""Owner advice for the bridge: the SAME code the backend uses (backend/app/analytics/advice.py),
so ESP32 and bridge give identical points/tiers/advice."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from app.analytics.advice import assess, compute_points, tier_for_points  # noqa: E402,F401
