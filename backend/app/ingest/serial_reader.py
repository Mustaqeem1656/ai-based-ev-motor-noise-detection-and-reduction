import asyncio
import json
import logging
from typing import Any, Optional

import serial

from app.ingest.event_bus import bus
from app.ingest.processing import process_frame

logger = logging.getLogger("quietdrive.serial")


class SerialReader:
    def __init__(self, port: str, baud: int = 115200) -> None:
        self.port = port
        self.baud = baud
        self._connection: Optional[serial.Serial] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        while True:
            try:
                if self._connection is None or not self._connection.is_open:
                    self._connection = serial.Serial(self.port, self.baud, timeout=1)
                    logger.info("Serial port open: %s", self.port)
                while self._connection.is_open:
                    line = self._connection.readline()
                    if not line:
                        continue
                    parsed = self._parse_line(line.decode("utf-8", errors="ignore").strip())
                    if parsed is not None:
                        if parsed.get("vid"):
                            bus.mark_device(parsed["vid"])
                        await bus.publish(process_frame(parsed))
            except Exception as exc:  # pragma: no cover - tolerant runtime handling
                logger.warning("Serial reconnecting after error: %s", exc)
                await asyncio.sleep(2)

    def _parse_line(self, line: str) -> Optional[dict]:
        if not line:
            return None
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                return None
            return payload
        except json.JSONDecodeError:
            logger.warning("Malformed serial frame ignored: %s", line[:200])
            return None
