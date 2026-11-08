from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class TelemetryIn(BaseModel):
    t: str = "tel"
    ts: int
    vid: str
    rpm: float
    current_a: float
    temp_c: float
    vib_rms_g: float
    vib_xyz: List[float]
    spl_ref_db: float
    spl_err_db: float
    pwm_hz: int
    pwm_mode: str
    anc_on: bool
    vib_cancel_on: bool
    vib_cancel_effort: float


class SpectrumIn(BaseModel):
    t: str = "spec"
    ts: int
    vid: str
    mic: str
    fmax: float
    bins: List[float] = Field(..., min_length=128, max_length=128)


class InferenceIn(BaseModel):
    t: str = "inf"
    ts: int
    vid: str
    fault: str
    conf: Dict[str, float]
    severity_raw: float
    protected_hz: List[float] = []
    # on-board ANC status (bridge/live.py --esp32), passed through unchanged
    anc_connected: Optional[bool] = None
    anc_on: Optional[bool] = None
    anc_freq: Optional[float] = None
    anc_level_db: Optional[float] = None
    anc_reduction_db: Optional[float] = None
    anc_out: Optional[float] = None
    anc_at_limit: Optional[bool] = None
    anc_tracking: Optional[bool] = None
    anc_msg: Optional[str] = None


class VehicleCommand(BaseModel):
    anc: Optional[bool] = None
    pwm_mode: Optional[str] = None
    vib_cancel: Optional[bool] = None
    motor_duty: Optional[int] = None


class FaultInjection(BaseModel):
    vid: str
    fault: str = "imbalance"
    severity: float = 0.5
    ramp_minutes: float = 0.0


class RecordingCreate(BaseModel):
    vid: str
    fault_type: str = "normal"
    severity_label: str = "Healthy"


class BaselineRequest(BaseModel):
    vid: str
    baseline_health: float
