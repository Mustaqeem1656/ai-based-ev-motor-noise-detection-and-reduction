from fastapi import APIRouter

from app.ingest.simulator import simulator
from app.schemas import FaultInjection

router = APIRouter(prefix="/api", tags=["demo"])


@router.post("/sim/fault")
async def inject_fault(payload: FaultInjection):
    await simulator.set_fault(payload.vid, payload.fault, payload.severity, payload.ramp_minutes)
    return {"ok": True, "vid": payload.vid, "fault": payload.fault, "severity": payload.severity}


@router.post("/sim/reset")
async def reset_simulation():
    simulator.reset()
    return {"ok": True}


@router.get("/health")
def backend_health():
    return {"status": "ok", "source": "sim"}
