"""Tests for the IPC Message Bus core operations."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from core.ipc.exceptions import (
    InvalidRecipient,
    MessageBusError,
    MessageExpired,
    MessageNotFound,
)
from core.ipc.message_bus import MessageBus
from core.ipc.models import (
    Message,
    MessagePriority,
    MessageStatus,
    MessageType,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def bus():
    """Create an in-memory MessageBus."""
    b = MessageBus(db_path=":memory:", default_ttl=timedelta(hours=24))
    yield b
    b.close()


@pytest.fixture
def bus_no_ttl():
    """Create a MessageBus with no default TTL."""
    b = MessageBus(db_path=":memory:", default_ttl=None)
    yield b
    b.close()


@pytest.fixture
def bus_with_registry():
    """Create a MessageBus with a mock profile registry."""

    class MockRegistry:
        def __init__(self):
            self.profiles = {"ceo", "cto", "pm-alpha"}

        def get(self, name):
            if name not in self.profiles:
                raise KeyError(f"Profile not found: {name}")
            return {"name": name}

    b = MessageBus(
        db_path=":memory:",
        profile_registry=MockRegistry(),
        default_ttl=timedelta(hours=1),
    )
    yield b
    b.close()


def _send_test_message(
    bus: MessageBus,
    from_profile: str = "ceo",
    to_profile: str = "cto",
    message_type: MessageType = MessageType.TASK_REQUEST,
    payload: dict | None = None,
    priority: MessagePriority = MessagePriority.NORMAL,
    correlation_id: str | None = None,
    ttl: timedelta | None = ...,
) -> str:
    """Helper to send a test message."""
    return bus.send(
        from_profile=from_profile,
        to_profile=to_profile,
        message_type=message_type,
        payload=payload or {"task": "test"},
        priority=priority,
        correlation_id=correlation_id,
        ttl=ttl,
    )


# ------------------------------------------------------------------
# Send tests
# ------------------------------------------------------------------

class TestSend:
    def test_returns_message_id(self, bus):
        mid = _send_test_message(bus)
        assert mid.startswith("msg-")

    def test_message_is_pending(self, bus):
        mid = _send_test_message(bus)
        msg = bus.get(mid)
        assert msg.status == MessageStatus.PENDING

    def test_sets_from_profile(self, bus):
        mid = _send_test_message(bus, from_profile="ceo")
        msg = bus.get(mid)
        assert msg.from_profile == "ceo"

    def test_sets_to_profile(self, bus):
        mid = _send_test_message(bus, to_profile="cto")
        msg = bus.get(mid)
        assert msg.to_profile == "cto"

    def test_sets_message_type(self, bus):
        mid = _send_test_message(bus, message_type=MessageType.ESCALATION)
        msg = bus.get(mid)
        assert msg.message_type == MessageType.ESCALATION

    def test_sets_payload(self, bus):
        mid = _send_test_message(bus, payload={"key": "value", "num": 42})
        msg = bus.get(mid)
        assert msg.payload == {"key": "value", "num": 42}

    def test_default_payload_is_empty(self, bus):
        mid = bus.send("ceo", "cto", MessageType.STATUS_QUERY)
        msg = bus.get(mid)
        assert msg.payload == {}

    def test_sets_priority(self, bus):
        mid = _send_test_message(bus, priority=MessagePriority.URGENT)
        msg = bus.get(mid)
        assert msg.priority == MessagePriority.URGENT

    def test_sets_correlation_id(self, bus):
        mid = _send_test_message(bus, correlation_id="corr-test123456")
        msg = bus.get(mid)
        assert msg.correlation_id == "corr-test123456"

    def test_default_ttl_sets_expires_at(self, bus):
        mid = _send_test_message(bus)
        msg = bus.get(mid)
        assert msg.expires_at is not None
        # Should be roughly 24 hours from now
        delta = msg.expires_at - msg.created_at
        assert timedelta(hours=23, minutes=59) < delta < timedelta(hours=24, minutes=1)

    def test_explicit_ttl(self, bus):
        mid = _send_test_message(bus, ttl=timedelta(minutes=30))
        msg = bus.get(mid)
        assert msg.expires_at is not None
        delta = msg.expires_at - msg.created_at
        assert timedelta(minutes=29) < delta < timedelta(minutes=31)

    def test_none_ttl_no_expiry(self, bus):
        mid = _send_test_message(bus, ttl=None)
        msg = bus.get(mid)
        assert msg.expires_at is None

    def test_no_default_ttl_no_expiry(self, bus_no_ttl):
        mid = _send_test_message(bus_no_ttl)
        msg = bus_no_ttl.get(mid)
        assert msg.expires_at is None

    def test_unique_ids(self, bus):
        ids = {_send_test_message(bus) for _ in range(50)}
        assert len(ids) == 50

    def test_created_at_is_utc(self, bus):
        mid = _send_test_message(bus)
        msg = bus.get(mid)
        assert msg.created_at.tzinfo is not None


class TestSendWithRegistry:
    def test_valid_recipient(self, bus_with_registry):
        mid = _send_test_message(bus_with_registry, to_profile="cto")
        assert mid.startswith("msg-")

    def test_invalid_recipient(self, bus_with_registry):
        with pytest.raises(InvalidRecipient) as exc_info:
            _send_test_message(bus_with_registry, to_profile="nonexistent")
        assert "nonexistent" in str(exc_info.value)


# ------------------------------------------------------------------
# Get tests
# ------------------------------------------------------------------

class TestGet:
    def test_retrieves_message(self, bus):
        mid = _send_test_message(bus)
        msg = bus.get(mid)
        assert msg.message_id == mid

    def test_not_found(self, bus):
        with pytest.raises(MessageNotFound):
            bus.get("msg-nonexistent")

    def test_returns_message_dataclass(self, bus):
        mid = _send_test_message(bus)
        msg = bus.get(mid)
        assert isinstance(msg, Message)


# ------------------------------------------------------------------
# Poll tests
# ------------------------------------------------------------------

class TestPoll:
    def test_returns_pending_messages(self, bus):
        _send_test_message(bus, to_profile="cto")
        _send_test_message(bus, to_profile="cto")
        messages = bus.poll("cto")
        assert len(messages) == 2

    def test_only_for_specified_profile(self, bus):
        _send_test_message(bus, to_profile="cto")
        _send_test_message(bus, to_profile="pm-alpha")
        messages = bus.poll("cto")
        assert len(messages) == 1
        assert messages[0].to_profile == "cto"

    def test_excludes_delivered(self, bus):
        mid = _send_test_message(bus, to_profile="cto")
        bus.acknowledge(mid)
        messages = bus.poll("cto")
        assert len(messages) == 0

    def test_excludes_read(self, bus):
        mid = _send_test_message(bus, to_profile="cto")
        bus.acknowledge(mid)
        bus.mark_read(mid)
        messages = bus.poll("cto")
        assert len(messages) == 0

    def test_priority_ordering_urgent_first(self, bus):
        _send_test_message(bus, to_profile="cto", priority=MessagePriority.LOW)
        _send_test_message(bus, to_profile="cto", priority=MessagePriority.URGENT)
        _send_test_message(bus, to_profile="cto", priority=MessagePriority.NORMAL)
        messages = bus.poll("cto")
        assert messages[0].priority == MessagePriority.URGENT
        assert messages[1].priority == MessagePriority.NORMAL
        assert messages[2].priority == MessagePriority.LOW

    def test_fifo_within_priority(self, bus):
        mid1 = _send_test_message(bus, to_profile="cto", payload={"order": 1})
        mid2 = _send_test_message(bus, to_profile="cto", payload={"order": 2})
        messages = bus.poll("cto")
        assert messages[0].message_id == mid1
        assert messages[1].message_id == mid2

    def test_limit(self, bus):
        for _ in range(5):
            _send_test_message(bus, to_profile="cto")
        messages = bus.poll("cto", limit=3)
        assert len(messages) == 3

    def test_filter_by_type(self, bus):
        _send_test_message(bus, to_profile="cto", message_type=MessageType.TASK_REQUEST)
        _send_test_message(bus, to_profile="cto", message_type=MessageType.STATUS_QUERY)
        messages = bus.poll("cto", message_type=MessageType.TASK_REQUEST)
        assert len(messages) == 1
        assert messages[0].message_type == MessageType.TASK_REQUEST

    def test_excludes_expired_by_default(self, bus):
        # Send with very short TTL
        mid = _send_test_message(bus, to_profile="cto", ttl=timedelta(seconds=-1))
        messages = bus.poll("cto")
        assert len(messages) == 0

    def test_include_expired(self, bus):
        mid = _send_test_message(bus, to_profile="cto", ttl=timedelta(seconds=-1))
        messages = bus.poll("cto", include_expired=True)
        assert len(messages) == 1

    def test_empty_result(self, bus):
        messages = bus.poll("cto")
        assert messages == []


# ------------------------------------------------------------------
# Acknowledge tests
# ------------------------------------------------------------------

class TestAcknowledge:
    def test_transitions_to_delivered(self, bus):
        mid = _send_test_message(bus)
        msg = bus.acknowledge(mid)
        assert msg.status == MessageStatus.DELIVERED

    def test_not_found(self, bus):
        with pytest.raises(MessageNotFound):
            bus.acknowledge("msg-nonexistent")

    def test_cannot_acknowledge_read(self, bus):
        mid = _send_test_message(bus)
        bus.acknowledge(mid)
        bus.mark_read(mid)
        with pytest.raises(MessageBusError):
            bus.acknowledge(mid)

    def test_cannot_re_acknowledge(self, bus):
        mid = _send_test_message(bus)
        bus.acknowledge(mid)
        with pytest.raises(MessageBusError):
            bus.acknowledge(mid)


# ------------------------------------------------------------------
# Mark read tests
# ------------------------------------------------------------------

class TestMarkRead:
    def test_transitions_to_read(self, bus):
        mid = _send_test_message(bus)
        bus.acknowledge(mid)
        msg = bus.mark_read(mid)
        assert msg.status == MessageStatus.READ

    def test_cannot_read_pending(self, bus):
        mid = _send_test_message(bus)
        with pytest.raises(MessageBusError):
            bus.mark_read(mid)

    def test_not_found(self, bus):
        with pytest.raises(MessageNotFound):
            bus.mark_read("msg-nonexistent")

    def test_cannot_re_read(self, bus):
        mid = _send_test_message(bus)
        bus.acknowledge(mid)
        bus.mark_read(mid)
        with pytest.raises(MessageBusError):
            bus.mark_read(mid)


# ------------------------------------------------------------------
# Get by correlation tests
# ------------------------------------------------------------------

class TestGetByCorrelation:
    def test_returns_correlated_messages(self, bus):
        mid1 = _send_test_message(bus, correlation_id="corr-abc")
        mid2 = _send_test_message(
            bus, from_profile="cto", to_profile="ceo",
            message_type=MessageType.TASK_RESPONSE,
            correlation_id="corr-abc",
        )
        messages = bus.get_by_correlation("corr-abc")
        assert len(messages) == 2
        assert messages[0].message_id == mid1
        assert messages[1].message_id == mid2

    def test_excludes_uncorrelated(self, bus):
        _send_test_message(bus, correlation_id="corr-abc")
        _send_test_message(bus, correlation_id="corr-def")
        messages = bus.get_by_correlation("corr-abc")
        assert len(messages) == 1

    def test_empty_result(self, bus):
        messages = bus.get_by_correlation("corr-nonexistent")
        assert messages == []

    def test_ordered_by_created_at(self, bus):
        mid1 = _send_test_message(bus, correlation_id="corr-xyz")
        mid2 = _send_test_message(
            bus, from_profile="cto", to_profile="ceo",
            correlation_id="corr-xyz",
        )
        messages = bus.get_by_correlation("corr-xyz")
        assert messages[0].message_id == mid1
        assert messages[1].message_id == mid2


# ------------------------------------------------------------------
# Get pending count tests
# ------------------------------------------------------------------

class TestGetPendingCount:
    def test_counts_pending(self, bus):
        _send_test_message(bus, to_profile="cto")
        _send_test_message(bus, to_profile="cto")
        _send_test_message(bus, to_profile="pm-alpha")
        assert bus.get_pending_count("cto") == 2
        assert bus.get_pending_count("pm-alpha") == 1

    def test_excludes_delivered(self, bus):
        mid = _send_test_message(bus, to_profile="cto")
        bus.acknowledge(mid)
        assert bus.get_pending_count("cto") == 0

    def test_excludes_expired(self, bus):
        _send_test_message(bus, to_profile="cto", ttl=timedelta(seconds=-1))
        assert bus.get_pending_count("cto") == 0

    def test_zero_for_unknown_profile(self, bus):
        assert bus.get_pending_count("nobody") == 0


# ------------------------------------------------------------------
# List messages tests
# ------------------------------------------------------------------

class TestListMessages:
    def test_returns_all_messages(self, bus):
        _send_test_message(bus, from_profile="ceo", to_profile="cto")
        _send_test_message(bus, from_profile="cto", to_profile="ceo")
        messages = bus.list_messages()
        assert len(messages) == 2

    def test_filter_by_profile_received(self, bus):
        _send_test_message(bus, from_profile="ceo", to_profile="cto")
        _send_test_message(bus, from_profile="cto", to_profile="ceo")
        messages = bus.list_messages(profile_name="cto", direction="received")
        assert len(messages) == 1
        assert messages[0].to_profile == "cto"

    def test_filter_by_profile_sent(self, bus):
        _send_test_message(bus, from_profile="ceo", to_profile="cto")
        _send_test_message(bus, from_profile="cto", to_profile="ceo")
        messages = bus.list_messages(profile_name="ceo", direction="sent")
        assert len(messages) == 1
        assert messages[0].from_profile == "ceo"

    def test_filter_by_profile_both_directions(self, bus):
        _send_test_message(bus, from_profile="ceo", to_profile="cto")
        _send_test_message(bus, from_profile="cto", to_profile="ceo")
        _send_test_message(bus, from_profile="pm", to_profile="pm2")
        messages = bus.list_messages(profile_name="ceo")
        assert len(messages) == 2

    def test_filter_by_status(self, bus):
        mid = _send_test_message(bus)
        _send_test_message(bus)
        bus.acknowledge(mid)
        messages = bus.list_messages(status=MessageStatus.DELIVERED)
        assert len(messages) == 1

    def test_filter_by_type(self, bus):
        _send_test_message(bus, message_type=MessageType.TASK_REQUEST)
        _send_test_message(bus, message_type=MessageType.BROADCAST)
        messages = bus.list_messages(message_type=MessageType.BROADCAST)
        assert len(messages) == 1

    def test_limit_and_offset(self, bus):
        for i in range(5):
            _send_test_message(bus, payload={"i": i})
        messages = bus.list_messages(limit=2, offset=1)
        assert len(messages) == 2

    def test_ordered_by_created_at_desc(self, bus):
        mid1 = _send_test_message(bus)
        mid2 = _send_test_message(bus)
        messages = bus.list_messages()
        # Most recent first
        assert messages[0].message_id == mid2
        assert messages[1].message_id == mid1


# ------------------------------------------------------------------
# Delete tests
# ------------------------------------------------------------------

class TestDelete:
    def test_deletes_message(self, bus):
        mid = _send_test_message(bus)
        result = bus.delete(mid)
        assert result is True
        with pytest.raises(MessageNotFound):
            bus.get(mid)

    def test_not_found(self, bus):
        with pytest.raises(MessageNotFound):
            bus.delete("msg-nonexistent")


# ------------------------------------------------------------------
# Stats tests
# ------------------------------------------------------------------

class TestGetStats:
    def test_empty_stats(self, bus):
        stats = bus.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["by_type"] == {}
        assert stats["by_profile"] == {}
        assert stats["archived"] == 0

    def test_counts_by_status(self, bus):
        mid1 = _send_test_message(bus)
        _send_test_message(bus)
        bus.acknowledge(mid1)
        stats = bus.get_stats()
        assert stats["total"] == 2
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["delivered"] == 1

    def test_counts_by_type(self, bus):
        _send_test_message(bus, message_type=MessageType.TASK_REQUEST)
        _send_test_message(bus, message_type=MessageType.BROADCAST)
        _send_test_message(bus, message_type=MessageType.BROADCAST)
        stats = bus.get_stats()
        assert stats["by_type"]["task_request"] == 1
        assert stats["by_type"]["broadcast"] == 2

    def test_counts_by_profile(self, bus):
        _send_test_message(bus, to_profile="cto")
        _send_test_message(bus, to_profile="cto")
        _send_test_message(bus, to_profile="pm")
        stats = bus.get_stats()
        assert stats["by_profile"]["cto"] == 2
        assert stats["by_profile"]["pm"] == 1


# ------------------------------------------------------------------
# Thread safety tests
# ------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_sends(self, bus):
        """Multiple threads can send messages concurrently."""
        results = []
        errors = []

        def send_msg(thread_id):
            try:
                mid = bus.send(
                    from_profile=f"sender-{thread_id}",
                    to_profile="cto",
                    message_type=MessageType.TASK_REQUEST,
                    payload={"thread": thread_id},
                )
                results.append(mid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=send_msg, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        assert len(set(results)) == 10  # All unique

    def test_concurrent_poll_and_send(self, bus):
        """Polling and sending can happen concurrently."""
        errors = []

        def sender():
            try:
                for _ in range(10):
                    bus.send("ceo", "cto", MessageType.TASK_REQUEST, {"data": "test"})
            except Exception as e:
                errors.append(e)

        def poller():
            try:
                for _ in range(10):
                    bus.poll("cto")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=sender)
        t2 = threading.Thread(target=poller)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0


# ------------------------------------------------------------------
# File-backed database tests
# ------------------------------------------------------------------

class TestFileBacked:
    def test_creates_db_file(self, bus_db_path):
        bus = MessageBus(db_path=bus_db_path)
        mid = _send_test_message(bus)
        msg = bus.get(mid)
        assert msg.message_id == mid
        bus.close()

    def test_persists_across_connections(self, bus_db_path):
        bus1 = MessageBus(db_path=bus_db_path)
        mid = _send_test_message(bus1)
        bus1.close()

        bus2 = MessageBus(db_path=bus_db_path)
        msg = bus2.get(mid)
        assert msg.message_id == mid
        bus2.close()


# ------------------------------------------------------------------
# Expired message handling
# ------------------------------------------------------------------

class TestExpiredMessages:
    def test_cannot_acknowledge_expired(self, bus):
        mid = _send_test_message(bus, ttl=timedelta(seconds=-1))
        with pytest.raises(MessageExpired):
            bus.acknowledge(mid)

    def test_cannot_mark_read_expired(self, bus):
        mid = _send_test_message(bus, ttl=timedelta(seconds=-1))
        # First transition to delivered (will fail because expired)
        with pytest.raises(MessageExpired):
            bus.acknowledge(mid)
