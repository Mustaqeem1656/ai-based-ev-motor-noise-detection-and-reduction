import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.analytics.health import explain_fault_bucket, level_for_score, score_from_inference
from app.ingest.event_bus import bus

router = APIRouter()


@router.websocket("/ws/live/{vid}")
async def live_stream(websocket: WebSocket, vid: str):
    await websocket.accept()
    async def forward(event):
        if event.get("vid") != vid:
            return
        try:
            await websocket.send_text(json.dumps(event))
        except Exception:
            pass

    bus.subscribe(key=f"live:{vid}", callback=forward)
    try:
        while True:
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/fleet")
async def fleet_stream(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_text(json.dumps({
                "ts": datetime.utcnow().isoformat(),
                "fleet": [
                    {"vid": "QD-01", "health": 82, "level": "Healthy", "fault": "normal"},
                    {"vid": "QD-02", "health": 76, "level": "Mild", "fault": "normal"},
                    {"vid": "QD-03", "health": 58, "level": "Mild", "fault": "imbalance"},
                    {"vid": "QD-04", "health": 44, "level": "Moderate", "fault": "loose_mount"},
                ],
            }))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
