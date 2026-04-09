from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Set

from fastapi import WebSocket

from .models import Event, EventType


@dataclass
class EventBus:
    """
    Minimal pub-sub broadcaster for WebSockets.
    """

    clients: Set[WebSocket] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self.lock:
            self.clients.discard(ws)

    async def publish(self, event_type: EventType, timestamp: datetime, payload: Dict[str, Any]) -> None:
        event = Event(type=event_type, timestamp=timestamp, payload=payload)
        message = event.model_dump()

        async with self.lock:
            clients = list(self.clients)

        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(json.dumps(message, default=str))
            except Exception:
                dead.append(ws)

        if dead:
            async with self.lock:
                for ws in dead:
                    self.clients.discard(ws)

