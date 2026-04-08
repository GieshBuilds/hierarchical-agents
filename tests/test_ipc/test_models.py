"""Tests for IPC data models."""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone, timedelta
from core.ipc.models import (
    MessageType,
    MessagePriority,
    MessageStatus,
    Message,
    VALID_STATUS_TRANSITIONS,
    DEFAULT_TTL,
    generate_message_id,
    generate_correlation_id,
)


class TestMessageType:
    def test_values(self):
        assert MessageType.TASK_REQUEST == "task_request"
        assert MessageType.TASK_RESPONSE == "task_response"
        assert MessageType.STATUS_QUERY == "status_query"
        assert MessageType.STATUS_RESPONSE == "status_response"
        assert MessageType.BROADCAST == "broadcast"
        assert MessageType.ESCALATION == "escalation"

    def test_is_string(self):
        assert isinstance(MessageType.TASK_REQUEST, str)

    def test_count(self):
        assert len(MessageType) == 6


class TestMessagePriority:
    def test_values(self):
        assert MessagePriority.LOW == "low"
        assert MessagePriority.NORMAL == "normal"
        assert MessagePriority.URGENT == "urgent"

    def test_sort_order(self):
        assert MessagePriority.LOW.sort_order < MessagePriority.NORMAL.sort_order
        assert MessagePriority.NORMAL.sort_order < MessagePriority.URGENT.sort_order

    def test_is_string(self):
        assert isinstance(MessagePriority.NORMAL, str)


class TestMessageStatus:
    def test_values(self):
        assert MessageStatus.PENDING == "pending"
        assert MessageStatus.DELIVERED == "delivered"
        assert MessageStatus.READ == "read"
        assert MessageStatus.EXPIRED == "expired"

    def test_is_string(self):
        assert isinstance(MessageStatus.PENDING, str)


class TestStatusTransitions:
    def test_pending_transitions(self):
        assert VALID_STATUS_TRANSITIONS[MessageStatus.PENDING] == {
            MessageStatus.DELIVERED, MessageStatus.EXPIRED
        }

    def test_delivered_transitions(self):
        assert VALID_STATUS_TRANSITIONS[MessageStatus.DELIVERED] == {
            MessageStatus.READ, MessageStatus.EXPIRED
        }

    def test_read_is_terminal(self):
        assert VALID_STATUS_TRANSITIONS[MessageStatus.READ] == set()

    def test_expired_is_terminal(self):
        assert VALID_STATUS_TRANSITIONS[MessageStatus.EXPIRED] == set()


class TestGenerateMessageId:
    def test_prefix(self):
        mid = generate_message_id()
        assert mid.startswith("msg-")

    def test_length(self):
        mid = generate_message_id()
        assert len(mid) == 16  # msg- (4) + 12 hex chars

    def test_unique(self):
        ids = {generate_message_id() for _ in range(100)}
        assert len(ids) == 100


class TestGenerateCorrelationId:
    def test_prefix(self):
        cid = generate_correlation_id()
        assert cid.startswith("corr-")

    def test_length(self):
        cid = generate_correlation_id()
        assert len(cid) == 17  # corr- (5) + 12 hex chars

    def test_unique(self):
        ids = {generate_correlation_id() for _ in range(100)}
        assert len(ids) == 100


class TestDefaultTTL:
    def test_is_24_hours(self):
        assert DEFAULT_TTL == timedelta(hours=24)


class TestMessage:
    def test_default_values(self):
        msg = Message()
        assert msg.message_id.startswith("msg-")
        assert msg.from_profile == ""
        assert msg.to_profile == ""
        assert msg.message_type == MessageType.TASK_REQUEST
        assert msg.payload == {}
        assert msg.correlation_id is None
        assert msg.priority == MessagePriority.NORMAL
        assert msg.status == MessageStatus.PENDING
        assert msg.created_at is not None
        assert msg.expires_at is None

    def test_custom_values(self):
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=1)
        msg = Message(
            message_id="msg-test123456",
            from_profile="ceo",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "fix the bug"},
            correlation_id="corr-test123456",
            priority=MessagePriority.URGENT,
            status=MessageStatus.PENDING,
            created_at=now,
            expires_at=expires,
        )
        assert msg.from_profile == "ceo"
        assert msg.to_profile == "cto"
        assert msg.payload["task"] == "fix the bug"

    def test_is_expired_when_no_expiry(self):
        msg = Message()
        assert msg.is_expired() is False

    def test_is_expired_when_future(self):
        msg = Message(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        assert msg.is_expired() is False

    def test_is_expired_when_past(self):
        msg = Message(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        assert msg.is_expired() is True

    def test_payload_as_json(self):
        msg = Message(payload={"key": "value", "num": 42})
        j = msg.payload_as_json()
        parsed = json.loads(j)
        assert parsed == {"key": "value", "num": 42}

    def test_payload_from_json(self):
        data = {"key": "value", "nested": {"a": 1}}
        result = Message.payload_from_json(json.dumps(data))
        assert result == data

    def test_can_transition_to_valid(self):
        msg = Message(status=MessageStatus.PENDING)
        assert msg.can_transition_to(MessageStatus.DELIVERED) is True
        assert msg.can_transition_to(MessageStatus.EXPIRED) is True

    def test_can_transition_to_invalid(self):
        msg = Message(status=MessageStatus.PENDING)
        assert msg.can_transition_to(MessageStatus.READ) is False

    def test_terminal_states_no_transitions(self):
        msg_read = Message(status=MessageStatus.READ)
        msg_expired = Message(status=MessageStatus.EXPIRED)
        for status in MessageStatus:
            assert msg_read.can_transition_to(status) is False
            assert msg_expired.can_transition_to(status) is False
