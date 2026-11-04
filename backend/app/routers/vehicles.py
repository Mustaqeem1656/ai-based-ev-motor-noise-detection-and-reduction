from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Vehicle
from app.schemas import BaselineRequest, VehicleCommand
from app.storage.crud import create_vehicle_if_missing, get_vehicle, list_vehicles

router = APIRouter(prefix="/api", tags=["vehicles"])


@router.get("/vehicles")
def get_vehicles(db: Session = Depends(get_db)):
    return {"vehicles": [
        {"vid": v.vid, "name": v.name, "baseline_health": v.baseline_health, "status": v.status}
        for v in list_vehicles(db)
    ]}


@router.get("/vehicles/{vid}")
def get_vehicle_detail(vid: str, db: Session = Depends(get_db)):
    vehicle = get_vehicle(db, vid)
    if not vehicle:
        vehicle = create_vehicle_if_missing(db, vid)
    return {"vid": vehicle.vid, "name": vehicle.name, "baseline_health": vehicle.baseline_health, "status": vehicle.status}


@router.post("/vehicles/{vid}/command")
def send_vehicle_command(vid: str, payload: VehicleCommand, db: Session = Depends(get_db)):
    vehicle = get_vehicle(db, vid)
    if not vehicle:
        vehicle = create_vehicle_if_missing(db, vid)
    return {"ok": True, "vid": vid, "command": payload.model_dump(exclude_none=True), "vehicle_id": vehicle.id}


@router.post("/vehicles/{vid}/baseline")
def set_vehicle_baseline(vid: str, payload: BaselineRequest, db: Session = Depends(get_db)):
    vehicle = get_vehicle(db, vid)
    if not vehicle:
        vehicle = create_vehicle_if_missing(db, vid)
    vehicle.baseline_health = payload.baseline_health
    db.commit()
    return {"ok": True, "vid": vid, "baseline_health": payload.baseline_health}
