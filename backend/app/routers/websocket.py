import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.analytics.health import explain_fault_bucket, level_for_score, score_from_inference
from app.ingest.event_bus import bus
from app.ingest.websocket_device import websocket_device_endpoint

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
            await websocket.receive_text()     # returns/raises when the browser disconnects
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        bus.unsubscribe(key=f"live:{vid}", callback=forward)


@router.websocket("/ws/device/{vid}")
async def device_stream(websocket: WebSocket, vid: str):
    await websocket_device_endpoint(websocket, vid)


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
