"""Data models and constants for IPC messaging."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional


class MessageType(str, Enum):
    """Types of IPC messages."""
    TASK_REQUEST = "task_request"
    TASK_RESPONSE = "task_response"
    STATUS_QUERY = "status_query"
    STATUS_RESPONSE = "status_response"
    BROADCAST = "broadcast"
    ESCALATION = "escalation"


class MessagePriority(str, Enum):
    """Priority levels for messages."""
    LOW = "low"
    NORMAL = "normal"
    URGENT = "urgent"

    @property
    def sort_order(self) -> int:
        """Numeric sort order for priority queue (higher = processed first)."""
        return {"low": 0, "normal": 1, "urgent": 2}[self.value]


class MessageStatus(str, Enum):
    """Status of a message in the bus."""
    PENDING = "pending"
    DELIVERED = "delivered"
    READ = "read"
    EXPIRED = "expired"


# Valid status transitions
VALID_STATUS_TRANSITIONS: dict[MessageStatus, set[MessageStatus]] = {
    MessageStatus.PENDING: {MessageStatus.DELIVERED, MessageStatus.EXPIRED},
    MessageStatus.DELIVERED: {MessageStatus.READ, MessageStatus.EXPIRED},
    MessageStatus.READ: set(),  # Terminal state
    MessageStatus.EXPIRED: set(),  # Terminal state
}


def generate_message_id() -> str:
    """Generate a unique message ID with msg- prefix."""
    return f"msg-{uuid.uuid4().hex[:12]}"


def generate_correlation_id() -> str:
    """Generate a unique correlation ID with corr- prefix."""
    return f"corr-{uuid.uuid4().hex[:12]}"


# Default TTL for messages: 24 hours
DEFAULT_TTL = timedelta(hours=24)


@dataclass
class Message:
    """A message in the IPC bus.

    Attributes
    ----------
    message_id : str
        Unique identifier (msg-XXXX format)
    from_profile : str
        Sender profile name
    to_profile : str
        Recipient profile name
    message_type : MessageType
        Type of message
    payload : dict[str, Any]
        JSON-serializable message content
    correlation_id : Optional[str]
        Links requests to responses (corr-XXXX format)
    priority : MessagePriority
        Message priority level
    status : MessageStatus
        Current message status
    created_at : datetime
        When the message was created (UTC)
    expires_at : Optional[datetime]
        When the message expires (UTC), None = never expires
    """
    message_id: str = field(default_factory=generate_message_id)
    from_profile: str = ""
    to_profile: str = ""
    message_type: MessageType = MessageType.TASK_REQUEST
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: Optional[str] = None
    priority: MessagePriority = MessagePriority.NORMAL
    status: MessageStatus = MessageStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None

    def is_expired(self) -> bool:
        """Check if the message has passed its expiry time."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def payload_as_json(self) -> str:
        """Serialize payload to JSON string."""
        return json.dumps(self.payload)

    @staticmethod
    def payload_from_json(json_str: str) -> dict[str, Any]:
        """Deserialize payload from JSON string."""
        return json.loads(json_str)

    def can_transition_to(self, new_status: MessageStatus) -> bool:
        """Check if transition to new_status is valid."""
        return new_status in VALID_STATUS_TRANSITIONS.get(self.status, set())
