"""Tests for integration delegation models and exceptions."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from core.integration.delegation import (
    ChainStatus,
    DelegationChain,
    DelegationHop,
    HopStatus,
)
from core.integration.exceptions import (
    ChainAlreadyComplete,
    ChainNotFound,
    CircularDelegation,
    DelegationTimeout,
    IntegrationError,
    InvalidDelegation,
)


# ======================================================================
# TestChainStatus
# ======================================================================


class TestChainStatus:
    def test_pending_value(self):
        assert ChainStatus.PENDING == "pending"

    def test_active_value(self):
        assert ChainStatus.ACTIVE == "active"

    def test_completed_value(self):
        assert ChainStatus.COMPLETED == "completed"

    def test_failed_value(self):
        assert ChainStatus.FAILED == "failed"

    def test_expired_value(self):
        assert ChainStatus.EXPIRED == "expired"

    def test_is_string(self):
        assert isinstance(ChainStatus.PENDING, str)

    def test_count(self):
        assert len(ChainStatus) == 5


# ======================================================================
# TestHopStatus
# ======================================================================


class TestHopStatus:
    def test_pending_value(self):
        assert HopStatus.PENDING == "pending"

    def test_delegated_value(self):
        assert HopStatus.DELEGATED == "delegated"

    def test_working_value(self):
        assert HopStatus.WORKING == "working"

    def test_completed_value(self):
        assert HopStatus.COMPLETED == "completed"

    def test_failed_value(self):
        assert HopStatus.FAILED == "failed"

    def test_is_string(self):
        assert isinstance(HopStatus.PENDING, str)

    def test_count(self):
        assert len(HopStatus) == 5


# ======================================================================
# TestDelegationHop
# ======================================================================


class TestDelegationHop:
    def test_default_values(self):
        hop = DelegationHop()
        assert hop.from_profile == ""
        assert hop.to_profile == ""
        assert hop.status == HopStatus.PENDING
        assert hop.message_id is None
        assert hop.delegated_at is None
        assert hop.completed_at is None

    def test_custom_values(self):
        hop = DelegationHop(from_profile="ceo", to_profile="cto")
        assert hop.from_profile == "ceo"
        assert hop.to_profile == "cto"
        assert hop.status == HopStatus.PENDING

    def test_mark_delegated(self):
        hop = DelegationHop(from_profile="ceo", to_profile="cto")
        hop.mark_delegated("msg-test123456")

        assert hop.status == HopStatus.DELEGATED
        assert hop.message_id == "msg-test123456"
        assert hop.delegated_at is not None
        assert isinstance(hop.delegated_at, datetime)
        assert hop.delegated_at.tzinfo is not None

    def test_mark_working(self):
        hop = DelegationHop(from_profile="ceo", to_profile="cto")
        hop.mark_working()
        assert hop.status == HopStatus.WORKING

    def test_mark_completed(self):
        hop = DelegationHop(from_profile="ceo", to_profile="cto")
        hop.mark_completed()

        assert hop.status == HopStatus.COMPLETED
        assert hop.completed_at is not None
        assert isinstance(hop.completed_at, datetime)
        assert hop.completed_at.tzinfo is not None

    def test_mark_failed(self):
        hop = DelegationHop(from_profile="ceo", to_profile="cto")
        hop.mark_failed()

        assert hop.status == HopStatus.FAILED
        assert hop.completed_at is not None
        assert isinstance(hop.completed_at, datetime)
        assert hop.completed_at.tzinfo is not None

    def test_to_dict(self):
        hop = DelegationHop(from_profile="ceo", to_profile="cto")
        hop.mark_delegated("msg-abc123")
        d = hop.to_dict()

        assert d["from_profile"] == "ceo"
        assert d["to_profile"] == "cto"
        assert d["status"] == "delegated"
        assert d["message_id"] == "msg-abc123"
        assert d["delegated_at"] is not None
        assert d["completed_at"] is None

    def test_from_dict(self):
        data = {
            "from_profile": "ceo",
            "to_profile": "cto",
            "status": "working",
            "message_id": "msg-xyz789",
            "delegated_at": "2025-01-15T10:30:00+00:00",
            "completed_at": None,
        }
        hop = DelegationHop.from_dict(data)

        assert hop.from_profile == "ceo"
        assert hop.to_profile == "cto"
        assert hop.status == HopStatus.WORKING
        assert hop.message_id == "msg-xyz789"
        assert hop.delegated_at is not None
        assert hop.completed_at is None

    def test_to_dict_from_dict_round_trip(self):
        hop = DelegationHop(from_profile="cto", to_profile="pm")
        hop.mark_delegated("msg-round123")
        hop.mark_completed()

        d = hop.to_dict()
        restored = DelegationHop.from_dict(d)

        assert restored.from_profile == hop.from_profile
        assert restored.to_profile == hop.to_profile
        assert restored.status == hop.status
        assert restored.message_id == hop.message_id
        assert restored.delegated_at == hop.delegated_at
        assert restored.completed_at == hop.completed_at


# ======================================================================
# TestDelegationChain
# ======================================================================


class TestDelegationChain:
    def test_default_values(self):
        chain = DelegationChain()

        assert chain.chain_id.startswith("chain-")
        assert chain.task_description == ""
        assert chain.originator == ""
        assert chain.status == ChainStatus.PENDING
        assert chain.hops == []
        assert chain.workers == []
        assert chain.created_at is not None
        assert isinstance(chain.created_at, datetime)
        assert chain.completed_at is None

    def test_chain_id_auto_generated(self):
        """Each chain gets a unique auto-generated ID."""
        ids = {DelegationChain().chain_id for _ in range(100)}
        assert len(ids) == 100

    def test_created_at_auto_set(self):
        """created_at is automatically set to current UTC time."""
        before = datetime.now(timezone.utc)
        chain = DelegationChain()
        after = datetime.now(timezone.utc)
        assert before <= chain.created_at <= after

    def test_add_hop(self):
        chain = DelegationChain(originator="ceo")
        hop = chain.add_hop("ceo", "cto")

        assert len(chain.hops) == 1
        assert hop.from_profile == "ceo"
        assert hop.to_profile == "cto"
        assert hop.status == HopStatus.PENDING
        assert chain.hops[0] is hop

    def test_add_multiple_hops(self):
        chain = DelegationChain(originator="ceo")
        chain.add_hop("ceo", "cto")
        chain.add_hop("cto", "pm")

        assert len(chain.hops) == 2
        assert chain.hops[0].from_profile == "ceo"
        assert chain.hops[1].from_profile == "cto"

    def test_current_hop_returns_latest_active(self):
        chain = DelegationChain(originator="ceo")
        hop1 = chain.add_hop("ceo", "cto")
        hop2 = chain.add_hop("cto", "pm")

        hop1.mark_delegated("msg-1")
        hop2.mark_delegated("msg-2")

        # Latest active (non-pending) hop is hop2
        current = chain.current_hop()
        assert current is hop2

    def test_current_hop_returns_first_active_when_second_pending(self):
        chain = DelegationChain(originator="ceo")
        hop1 = chain.add_hop("ceo", "cto")
        hop2 = chain.add_hop("cto", "pm")

        hop1.mark_delegated("msg-1")
        # hop2 remains PENDING

        current = chain.current_hop()
        assert current is hop1

    def test_current_hop_returns_none_when_all_pending(self):
        chain = DelegationChain(originator="ceo")
        chain.add_hop("ceo", "cto")
        chain.add_hop("cto", "pm")

        assert chain.current_hop() is None

    def test_current_hop_returns_none_when_no_hops(self):
        chain = DelegationChain()
        assert chain.current_hop() is None

    def test_add_worker(self):
        chain = DelegationChain(originator="ceo")
        chain.add_worker("cto")

        assert "cto" in chain.workers
        assert len(chain.workers) == 1

    def test_add_worker_no_duplicates(self):
        chain = DelegationChain(originator="ceo")
        chain.add_worker("cto")
        chain.add_worker("cto")

        assert chain.workers.count("cto") == 1

    def test_add_multiple_workers(self):
        chain = DelegationChain(originator="ceo")
        chain.add_worker("cto")
        chain.add_worker("pm")

        assert chain.workers == ["cto", "pm"]

    def test_activate(self):
        chain = DelegationChain()
        chain.activate()
        assert chain.status == ChainStatus.ACTIVE

    def test_activate_from_pending(self):
        chain = DelegationChain()
        assert chain.status == ChainStatus.PENDING
        chain.activate()
        assert chain.status == ChainStatus.ACTIVE

    def test_activate_when_completed_raises(self):
        chain = DelegationChain()
        chain.complete()
        with pytest.raises(ChainAlreadyComplete):
            chain.activate()

    def test_activate_when_failed_raises(self):
        chain = DelegationChain()
        chain.fail()
        with pytest.raises(ChainAlreadyComplete):
            chain.activate()

    def test_activate_when_expired_raises(self):
        chain = DelegationChain()
        chain.expire()
        with pytest.raises(ChainAlreadyComplete):
            chain.activate()

    def test_complete(self):
        chain = DelegationChain()
        chain.activate()
        chain.complete()

        assert chain.status == ChainStatus.COMPLETED
        assert chain.completed_at is not None
        assert isinstance(chain.completed_at, datetime)

    def test_complete_when_already_completed_raises(self):
        chain = DelegationChain()
        chain.complete()
        with pytest.raises(ChainAlreadyComplete):
            chain.complete()

    def test_fail(self):
        chain = DelegationChain()
        chain.fail()

        assert chain.status == ChainStatus.FAILED
        assert chain.completed_at is not None

    def test_expire(self):
        chain = DelegationChain()
        chain.expire()

        assert chain.status == ChainStatus.EXPIRED
        assert chain.completed_at is not None

    def test_is_terminal_true_for_completed(self):
        chain = DelegationChain()
        chain.complete()
        assert chain.is_terminal is True

    def test_is_terminal_true_for_failed(self):
        chain = DelegationChain()
        chain.fail()
        assert chain.is_terminal is True

    def test_is_terminal_true_for_expired(self):
        chain = DelegationChain()
        chain.expire()
        assert chain.is_terminal is True

    def test_is_terminal_false_for_pending(self):
        chain = DelegationChain()
        assert chain.is_terminal is False

    def test_is_terminal_false_for_active(self):
        chain = DelegationChain()
        chain.activate()
        assert chain.is_terminal is False

    def test_to_dict(self):
        chain = DelegationChain(
            task_description="Fix the bug",
            originator="ceo",
        )
        chain.add_hop("ceo", "cto")
        chain.add_worker("cto")

        d = chain.to_dict()

        assert d["chain_id"] == chain.chain_id
        assert d["task_description"] == "Fix the bug"
        assert d["originator"] == "ceo"
        assert d["status"] == "pending"
        assert len(d["hops"]) == 1
        assert d["workers"] == ["cto"]
        assert d["created_at"] is not None
        assert d["completed_at"] is None

    def test_to_dict_round_trip(self):
        chain = DelegationChain(
            task_description="Deploy feature",
            originator="ceo",
        )
        hop = chain.add_hop("ceo", "cto")
        hop.mark_delegated("msg-rt123")
        chain.add_worker("cto")
        chain.activate()

        d = chain.to_dict()
        restored = DelegationChain.from_dict(d)

        assert restored.chain_id == chain.chain_id
        assert restored.task_description == chain.task_description
        assert restored.originator == chain.originator
        assert restored.status == chain.status
        assert len(restored.hops) == len(chain.hops)
        assert restored.hops[0].from_profile == "ceo"
        assert restored.hops[0].to_profile == "cto"
        assert restored.hops[0].status == HopStatus.DELEGATED
        assert restored.hops[0].message_id == "msg-rt123"
        assert restored.workers == chain.workers
        assert restored.created_at == chain.created_at
        assert restored.completed_at == chain.completed_at

    def test_from_dict_with_hops(self):
        data = {
            "chain_id": "chain-test123456",
            "task_description": "Review code",
            "originator": "ceo",
            "status": "active",
            "hops": [
                {
                    "from_profile": "ceo",
                    "to_profile": "cto",
                    "status": "completed",
                    "message_id": "msg-hop1",
                    "delegated_at": "2025-01-15T10:00:00+00:00",
                    "completed_at": "2025-01-15T10:30:00+00:00",
                },
                {
                    "from_profile": "cto",
                    "to_profile": "pm",
                    "status": "working",
                    "message_id": "msg-hop2",
                    "delegated_at": "2025-01-15T10:35:00+00:00",
                    "completed_at": None,
                },
            ],
            "workers": ["cto", "pm"],
            "created_at": "2025-01-15T09:00:00+00:00",
            "completed_at": None,
        }

        chain = DelegationChain.from_dict(data)

        assert chain.chain_id == "chain-test123456"
        assert chain.task_description == "Review code"
        assert chain.originator == "ceo"
        assert chain.status == ChainStatus.ACTIVE
        assert len(chain.hops) == 2
        assert chain.hops[0].status == HopStatus.COMPLETED
        assert chain.hops[0].from_profile == "ceo"
        assert chain.hops[0].to_profile == "cto"
        assert chain.hops[0].message_id == "msg-hop1"
        assert chain.hops[0].delegated_at is not None
        assert chain.hops[0].completed_at is not None
        assert chain.hops[1].status == HopStatus.WORKING
        assert chain.hops[1].from_profile == "cto"
        assert chain.hops[1].to_profile == "pm"
        assert chain.hops[1].completed_at is None
        assert chain.workers == ["cto", "pm"]
        assert chain.completed_at is None

    def test_from_dict_minimal(self):
        """from_dict handles minimal data with defaults for optional fields."""
        data = {
            "chain_id": "chain-minimal",
            "status": "pending",
            "created_at": "2025-06-01T12:00:00+00:00",
        }
        chain = DelegationChain.from_dict(data)

        assert chain.chain_id == "chain-minimal"
        assert chain.task_description == ""
        assert chain.originator == ""
        assert chain.status == ChainStatus.PENDING
        assert chain.hops == []
        assert chain.workers == []


# ======================================================================
# TestExceptions
# ======================================================================


class TestExceptions:
    def test_integration_error_is_exception(self):
        assert issubclass(IntegrationError, Exception)
        err = IntegrationError("test error")
        assert str(err) == "test error"

    def test_chain_not_found(self):
        err = ChainNotFound("chain-abc123")
        assert err.chain_id == "chain-abc123"
        assert "chain-abc123" in str(err)

    def test_invalid_delegation(self):
        err = InvalidDelegation("bad target")
        assert "bad target" in str(err)

    def test_invalid_delegation_no_reason(self):
        err = InvalidDelegation()
        assert err.reason == ""
        assert str(err) == "Invalid delegation"

    def test_chain_already_complete(self):
        err = ChainAlreadyComplete("chain-done123")
        assert "chain-done123" in str(err)

    def test_delegation_timeout(self):
        err = DelegationTimeout("chain-slow123", timeout_seconds=30.0)
        assert err.chain_id == "chain-slow123"
        assert err.timeout_seconds == 30.0
        assert "chain-slow123" in str(err)
        assert "30.0s" in str(err)

    def test_delegation_timeout_no_seconds(self):
        err = DelegationTimeout("chain-timeout")
        assert err.timeout_seconds is None
        assert "chain-timeout" in str(err)

    def test_circular_delegation(self):
        err = CircularDelegation("cto already in chain-loop123")
        assert "cto" in str(err)
        assert "chain-loop123" in str(err)

    def test_all_inherit_from_integration_error(self):
        assert issubclass(ChainNotFound, IntegrationError)
        assert issubclass(InvalidDelegation, IntegrationError)
        assert issubclass(ChainAlreadyComplete, IntegrationError)
        assert issubclass(DelegationTimeout, IntegrationError)
        assert issubclass(CircularDelegation, IntegrationError)

    def test_exceptions_are_catchable_as_integration_error(self):
        """All subclasses can be caught with a single except IntegrationError."""
        exceptions = [
            ChainNotFound("chain-1"),
            InvalidDelegation("reason"),
            ChainAlreadyComplete("chain-2"),
            DelegationTimeout("chain-3"),
            CircularDelegation("profile in chain-4"),
        ]
        for exc in exceptions:
            with pytest.raises(IntegrationError):
                raise exc
