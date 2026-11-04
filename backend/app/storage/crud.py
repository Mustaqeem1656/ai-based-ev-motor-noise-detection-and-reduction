from typing import Optional

from sqlalchemy.orm import Session

from app.models import AlertRecord, Vehicle, TelemetryRecord, InferenceRecord


def get_vehicle(db: Session, vid: str) -> Optional[Vehicle]:
    return db.query(Vehicle).filter(Vehicle.vid == vid).first()


def list_vehicles(db: Session):
    return db.query(Vehicle).all()


def create_vehicle_if_missing(db: Session, vid: str, name: Optional[str] = None) -> Vehicle:
    vehicle = get_vehicle(db, vid)
    if vehicle is None:
        vehicle = Vehicle(vid=vid, name=name or vid, baseline_health=85.0)
        db.add(vehicle)
        db.commit()
        db.refresh(vehicle)
    return vehicle


def add_telemetry(db: Session, payload: dict) -> None:
    db.add(TelemetryRecord(
        vid=payload.get("vid"),
        ts=payload.get("ts"),
        rpm=payload.get("rpm"),
        current_a=payload.get("current_a"),
        temp_c=payload.get("temp_c"),
        vib_rms_g=payload.get("vib_rms_g"),
        spl_ref_db=payload.get("spl_ref_db"),
        spl_err_db=payload.get("spl_err_db"),
        pwm_hz=payload.get("pwm_hz"),
        pwm_mode=payload.get("pwm_mode"),
        anc_on=payload.get("anc_on"),
        vib_cancel_on=payload.get("vib_cancel_on"),
        vib_cancel_effort=payload.get("vib_cancel_effort"),
    ))
    db.commit()


def add_inference(db: Session, payload: dict) -> None:
    db.add(InferenceRecord(
        vid=payload.get("vid"),
        ts=payload.get("ts"),
        fault=payload.get("fault"),
        severity_raw=payload.get("severity_raw"),
        confidence_audio=payload.get("conf", {}).get("audio"),
        confidence_vib=payload.get("conf", {}).get("vib"),
        confidence_current=payload.get("conf", {}).get("current"),
        protected_hz=str(payload.get("protected_hz", [])),
    ))
    db.commit()


def add_alert(db: Session, vid: str, severity: str, title: str, message: str) -> AlertRecord:
    alert = AlertRecord(vid=vid, severity=severity, title=title, message=message, created_at=int(__import__('time').time() * 1000))
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def list_alerts(db: Session):
    return db.query(AlertRecord).order_by(AlertRecord.created_at.desc()).all()
