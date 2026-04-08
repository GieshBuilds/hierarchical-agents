"""IPCListener — polls the MessageBus and dispatches to a MessageHandler.

Runs a background thread that periodically polls the message bus for
pending messages and passes them to a handler for processing.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import threading
from typing import Optional

from core.ipc.interface import MessageHandler
from core.ipc.message_bus import MessageBus
from core.ipc.models import Message, MessageType


class IPCListener:
    """Background listener that polls the MessageBus for new messages.

    Parameters
    ----------
    bus : MessageBus
        The message bus to poll for pending messages.
    handler : MessageHandler
        Protocol-compliant handler that processes incoming messages.
    poll_interval : float
        Seconds between poll cycles (default: 2.0).
    profile_name : str
        The profile to poll messages for. When empty, defaults to
        ``"hermes"`` for backward compatibility.
    """

    def __init__(
        self,
        bus: MessageBus,
        handler: MessageHandler,
        poll_interval: float = 2.0,
        profile_name: str = "",
    ) -> None:
        self._bus = bus
        self._handler = handler
        self._poll_interval = poll_interval
        self._profile_name = profile_name or "hermes"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start polling in a background daemon thread.

        Does nothing if already running.
        """
        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="ipc-listener",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it to finish.

        Does nothing if not running.
        """
        if not self._running:
            return

        self._stop_event.set()
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval * 2)
            self._thread = None

    @property
    def is_running(self) -> bool:
        """Whether the listener is currently polling."""
        return self._running

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Internal loop: poll the bus and dispatch messages until stopped.

        Polls for both TASK_REQUEST and TASK_RESPONSE messages.
        TASK_REQUESTs are dispatched to the handler for worker spawning.
        TASK_RESPONSEs are dispatched to the handler for upstream
        forwarding and/or owner delivery via hook.
        All messages are acknowledged after handling.
        """
        while not self._stop_event.is_set():
            try:
                messages = self._bus.poll(
                    self._profile_name,
                    limit=50,
                )
                for msg in messages:
                    try:
                        self._handler.handle_message(msg)
                        self._bus.acknowledge(msg.message_id)
                    except Exception:
                        # Swallow handler errors to keep the loop alive.
                        pass
            except Exception:
                # Swallow bus errors to keep the loop alive.
                pass

            # Wait for the interval or until stop is signalled.
            self._stop_event.wait(timeout=self._poll_interval)
