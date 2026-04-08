"""Message protocol — higher-level IPC patterns on top of MessageBus.

Provides request/response correlation, broadcast, escalation,
and conversation tracking.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Optional

from core.ipc.exceptions import IPCError, MessageNotFound
from core.ipc.message_bus import MessageBus
from core.ipc.models import (
    Message,
    MessagePriority,
    MessageStatus,
    MessageType,
    generate_correlation_id,
)


class MessageProtocol:
    """Higher-level message patterns built on MessageBus.

    Parameters
    ----------
    bus : MessageBus
        The underlying message bus.
    profile_registry : object | None
        Optional ProfileRegistry for hierarchy lookups (escalation).
    """

    def __init__(
        self,
        bus: MessageBus,
        profile_registry: object | None = None,
    ) -> None:
        self._bus = bus
        self._profile_registry = profile_registry

    # ------------------------------------------------------------------
    # Request / Response
    # ------------------------------------------------------------------

    def send_request(
        self,
        from_profile: str,
        to_profile: str,
        payload: dict | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        ttl: timedelta | None = ...,  # type: ignore[assignment]
    ) -> tuple[str, str]:
        """Send a task request with auto-generated correlation ID.

        Returns
        -------
        tuple[str, str]
            (message_id, correlation_id)
        """
        corr_id = generate_correlation_id()
        mid = self._bus.send(
            from_profile=from_profile,
            to_profile=to_profile,
            message_type=MessageType.TASK_REQUEST,
            payload=payload,
            correlation_id=corr_id,
            priority=priority,
            ttl=ttl,
        )
        return mid, corr_id

    def send_response(
        self,
        correlation_id: str,
        from_profile: str,
        to_profile: str,
        payload: dict | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        ttl: timedelta | None = ...,  # type: ignore[assignment]
    ) -> str:
        """Send a task response linked to a correlation ID.

        Returns
        -------
        str
            The response message_id.
        """
        return self._bus.send(
            from_profile=from_profile,
            to_profile=to_profile,
            message_type=MessageType.TASK_RESPONSE,
            payload=payload,
            correlation_id=correlation_id,
            priority=priority,
            ttl=ttl,
        )

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    def send_broadcast(
        self,
        from_profile: str,
        to_profiles: list[str],
        payload: dict | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        ttl: timedelta | None = ...,  # type: ignore[assignment]
    ) -> list[str]:
        """Send a broadcast to multiple profiles.

        Creates one message per recipient, all sharing a correlation ID.

        Returns
        -------
        list[str]
            List of message_ids, one per recipient.
        """
        corr_id = generate_correlation_id()
        message_ids: list[str] = []
        for to_profile in to_profiles:
            mid = self._bus.send(
                from_profile=from_profile,
                to_profile=to_profile,
                message_type=MessageType.BROADCAST,
                payload=payload,
                correlation_id=corr_id,
                priority=priority,
                ttl=ttl,
            )
            message_ids.append(mid)
        return message_ids

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    def send_escalation(
        self,
        from_profile: str,
        payload: dict | None = None,
        priority: MessagePriority = MessagePriority.URGENT,
        ttl: timedelta | None = ...,  # type: ignore[assignment]
    ) -> str:
        """Send an escalation to the parent profile.

        Uses the profile_registry to determine the parent profile.
        If no registry is available, raises IPCError.

        Returns
        -------
        str
            The escalation message_id.

        Raises
        ------
        IPCError
            If no profile registry is available or parent cannot be determined.
        """
        if self._profile_registry is None:
            raise IPCError("Profile registry required for escalation")

        try:
            profile = self._profile_registry.get(from_profile)  # type: ignore[union-attr]
            parent_name = profile.parent_profile  # type: ignore[union-attr]
            if parent_name is None:
                raise IPCError(
                    f"Profile '{from_profile}' has no parent to escalate to"
                )
        except AttributeError:
            raise IPCError(
                f"Cannot determine parent for profile '{from_profile}'"
            )
        except Exception as exc:
            if isinstance(exc, IPCError):
                raise
            raise IPCError(
                f"Failed to look up parent for '{from_profile}': {exc}"
            ) from exc

        return self._bus.send(
            from_profile=from_profile,
            to_profile=parent_name,
            message_type=MessageType.ESCALATION,
            payload=payload,
            priority=priority,
            ttl=ttl,
        )

    # ------------------------------------------------------------------
    # Wait for response
    # ------------------------------------------------------------------

    def wait_for_response(
        self,
        correlation_id: str,
        responding_profile: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> Message | None:
        """Wait for a response with matching correlation_id.

        Polls the bus for messages from *responding_profile* matching
        the correlation_id.

        Parameters
        ----------
        correlation_id : str
            The correlation ID to wait for.
        responding_profile : str
            The profile expected to respond.
        timeout : float
            Maximum time to wait in seconds.
        poll_interval : float
            Time between polls in seconds.

        Returns
        -------
        Message | None
            The response message if found within timeout, else None.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            messages = self._bus.get_by_correlation(correlation_id)
            for msg in messages:
                if (
                    msg.from_profile == responding_profile
                    and msg.message_type == MessageType.TASK_RESPONSE
                ):
                    return msg
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))
        return None

    # ------------------------------------------------------------------
    # Conversation tracking
    # ------------------------------------------------------------------

    def get_conversation(self, correlation_id: str) -> list[Message]:
        """Get the full conversation chain for a correlation ID.

        Returns
        -------
        list[Message]
            All messages in the conversation, ordered by created_at.
        """
        return self._bus.get_by_correlation(correlation_id)
