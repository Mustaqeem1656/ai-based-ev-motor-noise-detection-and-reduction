import asyncio
from collections import defaultdict
from typing import Any, Callable, DefaultDict, List


class EventBus:
    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, List[Callable[[dict], Any]]] = defaultdict(list)
        self._queue: asyncio.Queue = asyncio.Queue()

    def subscribe(self, *, key: str = "all", callback: Callable[[dict], Any]) -> None:
        self._subscribers[key].append(callback)

    async def publish(self, event: dict) -> None:
        await self._queue.put(event)
        for callback in list(self._subscribers.get("all", [])):
            await self._safe_call(callback, event)

    async def publish_for_vehicle(self, vid: str, event: dict) -> None:
        await self.publish({**event, "vid": vid})

    async def _safe_call(self, callback: Callable[[dict], Any], event: dict) -> None:
        try:
            result = callback(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass


bus = EventBus()
