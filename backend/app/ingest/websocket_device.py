import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.ingest.event_bus import bus
from app.ingest.processing import process_frame

logger = logging.getLogger("quietdrive.device")


def parse_frame(text: str):
    """Same tolerant decode as the serial path: dict with a type, else None."""
    if not text or not text.strip():
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Malformed device frame ignored: %s", text[:200])
        return None
    if not isinstance(payload, dict) or "t" not in payload:
        return None
    return payload


async def websocket_device_endpoint(websocket: WebSocket, vid: str) -> None:
    """ESP32 or bridge/live.py: newline-free JSON frames per docs/protocol.md."""
    await websocket.accept()
    logger.info("Device connected: %s", vid)
    try:
        while True:
            payload = parse_frame(await websocket.receive_text())
            if payload is None:
                continue
            payload.setdefault("vid", vid)
            bus.mark_device(payload["vid"])
            await bus.publish(process_frame(payload))
    except WebSocketDisconnect:
        logger.info("Device disconnected: %s", vid)
    except Exception as exc:  # pragma: no cover
        logger.warning("Device %s closed after error: %s", vid, exc)
