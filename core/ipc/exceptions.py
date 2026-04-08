"""Custom exceptions for IPC operations."""

from __future__ import annotations


class IPCError(Exception):
    """Base exception for all IPC operations."""
    pass


class MessageNotFound(IPCError):
    """Raised when a message is not found."""

    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        super().__init__(f"Message not found: {message_id}")


class InvalidRecipient(IPCError):
    """Raised when the recipient profile is invalid."""

    def __init__(self, profile_name: str, reason: str = "") -> None:
        self.profile_name = profile_name
        self.reason = reason
        msg = f"Invalid recipient: {profile_name}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class InvalidMessageType(IPCError):
    """Raised when an invalid message type is provided."""

    def __init__(self, message_type: str) -> None:
        self.message_type = message_type
        super().__init__(f"Invalid message type: {message_type}")


class MessageExpired(IPCError):
    """Raised when operating on an expired message."""

    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        super().__init__(f"Message has expired: {message_id}")


class MessageBusError(IPCError):
    """Raised for message bus operational errors."""
    pass


class DeliveryError(IPCError):
    """Raised when message delivery fails."""

    def __init__(self, message_id: str, reason: str = "") -> None:
        self.message_id = message_id
        self.reason = reason
        msg = f"Delivery failed for message: {message_id}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)
