import asyncio
import time
from collections import defaultdict
from typing import Any, Callable, DefaultDict, Dict, List


class EventBus:
    """In-process pub/sub. Subscribers use key "all" or "live:<vid>"."""

    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, List[Callable[[dict], Any]]] = defaultdict(list)
        self._device_seen: Dict[str, float] = {}

    def subscribe(self, *, key: str = "all", callback: Callable[[dict], Any]) -> None:
        self._subscribers[key].append(callback)

    def unsubscribe(self, *, key: str, callback: Callable[[dict], Any]) -> None:
        if callback in self._subscribers.get(key, []):
            self._subscribers[key].remove(callback)

    async def publish(self, event: dict) -> None:
        keys = ["all"]
        if event.get("vid"):
            keys.append(f"live:{event['vid']}")
        for key in keys:
            for callback in list(self._subscribers.get(key, [])):
                await self._safe_call(callback, event)

    async def publish_for_vehicle(self, vid: str, event: dict) -> None:
        await self.publish({**event, "vid": vid})

    # A real device (ESP32 or bridge) streaming for a vehicle pauses the simulator for it.
    def mark_device(self, vid: str) -> None:
        self._device_seen[vid] = time.time()

    def device_active(self, vid: str, within_s: float = 3.0) -> bool:
        return time.time() - self._device_seen.get(vid, 0.0) < within_s

    async def _safe_call(self, callback: Callable[[dict], Any], event: dict) -> None:
        try:
            result = callback(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass


bus = EventBus()
