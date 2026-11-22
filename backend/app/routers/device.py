"""Hardware edition controls: forward dashboard commands to the ESP32-S3 and record sessions.
All endpoints answer 409 when the backend is not running with SOURCE=serial."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.ingest import hardware

router = APIRouter(prefix="/api/device", tags=["device"])


class SetCommand(BaseModel):
    anc: Optional[bool] = None
    pwm_mode: Optional[str] = Field(None, pattern="^(fixed|spread|optimize)$")
    motor_duty: Optional[int] = Field(None, ge=0, le=100)
    anc_mu: Optional[float] = Field(None, gt=0, le=0.1)
    anc_vmax: Optional[float] = Field(None, ge=0, le=1)
    whine_order: Optional[float] = Field(None, ge=2, le=64)   # order 1 = protected shaft tone, never cancelled


class AncToggle(BaseModel):
    on: bool


class Calibrate(BaseModel):
    f_hz: Optional[float] = Field(None, gt=50, lt=7000)   # centre frequency; default = last running whine


class RecordStart(BaseModel):
    label: str
    level: int = 0
    speed: int = 1
    seconds: float = Field(90, ge=5, le=600)
    notes: str = ""


def _link():
    if hardware.link is None:
        raise HTTPException(409, f"no ESP32-S3 link (SOURCE={settings.source}; set SOURCE=serial in backend/.env)")
    return hardware.link


def _send(cmd: dict):
    if not _link().send(cmd):
        raise HTTPException(503, "ESP32-S3 serial port not open")
    return {"ok": True, "sent": cmd}


@router.get("/status")
def status():
    if hardware.link is None:
        return {"source": settings.source, "connected": False}
    return hardware.link.status()


@router.post("/set")
def set_device(cmd: SetCommand):
    body = cmd.model_dump(exclude_none=True)
    if not body:
        raise HTTPException(422, "nothing to set")
    return _send({"cmd": "set", **body})


@router.post("/anc")
def anc(t: AncToggle):
    return _send({"cmd": "set", "anc": t.on})


@router.post("/anc/calibrate")
def anc_calibrate(c: Calibrate = Calibrate()):
    return _send({"cmd": "anc_calibrate", **({"f_hz": c.f_hz} if c.f_hz else {})})


@router.post("/record/start")
def record_start(r: RecordStart):
    link = _link()
    try:
        st = link.recorder.start(r.label, r.level, r.speed, r.seconds, r.notes)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not link.send({"cmd": "set", "raw": True}):
        link.recorder.active = False
        raise HTTPException(503, "ESP32-S3 serial port not open")
    return st


@router.post("/record/stop")
def record_stop():
    link = _link()
    res = link.recorder.stop()
    link.send({"cmd": "set", "raw": False})
    return res


@router.get("/record")
def record_status():
    return _link().recorder.status()
