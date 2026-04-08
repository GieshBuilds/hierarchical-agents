"""WebSocket server for pushing real-time events to the browser."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Set

import websockets
from websockets.asyncio.server import serve

from ui.realtime.event_bus import EventBus

logger = logging.getLogger(__name__)


class WebSocketBridge:
    """Bridges the synchronous EventBus to async WebSocket connections."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._connections: Set[websockets.asyncio.server.ServerConnection] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def add_connection(self, ws) -> None:
        with self._lock:
            self._connections.add(ws)
        logger.info("WebSocket client connected (%d total)", len(self._connections))

    def remove_connection(self, ws) -> None:
        with self._lock:
            self._connections.discard(ws)
        logger.info("WebSocket client disconnected (%d total)", len(self._connections))

    def on_event(self, event: dict) -> None:
        """Called by EventBus (from sync thread). Schedules async sends."""
        if not self._loop or not self._connections:
            return
        data = json.dumps(event, default=str)
        # Schedule the broadcast on the asyncio loop
        self._loop.call_soon_threadsafe(
            asyncio.ensure_future,
            self._broadcast(data),
        )

    async def _broadcast(self, data: str) -> None:
        with self._lock:
            targets = list(self._connections)
        for ws in targets:
            try:
                await ws.send(data)
            except Exception:
                self.remove_connection(ws)


def start_ws_server(port: int, bus: EventBus) -> None:
    """Run the WebSocket server in the current thread (blocking).

    Call from a daemon thread.
    """
    bridge = WebSocketBridge(bus)
    bus.subscribe(bridge.on_event)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bridge.set_loop(loop)

    async def handler(websocket):
        bridge.add_connection(websocket)
        try:
            # Send recent history on connect
            recent = bus.get_recent(50)
            await websocket.send(json.dumps({
                "type": "history",
                "events": recent,
            }, default=str))

            # Keep connection alive; handle any client messages
            async for message in websocket:
                # Client can send ping or subscription filters
                try:
                    data = json.loads(message)
                    if data.get("type") == "ping":
                        await websocket.send(json.dumps({"type": "pong"}))
                except Exception:
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            bridge.remove_connection(websocket)

    async def main():
        async with serve(handler, "0.0.0.0", port):
            logger.info("WebSocket server listening on port %d", port)
            await asyncio.Future()  # Run forever

    loop.run_until_complete(main())
