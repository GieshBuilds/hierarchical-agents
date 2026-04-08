"""Tests for the IPC MessageProtocol — higher-level patterns."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest

from core.ipc.exceptions import IPCError
from core.ipc.message_bus import MessageBus
from core.ipc.models import (
    Message,
    MessagePriority,
    MessageStatus,
    MessageType,
)
from core.ipc.protocol import MessageProtocol


# ------------------------------------------------------------------
# Mock profile registry helpers
# ------------------------------------------------------------------

class _MockProfile:
    """Minimal object exposing .parent_profile."""

    def __init__(self, name: str, parent_profile: str | None = None):
        self.name = name
        self.parent_profile = parent_profile


class _MockProfileRegistry:
    """Minimal registry that returns _MockProfile objects via .get()."""

    def __init__(self, profiles: dict[str, _MockProfile] | None = None):
        self._profiles = profiles or {}

    def add(self, profile: _MockProfile) -> None:
        self._profiles[profile.name] = profile

    def get(self, name: str) -> _MockProfile:
        if name not in self._profiles:
            raise KeyError(f"Profile not found: {name}")
        return self._profiles[name]


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def bus():
    """Create an in-memory MessageBus with no default TTL."""
    b = MessageBus(db_path=":memory:", default_ttl=None)
    yield b
    b.close()


@pytest.fixture
def protocol(bus):
    """Create a MessageProtocol backed by an in-memory bus."""
    return MessageProtocol(bus=bus)


@pytest.fixture
def registry():
    """Create a mock profile registry with a small hierarchy."""
    reg = _MockProfileRegistry()
    reg.add(_MockProfile("ceo", parent_profile=None))
    reg.add(_MockProfile("cto", parent_profile="ceo"))
    reg.add(_MockProfile("pm-alpha", parent_profile="cto"))
    reg.add(_MockProfile("dev-1", parent_profile="pm-alpha"))
    return reg


@pytest.fixture
def protocol_with_registry(bus, registry):
    """Protocol with a mock profile registry."""
    return MessageProtocol(bus=bus, profile_registry=registry)


# ------------------------------------------------------------------
# TestSendRequest
# ------------------------------------------------------------------

class TestSendRequest:
    """send_request() returns (message_id, correlation_id)."""

    def test_returns_tuple(self, protocol):
        result = protocol.send_request("ceo", "cto")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_message_id_prefix(self, protocol):
        mid, _ = protocol.send_request("ceo", "cto")
        assert mid.startswith("msg-")

    def test_correlation_id_prefix(self, protocol):
        _, corr_id = protocol.send_request("ceo", "cto")
        assert corr_id.startswith("corr-")

    def test_generates_unique_correlation_ids(self, protocol):
        ids = {protocol.send_request("ceo", "cto")[1] for _ in range(20)}
        assert len(ids) == 20

    def test_message_type_is_task_request(self, protocol, bus):
        mid, _ = protocol.send_request("ceo", "cto")
        msg = bus.get(mid)
        assert msg.message_type == MessageType.TASK_REQUEST

    def test_sets_from_profile(self, protocol, bus):
        mid, _ = protocol.send_request("ceo", "cto")
        msg = bus.get(mid)
        assert msg.from_profile == "ceo"

    def test_sets_to_profile(self, protocol, bus):
        mid, _ = protocol.send_request("ceo", "cto")
        msg = bus.get(mid)
        assert msg.to_profile == "cto"

    def test_sets_payload(self, protocol, bus):
        mid, _ = protocol.send_request("ceo", "cto", payload={"action": "deploy"})
        msg = bus.get(mid)
        assert msg.payload == {"action": "deploy"}

    def test_default_payload_is_empty(self, protocol, bus):
        mid, _ = protocol.send_request("ceo", "cto")
        msg = bus.get(mid)
        assert msg.payload == {}

    def test_sets_priority(self, protocol, bus):
        mid, _ = protocol.send_request(
            "ceo", "cto", priority=MessagePriority.URGENT
        )
        msg = bus.get(mid)
        assert msg.priority == MessagePriority.URGENT

    def test_default_priority_is_normal(self, protocol, bus):
        mid, _ = protocol.send_request("ceo", "cto")
        msg = bus.get(mid)
        assert msg.priority == MessagePriority.NORMAL

    def test_correlation_id_stored_on_message(self, protocol, bus):
        mid, corr_id = protocol.send_request("ceo", "cto")
        msg = bus.get(mid)
        assert msg.correlation_id == corr_id

    def test_message_status_is_pending(self, protocol, bus):
        mid, _ = protocol.send_request("ceo", "cto")
        msg = bus.get(mid)
        assert msg.status == MessageStatus.PENDING


# ------------------------------------------------------------------
# TestSendResponse
# ------------------------------------------------------------------

class TestSendResponse:
    """send_response() sends a TASK_RESPONSE linked to a correlation_id."""

    def test_returns_message_id(self, protocol):
        mid = protocol.send_response(
            correlation_id="corr-abc",
            from_profile="cto",
            to_profile="ceo",
        )
        assert mid.startswith("msg-")

    def test_message_type_is_task_response(self, protocol, bus):
        mid = protocol.send_response(
            correlation_id="corr-abc",
            from_profile="cto",
            to_profile="ceo",
        )
        msg = bus.get(mid)
        assert msg.message_type == MessageType.TASK_RESPONSE

    def test_links_correlation_id(self, protocol, bus):
        mid = protocol.send_response(
            correlation_id="corr-abc",
            from_profile="cto",
            to_profile="ceo",
        )
        msg = bus.get(mid)
        assert msg.correlation_id == "corr-abc"

    def test_correct_from_to(self, protocol, bus):
        mid = protocol.send_response(
            correlation_id="corr-abc",
            from_profile="cto",
            to_profile="ceo",
        )
        msg = bus.get(mid)
        assert msg.from_profile == "cto"
        assert msg.to_profile == "ceo"

    def test_sets_payload(self, protocol, bus):
        mid = protocol.send_response(
            correlation_id="corr-abc",
            from_profile="cto",
            to_profile="ceo",
            payload={"result": "success", "data": [1, 2, 3]},
        )
        msg = bus.get(mid)
        assert msg.payload == {"result": "success", "data": [1, 2, 3]}

    def test_sets_priority(self, protocol, bus):
        mid = protocol.send_response(
            correlation_id="corr-abc",
            from_profile="cto",
            to_profile="ceo",
            priority=MessagePriority.LOW,
        )
        msg = bus.get(mid)
        assert msg.priority == MessagePriority.LOW

    def test_request_response_share_correlation_id(self, protocol, bus):
        """End-to-end: request then response share the same corr ID."""
        req_mid, corr_id = protocol.send_request("ceo", "cto", payload={"q": 1})
        resp_mid = protocol.send_response(
            correlation_id=corr_id,
            from_profile="cto",
            to_profile="ceo",
            payload={"a": 42},
        )
        req_msg = bus.get(req_mid)
        resp_msg = bus.get(resp_mid)
        assert req_msg.correlation_id == resp_msg.correlation_id == corr_id


# ------------------------------------------------------------------
# TestSendBroadcast
# ------------------------------------------------------------------

class TestSendBroadcast:
    """send_broadcast() sends one message per recipient."""

    def test_returns_list_of_message_ids(self, protocol):
        mids = protocol.send_broadcast("ceo", ["cto", "pm-alpha", "dev-1"])
        assert isinstance(mids, list)
        assert len(mids) == 3
        for mid in mids:
            assert mid.startswith("msg-")

    def test_sends_to_each_profile(self, protocol, bus):
        mids = protocol.send_broadcast("ceo", ["cto", "pm-alpha"])
        recipients = {bus.get(mid).to_profile for mid in mids}
        assert recipients == {"cto", "pm-alpha"}

    def test_all_share_correlation_id(self, protocol, bus):
        mids = protocol.send_broadcast("ceo", ["cto", "pm-alpha", "dev-1"])
        corr_ids = {bus.get(mid).correlation_id for mid in mids}
        assert len(corr_ids) == 1
        assert corr_ids.pop().startswith("corr-")

    def test_message_type_is_broadcast(self, protocol, bus):
        mids = protocol.send_broadcast("ceo", ["cto", "pm-alpha"])
        for mid in mids:
            msg = bus.get(mid)
            assert msg.message_type == MessageType.BROADCAST

    def test_from_profile_set(self, protocol, bus):
        mids = protocol.send_broadcast("ceo", ["cto"])
        assert bus.get(mids[0]).from_profile == "ceo"

    def test_payload_set(self, protocol, bus):
        mids = protocol.send_broadcast(
            "ceo", ["cto", "pm-alpha"], payload={"announcement": "release!"}
        )
        for mid in mids:
            msg = bus.get(mid)
            assert msg.payload == {"announcement": "release!"}

    def test_empty_recipients_returns_empty(self, protocol):
        mids = protocol.send_broadcast("ceo", [])
        assert mids == []

    def test_single_recipient(self, protocol, bus):
        mids = protocol.send_broadcast("ceo", ["cto"])
        assert len(mids) == 1

    def test_sets_priority(self, protocol, bus):
        mids = protocol.send_broadcast(
            "ceo", ["cto"], priority=MessagePriority.URGENT
        )
        msg = bus.get(mids[0])
        assert msg.priority == MessagePriority.URGENT


# ------------------------------------------------------------------
# TestSendEscalation
# ------------------------------------------------------------------

class TestSendEscalation:
    """send_escalation() looks up parent via profile_registry."""

    def test_sends_to_parent(self, protocol_with_registry, bus):
        mid = protocol_with_registry.send_escalation(
            "cto", payload={"issue": "blocked"}
        )
        msg = bus.get(mid)
        assert msg.from_profile == "cto"
        assert msg.to_profile == "ceo"

    def test_message_type_is_escalation(self, protocol_with_registry, bus):
        mid = protocol_with_registry.send_escalation("cto")
        msg = bus.get(mid)
        assert msg.message_type == MessageType.ESCALATION

    def test_default_priority_is_urgent(self, protocol_with_registry, bus):
        mid = protocol_with_registry.send_escalation("cto")
        msg = bus.get(mid)
        assert msg.priority == MessagePriority.URGENT

    def test_custom_priority(self, protocol_with_registry, bus):
        mid = protocol_with_registry.send_escalation(
            "cto", priority=MessagePriority.NORMAL
        )
        msg = bus.get(mid)
        assert msg.priority == MessagePriority.NORMAL

    def test_sets_payload(self, protocol_with_registry, bus):
        mid = protocol_with_registry.send_escalation(
            "pm-alpha", payload={"reason": "deadline"}
        )
        msg = bus.get(mid)
        assert msg.payload == {"reason": "deadline"}
        # pm-alpha's parent is cto
        assert msg.to_profile == "cto"

    def test_deep_escalation(self, protocol_with_registry, bus):
        """dev-1 escalates to pm-alpha (its direct parent)."""
        mid = protocol_with_registry.send_escalation("dev-1")
        msg = bus.get(mid)
        assert msg.from_profile == "dev-1"
        assert msg.to_profile == "pm-alpha"

    def test_fails_without_registry(self, protocol):
        """Protocol with no registry cannot escalate."""
        with pytest.raises(IPCError, match="Profile registry required"):
            protocol.send_escalation("cto")

    def test_fails_when_no_parent(self, protocol_with_registry):
        """CEO has no parent — escalation should fail."""
        with pytest.raises(IPCError, match="has no parent"):
            protocol_with_registry.send_escalation("ceo")

    def test_fails_for_unknown_profile(self, protocol_with_registry):
        """Unknown profile raises IPCError."""
        with pytest.raises(IPCError, match="Failed to look up parent"):
            protocol_with_registry.send_escalation("nonexistent")


# ------------------------------------------------------------------
# TestWaitForResponse
# ------------------------------------------------------------------

class TestWaitForResponse:
    """wait_for_response() polls for a matching response."""

    def test_finds_existing_response(self, protocol, bus):
        """If the response already exists, return it immediately."""
        # Set up a request/response pair
        req_mid, corr_id = protocol.send_request("ceo", "cto", payload={"q": 1})
        resp_mid = protocol.send_response(
            correlation_id=corr_id,
            from_profile="cto",
            to_profile="ceo",
            payload={"a": 42},
        )

        result = protocol.wait_for_response(
            corr_id, "cto", timeout=1.0, poll_interval=0.05
        )
        assert result is not None
        assert result.message_id == resp_mid
        assert result.from_profile == "cto"
        assert result.message_type == MessageType.TASK_RESPONSE
        assert result.payload == {"a": 42}

    def test_returns_none_on_timeout(self, protocol):
        """No response exists — should time out and return None."""
        _, corr_id = protocol.send_request("ceo", "cto")
        result = protocol.wait_for_response(
            corr_id, "cto", timeout=0.1, poll_interval=0.02
        )
        assert result is None

    def test_returns_none_for_wrong_profile(self, protocol, bus):
        """Response from a different profile should not match."""
        _, corr_id = protocol.send_request("ceo", "cto")
        # Response from pm-alpha instead of cto
        protocol.send_response(
            correlation_id=corr_id,
            from_profile="pm-alpha",
            to_profile="ceo",
        )
        result = protocol.wait_for_response(
            corr_id, "cto", timeout=0.1, poll_interval=0.02
        )
        assert result is None

    def test_returns_none_for_non_response_type(self, protocol, bus):
        """A TASK_REQUEST with the same correlation_id should not match."""
        _, corr_id = protocol.send_request("ceo", "cto")
        # Send another request (not a response) with the same corr_id
        bus.send(
            from_profile="cto",
            to_profile="ceo",
            message_type=MessageType.TASK_REQUEST,
            correlation_id=corr_id,
        )
        result = protocol.wait_for_response(
            corr_id, "cto", timeout=0.1, poll_interval=0.02
        )
        assert result is None

    def test_timeout_duration_is_respected(self, protocol):
        """The wait should not run significantly longer than timeout."""
        _, corr_id = protocol.send_request("ceo", "cto")
        start = time.monotonic()
        protocol.wait_for_response(
            corr_id, "cto", timeout=0.15, poll_interval=0.03
        )
        elapsed = time.monotonic() - start
        # Should finish within a reasonable margin of timeout
        assert elapsed < 0.5

    def test_returns_none_for_unrelated_correlation(self, protocol, bus):
        """Response with a different correlation_id should not match."""
        _, corr_id = protocol.send_request("ceo", "cto")
        protocol.send_response(
            correlation_id="corr-unrelated",
            from_profile="cto",
            to_profile="ceo",
        )
        result = protocol.wait_for_response(
            corr_id, "cto", timeout=0.1, poll_interval=0.02
        )
        assert result is None


# ------------------------------------------------------------------
# TestGetConversation
# ------------------------------------------------------------------

class TestGetConversation:
    """get_conversation() returns the full message chain."""

    def test_returns_full_chain(self, protocol, bus):
        req_mid, corr_id = protocol.send_request("ceo", "cto", payload={"q": 1})
        resp_mid = protocol.send_response(
            correlation_id=corr_id,
            from_profile="cto",
            to_profile="ceo",
            payload={"a": 42},
        )
        chain = protocol.get_conversation(corr_id)
        assert len(chain) == 2
        ids = [m.message_id for m in chain]
        assert req_mid in ids
        assert resp_mid in ids

    def test_ordered_by_created_at(self, protocol, bus):
        req_mid, corr_id = protocol.send_request("ceo", "cto")
        resp_mid = protocol.send_response(
            correlation_id=corr_id,
            from_profile="cto",
            to_profile="ceo",
        )
        chain = protocol.get_conversation(corr_id)
        # Request was sent first
        assert chain[0].message_id == req_mid
        assert chain[1].message_id == resp_mid

    def test_multi_message_conversation(self, protocol, bus):
        """Multiple exchanges on the same correlation_id."""
        req_mid, corr_id = protocol.send_request("ceo", "cto")
        resp1_mid = protocol.send_response(
            correlation_id=corr_id,
            from_profile="cto",
            to_profile="ceo",
            payload={"status": "working"},
        )
        resp2_mid = protocol.send_response(
            correlation_id=corr_id,
            from_profile="cto",
            to_profile="ceo",
            payload={"status": "done"},
        )
        chain = protocol.get_conversation(corr_id)
        assert len(chain) == 3
        assert chain[0].message_id == req_mid
        assert chain[1].message_id == resp1_mid
        assert chain[2].message_id == resp2_mid

    def test_empty_for_unknown_correlation(self, protocol):
        chain = protocol.get_conversation("corr-nonexistent")
        assert chain == []

    def test_excludes_other_correlations(self, protocol, bus):
        _, corr_id_a = protocol.send_request("ceo", "cto")
        _, corr_id_b = protocol.send_request("ceo", "pm-alpha")
        chain_a = protocol.get_conversation(corr_id_a)
        chain_b = protocol.get_conversation(corr_id_b)
        assert len(chain_a) == 1
        assert len(chain_b) == 1
        assert chain_a[0].correlation_id == corr_id_a
        assert chain_b[0].correlation_id == corr_id_b

    def test_returns_message_objects(self, protocol):
        _, corr_id = protocol.send_request("ceo", "cto")
        chain = protocol.get_conversation(corr_id)
        assert all(isinstance(m, Message) for m in chain)

    def test_broadcast_conversation(self, protocol, bus):
        """Broadcast messages sharing a correlation_id form a conversation."""
        mids = protocol.send_broadcast(
            "ceo", ["cto", "pm-alpha"], payload={"notice": "update"}
        )
        # All broadcasts share the same corr_id
        corr_id = bus.get(mids[0]).correlation_id
        chain = protocol.get_conversation(corr_id)
        assert len(chain) == 2
        assert {m.message_id for m in chain} == set(mids)
