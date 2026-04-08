"""In-process pub/sub event bus for real-time UI updates."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict], None]


class EventBus:
    """Thread-safe publish/subscribe event bus.

    Events are dicts with at least a ``type`` key.
    Subscribers receive all events (filtering is done client-side).
    Recent events are kept in a ring buffer for new subscriber catch-up.
    """

    def __init__(self, history_size: int = 200) -> None:
        self._subscribers: list[EventCallback] = []
        self._lock = threading.Lock()
        self._history: deque[dict] = deque(maxlen=history_size)

    def subscribe(self, callback: EventCallback) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: EventCallback) -> None:
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    def publish(self, event: dict) -> None:
        """Publish an event to all subscribers."""
        event.setdefault("timestamp", time.time())
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(event)
            except Exception as e:
                logger.debug("Subscriber error: %s", e)

    def get_recent(self, count: int = 50) -> list[dict]:
        """Return the most recent events from the ring buffer."""
        with self._lock:
            items = list(self._history)
        return items[-count:]


# Module-level singleton
event_bus = EventBus()
