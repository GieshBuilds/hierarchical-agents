"""Edge-case integration tests for the hierarchical architecture.

Covers message queueing, TTL expiry, chain failure propagation, empty chains,
duplicate delegation across chains, status transitions, and worker sleep/resume.
"""
from __future__ import annotations

import os
import time
from datetime import timedelta

import pytest

from core.integration.orchestrator import ChainOrchestrator
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
    InvalidDelegation,
)
from core.ipc.cleanup import MessageCleanup
from core.ipc.models import MessagePriority, MessageStatus, MessageType
from core.ipc.message_bus import MessageBus
from core.registry.profile_registry import ProfileRegistry
from core.workers.models import SubagentStatus
from core.workers.subagent_registry import SubagentRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir(tmp_path):
    """Return a temp directory for database files."""
    return str(tmp_path)


@pytest.fixture
def registry(tmp_dir):
    """ProfileRegistry with CEO (hermes) → CTO/CMO → PMs."""
    db_path = os.path.join(tmp_dir, "registry.db")
    reg = ProfileRegistry(db_path)
    reg.create_profile(
        "cto", display_name="CTO", role="department_head",
        parent="hermes", department="engineering",
    )
    reg.create_profile(
        "cmo", display_name="CMO", role="department_head",
        parent="hermes", department="marketing",
    )
    reg.create_profile(
        "pm-alpha", display_name="PM Alpha", role="project_manager",
        parent="cto",
    )
    reg.create_profile(
        "pm-beta", display_name="PM Beta", role="project_manager",
        parent="cto",
    )
    reg.create_profile(
        "pm-mktg", display_name="PM Marketing", role="project_manager",
        parent="cmo",
    )
    return reg


@pytest.fixture
def bus(tmp_dir):
    """MessageBus without profile registry."""
    db_path = os.path.join(tmp_dir, "bus.db")
    return MessageBus(db_path)


@pytest.fixture
def worker_registries(tmp_dir):
    """Factory that creates per-PM SubagentRegistry instances."""
    _cache: dict[str, SubagentRegistry] = {}

    def factory(pm_name: str) -> SubagentRegistry:
        if pm_name not in _cache:
            db_path = os.path.join(tmp_dir, f"workers-{pm_name}.db")
            _cache[pm_name] = SubagentRegistry(db_path)
        return _cache[pm_name]

    return factory


@pytest.fixture
def orchestrator(registry, bus, worker_registries):
    """ChainOrchestrator wired to all subsystems."""
    return ChainOrchestrator(
        registry=registry,
        bus=bus,
        worker_registry_factory=worker_registries,
    )


# ===========================================================================
# Message Queueing — Send Then Poll
# ===========================================================================


class TestMessageQueueing:
    """Verify messages queue up and are retrievable by polling."""

    def test_send_then_poll_retrieves_message(self, bus):
        """Send a message to a profile, then poll to receive it."""
        msg_id = bus.send(
            from_profile="hermes",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "Review architecture"},
        )
        messages = bus.poll("cto")
        assert len(messages) >= 1
        found = [m for m in messages if m.message_id == msg_id]
        assert len(found) == 1
        assert found[0].payload["task"] == "Review architecture"

    def test_multiple_messages_queue_order(self, bus):
        """Messages are returned in priority then FIFO order."""
        bus.send(
            from_profile="hermes",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "Low priority"},
            priority=MessagePriority.LOW,
        )
        bus.send(
            from_profile="hermes",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "Urgent fix"},
            priority=MessagePriority.URGENT,
        )
        bus.send(
            from_profile="hermes",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "Normal task"},
            priority=MessagePriority.NORMAL,
        )

        messages = bus.poll("cto")
        assert len(messages) == 3
        # Urgent first, then normal, then low
        assert messages[0].payload["task"] == "Urgent fix"
        assert messages[1].payload["task"] == "Normal task"
        assert messages[2].payload["task"] == "Low priority"

    def test_poll_only_returns_target_profile_messages(self, bus):
        """Poll only returns messages addressed to the specified profile."""
        bus.send(
            from_profile="hermes",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "For CTO"},
        )
        bus.send(
            from_profile="hermes",
            to_profile="cmo",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "For CMO"},
        )

        cto_msgs = bus.poll("cto")
        cmo_msgs = bus.poll("cmo")

        assert all(m.to_profile == "cto" for m in cto_msgs)
        assert all(m.to_profile == "cmo" for m in cmo_msgs)
        assert len(cto_msgs) == 1
        assert len(cmo_msgs) == 1

    def test_delegation_sends_pollable_message(self, orchestrator, bus):
        """When orchestrator delegates, the message is pollable by recipient."""
        chain = orchestrator.create_chain("Build API", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")

        messages = bus.poll("cto")
        assert len(messages) >= 1
        task_msgs = [m for m in messages if m.message_type == MessageType.TASK_REQUEST]
        assert len(task_msgs) == 1
        assert task_msgs[0].payload["task"] == "Build API"
        assert task_msgs[0].payload["chain_id"] == chain.chain_id


# ===========================================================================
# Message Expiry & Cleanup
# ===========================================================================


class TestMessageExpiry:
    """Test message TTL expiry and cleanup via MessageCleanup."""

    def test_message_expires_after_short_ttl(self, bus):
        """Send a message with a very short TTL, wait, then verify cleanup."""
        msg_id = bus.send(
            from_profile="hermes",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "Ephemeral task"},
            ttl=timedelta(seconds=1),
        )

        # Immediately pollable
        messages = bus.poll("cto")
        assert any(m.message_id == msg_id for m in messages)

        # Wait for TTL to expire
        time.sleep(1.5)

        # After expiry, poll (without include_expired) should not return it
        messages_after = bus.poll("cto")
        assert all(m.message_id != msg_id for m in messages_after)

    def test_message_cleanup_expires_and_archives(self, bus):
        """MessageCleanup.cleanup() marks expired messages and archives them."""
        # Send with short TTL
        msg_id = bus.send(
            from_profile="hermes",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "Will expire"},
            ttl=timedelta(seconds=1),
        )

        time.sleep(1.5)

        cleanup = MessageCleanup(bus)
        result = cleanup.cleanup()

        assert result["expired"] >= 1
        assert result["archived"] >= 1

        # Verify archived count
        assert cleanup.get_archived_count() >= 1

    def test_cleanup_does_nothing_when_no_expired(self, bus):
        """Cleanup with no expired messages returns zero counts."""
        bus.send(
            from_profile="hermes",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "Long-lived"},
            ttl=None,  # Never expires
        )

        cleanup = MessageCleanup(bus)
        result = cleanup.cleanup()
        assert result["expired"] == 0
        assert result["archived"] == 0

    def test_message_with_no_ttl_never_expires(self, bus):
        """A message with ttl=None never expires."""
        msg_id = bus.send(
            from_profile="hermes",
            to_profile="cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "Permanent task"},
            ttl=None,
        )

        time.sleep(0.5)

        cleanup = MessageCleanup(bus)
        expired_count = cleanup.expire_messages()
        assert expired_count == 0

        messages = bus.poll("cto")
        assert any(m.message_id == msg_id for m in messages)


# ===========================================================================
# Chain Failure Propagation
# ===========================================================================


class TestChainFailurePropagation:
    """Test failure propagation through delegation chains."""

    def test_fail_chain_marks_all_hops_failed(self, orchestrator):
        """fail_chain marks all hops as FAILED."""
        chain = orchestrator.create_chain("Doomed task", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        assert len(chain.hops) == 2

        orchestrator.fail_chain(chain, "Budget exceeded")
        assert chain.status == ChainStatus.FAILED
        for hop in chain.hops:
            assert hop.status == HopStatus.FAILED

    def test_fail_chain_sends_error_to_originator(self, orchestrator, bus):
        """Failing a chain sends error response messages up the chain."""
        chain = orchestrator.create_chain("Failing task", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        orchestrator.fail_chain(chain, "Resource unavailable")

        responses = bus.poll("hermes", message_type=MessageType.TASK_RESPONSE)
        assert len(responses) >= 1
        assert any("Resource unavailable" in str(m.payload) for m in responses)

    def test_fail_multi_hop_chain_sends_errors_to_all_intermediaries(
        self, orchestrator, bus
    ):
        """Failing a multi-hop chain sends error responses to all upstream profiles."""
        chain = orchestrator.create_chain("Multi-hop failure", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        orchestrator.fail_chain(chain, "Worker crashed")

        # Both CEO and CTO should have error responses
        ceo_msgs = bus.poll("hermes", message_type=MessageType.TASK_RESPONSE)
        cto_msgs = bus.poll("cto", message_type=MessageType.TASK_RESPONSE)
        assert len(ceo_msgs) >= 1
        assert len(cto_msgs) >= 1

    def test_cannot_delegate_after_fail(self, orchestrator):
        """Cannot delegate on a failed chain."""
        chain = orchestrator.create_chain("Task", "hermes")
        chain.fail()
        with pytest.raises(ChainAlreadyComplete):
            orchestrator.delegate(chain, "hermes", "cto")

    def test_cannot_spawn_worker_after_fail(self, orchestrator):
        """Cannot spawn worker on a failed chain."""
        chain = orchestrator.create_chain("Task", "hermes")
        chain.fail()
        with pytest.raises(ChainAlreadyComplete):
            orchestrator.spawn_worker(chain, "pm-alpha", "Work")


# ===========================================================================
# Empty Chain Propagation
# ===========================================================================


class TestEmptyChainPropagation:
    """Test delegate_down_chain with no hops (target is the originator itself)."""

    def test_delegate_down_chain_to_self_returns_empty(self, orchestrator):
        """delegate_down_chain to the originator returns empty hop list."""
        chain = orchestrator.create_chain("Self task", "hermes")
        hops = orchestrator.delegate_down_chain(chain, "hermes")
        assert hops == []
        # Chain stays pending because no hops were created
        assert chain.status == ChainStatus.PENDING

    def test_delegate_down_chain_single_hop(self, orchestrator):
        """CEO → CTO is a single hop."""
        chain = orchestrator.create_chain("Direct delegation", "hermes")
        hops = orchestrator.delegate_down_chain(chain, "cto")
        assert len(hops) == 1
        assert hops[0].from_profile == "hermes"
        assert hops[0].to_profile == "cto"


# ===========================================================================
# Duplicate Profile Delegation Across Chains
# ===========================================================================


class TestDuplicateProfileAcrossChains:
    """Delegate to the same profile in different chains — should work fine."""

    def test_same_target_in_two_chains(self, orchestrator):
        """Two independent chains can both delegate to the CTO."""
        chain1 = orchestrator.create_chain("Task A", "hermes")
        chain2 = orchestrator.create_chain("Task B", "hermes")

        hop1 = orchestrator.delegate(chain1, "hermes", "cto")
        hop2 = orchestrator.delegate(chain2, "hermes", "cto")

        assert hop1.to_profile == "cto"
        assert hop2.to_profile == "cto"
        assert chain1.status == ChainStatus.ACTIVE
        assert chain2.status == ChainStatus.ACTIVE

    def test_same_pm_in_multiple_chains(self, orchestrator):
        """Multiple chains can delegate to pm-alpha independently."""
        chains = []
        for i in range(3):
            c = orchestrator.create_chain(f"Task {i}", "hermes")
            orchestrator.delegate_down_chain(c, "pm-alpha")
            chains.append(c)

        for c in chains:
            assert c.status == ChainStatus.ACTIVE
            pm_hop = [h for h in c.hops if h.to_profile == "pm-alpha"]
            assert len(pm_hop) == 1

    def test_same_profile_in_same_chain_raises_circular(self, orchestrator):
        """Delegating to the same profile twice in the same chain raises CircularDelegation."""
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        with pytest.raises(CircularDelegation):
            orchestrator.delegate(chain, "hermes", "cto")


# ===========================================================================
# Chain Status Transitions
# ===========================================================================


class TestChainStatusTransitions:
    """Test status transition rules on DelegationChain."""

    def test_pending_to_active(self):
        """Chain can go from PENDING to ACTIVE."""
        chain = DelegationChain(task_description="Test", originator="hermes")
        assert chain.status == ChainStatus.PENDING
        chain.activate()
        assert chain.status == ChainStatus.ACTIVE

    def test_active_to_completed(self):
        """Chain can go from ACTIVE to COMPLETED."""
        chain = DelegationChain(task_description="Test", originator="hermes")
        chain.activate()
        chain.complete()
        assert chain.status == ChainStatus.COMPLETED
        assert chain.completed_at is not None

    def test_pending_to_completed(self):
        """Chain can go from PENDING to COMPLETED directly."""
        chain = DelegationChain(task_description="Test", originator="hermes")
        chain.complete()
        assert chain.status == ChainStatus.COMPLETED

    def test_pending_to_failed(self):
        """Chain can go from PENDING to FAILED."""
        chain = DelegationChain(task_description="Test", originator="hermes")
        chain.fail()
        assert chain.status == ChainStatus.FAILED

    def test_completed_chain_is_terminal(self):
        """A completed chain is terminal — cannot activate, complete, or fail again."""
        chain = DelegationChain(task_description="Test", originator="hermes")
        chain.complete()
        assert chain.is_terminal is True
        with pytest.raises(ChainAlreadyComplete):
            chain.activate()
        with pytest.raises(ChainAlreadyComplete):
            chain.complete()

    def test_failed_chain_is_terminal(self):
        """A failed chain is terminal."""
        chain = DelegationChain(task_description="Test", originator="hermes")
        chain.fail()
        assert chain.is_terminal is True
        with pytest.raises(ChainAlreadyComplete):
            chain.activate()

    def test_expired_chain_is_terminal(self):
        """An expired chain is terminal."""
        chain = DelegationChain(task_description="Test", originator="hermes")
        chain.expire()
        assert chain.is_terminal is True
        assert chain.status == ChainStatus.EXPIRED
        with pytest.raises(ChainAlreadyComplete):
            chain.activate()

    def test_hop_status_transitions(self):
        """DelegationHop transitions through the correct states."""
        hop = DelegationHop(from_profile="hermes", to_profile="cto")
        assert hop.status == HopStatus.PENDING

        hop.mark_delegated("msg-123")
        assert hop.status == HopStatus.DELEGATED
        assert hop.message_id == "msg-123"

        hop.mark_working()
        assert hop.status == HopStatus.WORKING

        hop.mark_completed()
        assert hop.status == HopStatus.COMPLETED
        assert hop.completed_at is not None


# ===========================================================================
# Worker Sleep and Resume
# ===========================================================================


class TestWorkerSleepResume:
    """Test SubagentRegistry sleep() and resume via update_status()."""

    def test_sleep_running_worker(self, worker_registries):
        """A running worker can be put to sleep."""
        wreg = worker_registries("pm-alpha")
        subagent = wreg.register(
            project_manager="pm-alpha",
            task_goal="Build feature X",
        )
        assert subagent.status == SubagentStatus.RUNNING.value

        slept = wreg.sleep(subagent.subagent_id, project_manager="pm-alpha")
        assert slept.status == SubagentStatus.SLEEPING.value

    def test_resume_sleeping_worker(self, worker_registries):
        """A sleeping worker can resume (update_status → running)."""
        wreg = worker_registries("pm-beta")
        subagent = wreg.register(
            project_manager="pm-beta",
            task_goal="Long task",
        )

        # Sleep
        wreg.sleep(subagent.subagent_id, project_manager="pm-beta")
        # Resume
        resumed = wreg.update_status(
            subagent.subagent_id,
            SubagentStatus.RUNNING,
            project_manager="pm-beta",
        )
        assert resumed.status == SubagentStatus.RUNNING.value

    def test_sleep_and_resume_preserves_task_goal(self, worker_registries):
        """Task goal is preserved across sleep/resume cycle."""
        wreg = worker_registries("pm-alpha")
        subagent = wreg.register(
            project_manager="pm-alpha",
            task_goal="Important task",
        )

        wreg.sleep(subagent.subagent_id, project_manager="pm-alpha")
        resumed = wreg.update_status(
            subagent.subagent_id,
            SubagentStatus.RUNNING,
            project_manager="pm-alpha",
        )
        assert resumed.task_goal == "Important task"

    def test_cannot_sleep_completed_worker(self, worker_registries):
        """A completed worker cannot be put to sleep."""
        from core.workers.exceptions import InvalidSubagentStatus

        wreg = worker_registries("pm-alpha")
        subagent = wreg.register(
            project_manager="pm-alpha",
            task_goal="Quick task",
        )
        wreg.complete(
            subagent.subagent_id,
            result_summary="All done",
            project_manager="pm-alpha",
        )
        with pytest.raises(InvalidSubagentStatus):
            wreg.sleep(subagent.subagent_id, project_manager="pm-alpha")

    def test_worker_lifecycle_full(self, worker_registries):
        """Full worker lifecycle: running → sleeping → running → completed → archived."""
        wreg = worker_registries("pm-alpha")
        sa = wreg.register(
            project_manager="pm-alpha",
            task_goal="Full lifecycle task",
        )
        assert sa.status == SubagentStatus.RUNNING.value

        # Sleep
        sa = wreg.sleep(sa.subagent_id, project_manager="pm-alpha")
        assert sa.status == SubagentStatus.SLEEPING.value

        # Resume
        sa = wreg.update_status(
            sa.subagent_id, SubagentStatus.RUNNING, project_manager="pm-alpha",
        )
        assert sa.status == SubagentStatus.RUNNING.value

        # Complete
        sa = wreg.complete(
            sa.subagent_id,
            result_summary="Task completed",
            project_manager="pm-alpha",
        )
        assert sa.status == SubagentStatus.COMPLETED.value

        # Archive
        sa = wreg.archive(sa.subagent_id, project_manager="pm-alpha")
        assert sa.status == SubagentStatus.ARCHIVED.value

    def test_orchestrator_spawn_then_sleep_resume(
        self, orchestrator, worker_registries
    ):
        """Worker spawned via orchestrator can be slept and resumed via registry."""
        chain = orchestrator.create_chain("Feature build", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        sid = orchestrator.spawn_worker(chain, "pm-alpha", "Write code")

        wreg = worker_registries("pm-alpha")

        # Sleep
        slept = wreg.sleep(sid, project_manager="pm-alpha")
        assert slept.status == SubagentStatus.SLEEPING.value

        # Resume
        resumed = wreg.update_status(
            sid, SubagentStatus.RUNNING, project_manager="pm-alpha",
        )
        assert resumed.status == SubagentStatus.RUNNING.value

        # Complete via orchestrator
        orchestrator.complete_worker(chain, "pm-alpha", sid, "Code written")
        final = wreg.get(sid, project_manager="pm-alpha")
        assert final.status == SubagentStatus.COMPLETED.value
        assert final.result_summary == "Code written"


# ===========================================================================
# Miscellaneous Edge Cases
# ===========================================================================


class TestMiscEdgeCases:
    """Miscellaneous edge cases."""

    def test_get_nonexistent_chain_raises(self, orchestrator):
        """Getting a non-existent chain raises ChainNotFound."""
        with pytest.raises(ChainNotFound):
            orchestrator.get_chain("chain-doesnotexist")

    def test_list_chains_empty(self, orchestrator):
        """List chains when no chains exist returns empty list."""
        chains = orchestrator.list_chains()
        assert chains == []

    def test_delegate_to_non_direct_report_raises(self, orchestrator):
        """Delegating to a non-direct-report raises InvalidDelegation."""
        chain = orchestrator.create_chain("Task", "hermes")
        # pm-alpha is not a direct report of hermes (needs CTO in between)
        with pytest.raises(InvalidDelegation):
            orchestrator.delegate(chain, "hermes", "pm-alpha")

    def test_delegate_upward_raises(self, orchestrator):
        """Delegating upward (PM to CTO) raises InvalidDelegation."""
        chain = orchestrator.create_chain("Task", "pm-alpha")
        with pytest.raises(InvalidDelegation):
            orchestrator.delegate(chain, "pm-alpha", "cto")

    def test_delegate_cross_department_raises(self, orchestrator):
        """CTO cannot delegate to pm-mktg (wrong department path)."""
        chain = orchestrator.create_chain("Task", "cto")
        with pytest.raises(InvalidDelegation):
            orchestrator.delegate(chain, "cto", "pm-mktg")

    def test_complete_chain_then_propagate_raises(self, orchestrator):
        """Propagating result on a completed chain fails (chain is terminal)."""
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        orchestrator.propagate_result(chain, "First result")

        # Chain is now COMPLETED — propagating again should raise
        with pytest.raises(ChainAlreadyComplete):
            orchestrator.propagate_result(chain, "Second result")

    def test_chain_with_workers_can_complete(self, orchestrator, worker_registries):
        """A chain with workers can still propagate results normally."""
        chain = orchestrator.create_chain("Build module", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        sid = orchestrator.spawn_worker(chain, "pm-alpha", "Code it")
        orchestrator.complete_worker(chain, "pm-alpha", sid, "Module built")
        orchestrator.propagate_result(chain, "Module complete")
        assert chain.status == ChainStatus.COMPLETED
