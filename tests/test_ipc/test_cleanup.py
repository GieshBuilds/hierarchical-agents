"""Tests for IPC message cleanup — TTL expiry and archival."""
from __future__ import annotations

from datetime import timedelta

import pytest

from core.ipc.cleanup import MessageCleanup
from core.ipc.message_bus import MessageBus
from core.ipc.models import MessageStatus, MessageType, MessagePriority


@pytest.fixture
def bus():
    b = MessageBus(db_path=":memory:", default_ttl=timedelta(hours=24))
    yield b
    b.close()


@pytest.fixture
def cleanup(bus):
    return MessageCleanup(bus)


def _send(bus, to="cto", ttl=..., **kwargs):
    return bus.send(
        from_profile="ceo",
        to_profile=to,
        message_type=MessageType.TASK_REQUEST,
        payload={"task": "test"},
        ttl=ttl,
        **kwargs,
    )


class TestExpireMessages:
    def test_expires_past_due(self, bus, cleanup):
        _send(bus, ttl=timedelta(seconds=-1))
        _send(bus, ttl=timedelta(seconds=-1))
        count = cleanup.expire_messages()
        assert count == 2

    def test_does_not_expire_future(self, bus, cleanup):
        _send(bus, ttl=timedelta(hours=1))
        count = cleanup.expire_messages()
        assert count == 0

    def test_does_not_expire_no_ttl(self, bus, cleanup):
        _send(bus, ttl=None)
        count = cleanup.expire_messages()
        assert count == 0

    def test_does_not_expire_already_expired(self, bus, cleanup):
        _send(bus, ttl=timedelta(seconds=-1))
        cleanup.expire_messages()
        count = cleanup.expire_messages()  # second run
        assert count == 0

    def test_does_not_expire_read(self, bus, cleanup):
        mid = _send(bus, ttl=timedelta(seconds=-1))
        # Force to read via SQL (bypass normal expiry check)
        bus._conn.execute(
            "UPDATE messages SET status = 'read' WHERE message_id = ?", (mid,)
        )
        bus._conn.commit()
        count = cleanup.expire_messages()
        assert count == 0

    def test_expires_delivered_messages(self, bus, cleanup):
        mid = _send(bus, ttl=timedelta(seconds=-1))
        # Force to delivered via SQL
        bus._conn.execute(
            "UPDATE messages SET status = 'delivered' WHERE message_id = ?", (mid,)
        )
        bus._conn.commit()
        count = cleanup.expire_messages()
        assert count == 1


class TestArchiveExpired:
    def test_archives_expired_messages(self, bus, cleanup):
        _send(bus, ttl=timedelta(seconds=-1))
        _send(bus, ttl=timedelta(seconds=-1))
        cleanup.expire_messages()
        count = cleanup.archive_expired()
        assert count == 2

    def test_removes_from_messages_table(self, bus, cleanup):
        mid = _send(bus, ttl=timedelta(seconds=-1))
        cleanup.expire_messages()
        cleanup.archive_expired()
        # Should not be in messages table
        stats = bus.get_stats()
        assert stats["total"] == 0

    def test_adds_to_archive_table(self, bus, cleanup):
        _send(bus, ttl=timedelta(seconds=-1))
        cleanup.expire_messages()
        cleanup.archive_expired()
        assert cleanup.get_archived_count() == 1

    def test_no_expired_to_archive(self, bus, cleanup):
        _send(bus, ttl=timedelta(hours=1))
        count = cleanup.archive_expired()
        assert count == 0

    def test_archived_message_has_archived_at(self, bus, cleanup):
        _send(bus, ttl=timedelta(seconds=-1))
        cleanup.expire_messages()
        cleanup.archive_expired()
        archived = cleanup.get_archived_messages()
        assert len(archived) == 1
        assert archived[0]["archived_at"] is not None


class TestCleanup:
    def test_combined_expire_and_archive(self, bus, cleanup):
        _send(bus, ttl=timedelta(seconds=-1))
        _send(bus, ttl=timedelta(seconds=-1))
        _send(bus, ttl=timedelta(hours=1))
        result = cleanup.cleanup()
        assert result["expired"] == 2
        assert result["archived"] == 2
        assert bus.get_stats()["total"] == 1

    def test_cleanup_idempotent(self, bus, cleanup):
        _send(bus, ttl=timedelta(seconds=-1))
        cleanup.cleanup()
        result = cleanup.cleanup()
        assert result["expired"] == 0
        assert result["archived"] == 0


class TestGetArchivedCount:
    def test_zero_initially(self, cleanup):
        assert cleanup.get_archived_count() == 0

    def test_counts_after_archive(self, bus, cleanup):
        for _ in range(3):
            _send(bus, ttl=timedelta(seconds=-1))
        cleanup.cleanup()
        assert cleanup.get_archived_count() == 3


class TestGetArchivedMessages:
    def test_returns_archived(self, bus, cleanup):
        _send(bus, ttl=timedelta(seconds=-1), correlation_id="corr-test")
        cleanup.cleanup()
        msgs = cleanup.get_archived_messages()
        assert len(msgs) == 1
        assert msgs[0]["correlation_id"] == "corr-test"

    def test_filter_by_correlation(self, bus, cleanup):
        _send(bus, ttl=timedelta(seconds=-1), correlation_id="corr-a")
        _send(bus, ttl=timedelta(seconds=-1), correlation_id="corr-b")
        cleanup.cleanup()
        msgs = cleanup.get_archived_messages(correlation_id="corr-a")
        assert len(msgs) == 1

    def test_limit(self, bus, cleanup):
        for _ in range(5):
            _send(bus, ttl=timedelta(seconds=-1))
        cleanup.cleanup()
        msgs = cleanup.get_archived_messages(limit=3)
        assert len(msgs) == 3

    def test_empty_when_none_archived(self, cleanup):
        msgs = cleanup.get_archived_messages()
        assert msgs == []
