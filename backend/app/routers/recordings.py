import csv
import json
import time
from pathlib import Path

from fastapi import APIRouter

from app.schemas import RecordingCreate

router = APIRouter(prefix="/api", tags=["recordings"])

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "recordings"
DATA_DIR.mkdir(parents=True, exist_ok=True)

FEATURE_HEADER = [
    "ts",
    "vid",
    "rpm",
    "audio_bands",
    "vib_bands",
    "vib_1x",
    "vib_2x",
    "i_mean",
    "i_ripple",
    "temp_c",
]


def _feature_csv_path(vid: str, ts: int, fault_type: str) -> Path:
    return DATA_DIR / f"{vid}_{ts}_{fault_type}.csv"


@router.post("/recordings/start")
def start_recording(payload: RecordingCreate):
    ts = int(time.time() * 1000)
    file_name = f"{payload.vid}_{ts}_{payload.fault_type}.csv"
    file_path = DATA_DIR / file_name
    file_path.write_text(
        ",".join(FEATURE_HEADER) + "\n",
        encoding="utf-8",
    )
    return {"ok": True, "path": str(file_path), "label": payload.fault_type, "severity_label": payload.severity_label}


@router.post("/recordings/stop")
def stop_recording():
    return {"ok": True, "message": "Recording session stopped"}


@router.get("/recordings")
def list_recordings():
    files = sorted(DATA_DIR.glob("*.csv"))
    return {"recordings": [f.name for f in files]}


def append_feature_row(file_path: Path, payload: dict) -> None:
    if not file_path.exists():
        return
    audio_bands = payload.get("audio_bands", [])
    vib_bands = payload.get("vib_bands", [])
    row = {
        "ts": payload.get("ts", int(time.time() * 1000)),
        "vid": payload.get("vid", "QD-01"),
        "rpm": payload.get("rpm", 0.0),
        "audio_bands": json.dumps(audio_bands),
        "vib_bands": json.dumps(vib_bands),
        "vib_1x": payload.get("vib_1x", 0.0),
        "vib_2x": payload.get("vib_2x", 0.0),
        "i_mean": payload.get("i_mean", 0.0),
        "i_ripple": payload.get("i_ripple", 0.0),
        "temp_c": payload.get("temp_c", 0.0),
    }
    with file_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FEATURE_HEADER)
        writer.writerow(row)
