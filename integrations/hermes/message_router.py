"""HermesMessageRouter — routes messages between profiles in the Hermes framework.

Implements the ``MessageRouter`` protocol from ``core.ipc.interface``.
Routes messages through the message bus, optionally activating target
profiles via a ``ProfileActivator``.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

from typing import Optional

from core.ipc.interface import ProfileActivator
from core.ipc.message_bus import MessageBus
from core.ipc.models import Message


class HermesMessageRouter:
    """Routes IPC messages to correct profiles, triggering activation if needed.

    Implements the ``MessageRouter`` protocol from ``core.ipc.interface``.

    Parameters
    ----------
    bus : MessageBus
        The message bus used for delivery.
    activator : ProfileActivator | None
        Optional activator to wake up target profiles on message delivery.
    """

    def __init__(
        self,
        bus: MessageBus,
        activator: Optional[ProfileActivator] = None,
    ) -> None:
        self._bus = bus
        self._activator = activator

    # ------------------------------------------------------------------
    # MessageRouter protocol
    # ------------------------------------------------------------------

    def route_message(self, message: Message) -> bool:
        """Route a message to its recipient profile.

        If an activator is configured and the recipient profile is not
        active, the activator is asked to wake it up before delivery.

        Parameters
        ----------
        message : Message
            The message to route.

        Returns
        -------
        bool
            True if routing (sending) succeeded, False otherwise.
        """
        to_profile = message.to_profile
        if not to_profile:
            return False

        # Activate the target profile if we have an activator.
        if self._activator is not None:
            if not self._activator.is_active(to_profile):
                self._activator.activate_profile(to_profile)

        try:
            self._bus.send(
                from_profile=message.from_profile,
                to_profile=to_profile,
                message_type=message.message_type,
                payload=message.payload,
                correlation_id=message.correlation_id,
                priority=message.priority,
            )
            return True
        except Exception:
            return False

    def can_route(self, to_profile: str) -> bool:
        """Check if a message can be routed to the given profile.

        Parameters
        ----------
        to_profile : str
            The target profile.

        Returns
        -------
        bool
            True if routing is possible. Currently always True if
            the profile name is non-empty.
        """
        return bool(to_profile)
