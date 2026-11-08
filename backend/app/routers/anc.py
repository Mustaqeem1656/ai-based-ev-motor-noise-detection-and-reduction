"""Noise reduction page: run bridge/anc_recorded.py (FxLMS) on a recorded session.
Real recorded motor noise, simulated speaker path."""
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/anc", tags=["anc"])

BRIDGE = Path(__file__).resolve().parents[3] / "bridge"
SOURCES = {"data": BRIDGE / "data", "data_fake": BRIDGE / "data_fake"}   # real first, then synthetic
FILES = {"anc_before.wav": "audio/wav", "anc_after.wav": "audio/wav", "anc_result.png": "image/png"}
NAME = re.compile(r"^[A-Za-z0-9_\-]+$")
_lock = asyncio.Lock()      # one ANC run at a time (CPU heavy)


def _session_dir(source: str, session: str) -> Path:
    if source not in SOURCES or not NAME.match(session):
        raise HTTPException(404, "unknown session")
    folder = SOURCES[source] / session
    if not (folder / "audio.wav").exists():
        raise HTTPException(404, "unknown session")
    return folder


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@router.get("/sessions")
def list_sessions():
    out = []
    for source, root in SOURCES.items():
        if not root.exists():
            continue
        for folder in sorted(root.iterdir()):
            if not (folder / "audio.wav").exists():
                continue
            meta = _read_json(folder / "meta.json") or {}
            out.append({
                "source": source, "session": folder.name, "synthetic": source == "data_fake",
                "label": meta.get("label"), "level": meta.get("level"), "speed": meta.get("speed"),
                "seconds": meta.get("seconds"), "result": _read_json(folder / "anc_result.json"),
            })
    return {"sessions": out}


@router.post("/run/{source}/{session}")
async def run_anc(source: str, session: str, seconds: float = 10.0):
    folder = _session_dir(source, session)
    seconds = max(3.0, min(seconds, 30.0))
    cmd = [sys.executable, str(BRIDGE / "anc_recorded.py"), str(folder), "--seconds", str(seconds)]
    async with _lock:
        proc = await asyncio.to_thread(subprocess.run, cmd, cwd=str(BRIDGE), capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise HTTPException(500, f"ANC failed: {proc.stderr.strip()[-500:]}")
    return {"ok": True, "log": proc.stdout, "result": _read_json(folder / "anc_result.json")}


@router.get("/file/{source}/{session}/{name}")
def get_file(source: str, session: str, name: str):
    if name not in FILES:
        raise HTTPException(404, "unknown file")
    path = _session_dir(source, session) / name
    if not path.exists():
        raise HTTPException(404, "run ANC first")
    return FileResponse(path, media_type=FILES[name], headers={"Cache-Control": "no-store"})
