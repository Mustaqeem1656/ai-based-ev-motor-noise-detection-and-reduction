from sqlalchemy import Boolean, Column, Float, Integer, String, Text

from app.database import Base


class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, index=True)
    vid = Column(String, unique=True, index=True)
    name = Column(String, nullable=True)
    baseline_health = Column(Float, default=85.0)
    status = Column(String, default="healthy")
    is_live = Column(Boolean, default=True)


class TelemetryRecord(Base):
    __tablename__ = "telemetry_records"

    id = Column(Integer, primary_key=True, index=True)
    vid = Column(String, index=True)
    ts = Column(Integer, index=True)
    rpm = Column(Float, nullable=True)
    current_a = Column(Float, nullable=True)
    temp_c = Column(Float, nullable=True)
    vib_rms_g = Column(Float, nullable=True)
    spl_ref_db = Column(Float, nullable=True)
    spl_err_db = Column(Float, nullable=True)
    pwm_hz = Column(Integer, nullable=True)
    pwm_mode = Column(String, nullable=True)
    anc_on = Column(Boolean, default=False)
    vib_cancel_on = Column(Boolean, default=False)
    vib_cancel_effort = Column(Float, default=0.0)


class InferenceRecord(Base):
    __tablename__ = "inference_records"

    id = Column(Integer, primary_key=True, index=True)
    vid = Column(String, index=True)
    ts = Column(Integer, index=True)
    fault = Column(String)
    severity_raw = Column(Float)
    confidence_audio = Column(Float)
    confidence_vib = Column(Float)
    confidence_current = Column(Float)
    protected_hz = Column(Text, nullable=True)


class AlertRecord(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    vid = Column(String, index=True)
    severity = Column(String, default="mild")
    title = Column(String)
    message = Column(Text)
    acknowledged = Column(Boolean, default=False)
    created_at = Column(Integer)


class ServiceEvent(Base):
    __tablename__ = "service_events"

    id = Column(Integer, primary_key=True, index=True)
    vid = Column(String, index=True)
    service_type = Column(String)
    note = Column(Text)
    ts = Column(Integer)


class RecordingSession(Base):
    __tablename__ = "recording_sessions"

    id = Column(Integer, primary_key=True, index=True)
    vid = Column(String, index=True)
    label = Column(String)
    fault_type = Column(String)
    severity_label = Column(String)
    file_path = Column(String)
    started_at = Column(Integer)
    ended_at = Column(Integer, nullable=True)
