import asyncio
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._groups: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, train_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._groups[train_id].add(ws)

    async def unsubscribe(self, train_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._groups[train_id].discard(ws)
            if not self._groups[train_id]:
                del self._groups[train_id]

    async def broadcast(self, train_id: str, message: str) -> None:
        sockets = list(self._groups.get(train_id, []))
        dead = []
        for ws in sockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unsubscribe(train_id, ws)
