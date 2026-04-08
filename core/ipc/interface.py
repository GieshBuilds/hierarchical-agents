"""Framework integration interfaces for IPC.

Defines protocols (abstract contracts) that frameworks implement
to integrate with the IPC message bus. Uses typing.Protocol for
structural subtyping — no inheritance required.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from core.ipc.models import Message


@runtime_checkable
class MessageHandler(Protocol):
    """Protocol for handling incoming IPC messages.

    Frameworks implement this to define how messages are processed
    when a profile receives them.
    """

    def handle_message(self, message: Message) -> Optional[Message]:
        """Process an incoming message.

        Parameters
        ----------
        message : Message
            The incoming message to handle.

        Returns
        -------
        Optional[Message]
            A response message if applicable, or None for fire-and-forget.
        """
        ...


@runtime_checkable
class ProfileActivator(Protocol):
    """Protocol for activating/deactivating profiles on message receipt.

    Frameworks implement this to define the profile lifecycle
    when messages arrive.
    """

    def activate_profile(self, profile_name: str) -> bool:
        """Activate a profile to process pending messages.

        Parameters
        ----------
        profile_name : str
            The profile to activate.

        Returns
        -------
        bool
            True if activation succeeded.
        """
        ...

    def deactivate_profile(self, profile_name: str) -> bool:
        """Deactivate a profile after processing.

        Parameters
        ----------
        profile_name : str
            The profile to deactivate.

        Returns
        -------
        bool
            True if deactivation succeeded.
        """
        ...

    def is_active(self, profile_name: str) -> bool:
        """Check if a profile is currently active.

        Parameters
        ----------
        profile_name : str
            The profile to check.

        Returns
        -------
        bool
            True if the profile is active.
        """
        ...


@runtime_checkable
class MessageRouter(Protocol):
    """Protocol for routing messages between profiles.

    Frameworks implement this to define message delivery behavior —
    e.g., activating profiles, queuing for later delivery, etc.
    """

    def route_message(self, message: Message) -> bool:
        """Route a message to its recipient.

        Parameters
        ----------
        message : Message
            The message to route.

        Returns
        -------
        bool
            True if routing succeeded.
        """
        ...

    def can_route(self, to_profile: str) -> bool:
        """Check if a message can be routed to the given profile.

        Parameters
        ----------
        to_profile : str
            The target profile.

        Returns
        -------
        bool
            True if routing is possible.
        """
        ...
