import json
from typing import Optional

from fastapi import WebSocket


async def websocket_device_endpoint(websocket: WebSocket, vid: str) -> None:
    await websocket.accept()
    while True:
        try:
            message = await websocket.receive_text()
            if not message:
                continue
            payload = json.loads(message)
            await websocket.send_text(json.dumps({"ok": True, "vid": vid, "payload": payload}))
        except Exception:
            break
