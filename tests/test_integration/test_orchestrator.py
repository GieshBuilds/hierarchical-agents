"""Tests for ChainOrchestrator — delegation, worker management, result propagation."""
from __future__ import annotations

import os
import tempfile

import pytest

from core.integration.orchestrator import ChainOrchestrator
from core.integration.delegation import ChainStatus, DelegationChain, HopStatus
from core.integration.exceptions import (
    ChainAlreadyComplete,
    ChainNotFound,
    CircularDelegation,
    InvalidDelegation,
)
from core.ipc.models import MessagePriority, MessageType
from core.ipc.message_bus import MessageBus
from core.registry.profile_registry import ProfileRegistry
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
    """ProfileRegistry with CEO (hermes) → CTO → PM hierarchy.

    Note: ProfileRegistry auto-creates a CEO named 'hermes' on init.
    """
    db_path = os.path.join(tmp_dir, "registry.db")
    reg = ProfileRegistry(db_path)
    # 'hermes' CEO is auto-created by ProfileRegistry.__init__
    reg.create_profile("cto", display_name="CTO", role="department_head", parent="hermes", department="engineering")
    reg.create_profile("cmo", display_name="CMO", role="department_head", parent="hermes", department="marketing")
    reg.create_profile("pm-alpha", display_name="PM Alpha", role="project_manager", parent="cto")
    reg.create_profile("pm-beta", display_name="PM Beta", role="project_manager", parent="cto")
    reg.create_profile("pm-mktg", display_name="PM Marketing", role="project_manager", parent="cmo")
    return reg


@pytest.fixture
def bus(tmp_dir):
    """MessageBus without profile registry (orchestrator handles validation)."""
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
# Chain Management
# ===========================================================================

class TestChainManagement:
    """Tests for create_chain, get_chain, list_chains."""

    def test_create_chain(self, orchestrator):
        chain = orchestrator.create_chain("Build feature X", "hermes")
        assert chain.task_description == "Build feature X"
        assert chain.originator == "hermes"
        assert chain.status == ChainStatus.PENDING
        assert chain.chain_id.startswith("chain-")

    def test_get_chain(self, orchestrator):
        chain = orchestrator.create_chain("Task A", "hermes")
        retrieved = orchestrator.get_chain(chain.chain_id)
        assert retrieved.chain_id == chain.chain_id

    def test_get_chain_not_found(self, orchestrator):
        with pytest.raises(ChainNotFound):
            orchestrator.get_chain("nonexistent-chain")

    def test_list_chains_all(self, orchestrator):
        orchestrator.create_chain("Task 1", "hermes")
        orchestrator.create_chain("Task 2", "hermes")
        chains = orchestrator.list_chains()
        assert len(chains) == 2

    def test_list_chains_by_status(self, orchestrator):
        chain = orchestrator.create_chain("Task 1", "hermes")
        orchestrator.create_chain("Task 2", "hermes")
        chain.activate()
        pending = orchestrator.list_chains(status=ChainStatus.PENDING)
        active = orchestrator.list_chains(status=ChainStatus.ACTIVE)
        assert len(pending) == 1
        assert len(active) == 1

    def test_list_chains_by_originator(self, orchestrator, registry):
        orchestrator.create_chain("Task CEO", "hermes")
        orchestrator.create_chain("Task CTO", "cto")
        ceo_chains = orchestrator.list_chains(originator="hermes")
        assert len(ceo_chains) == 1
        assert ceo_chains[0].originator == "hermes"


# ===========================================================================
# Delegation
# ===========================================================================

class TestDelegation:
    """Tests for delegate and delegate_down_chain."""

    def test_delegate_single_hop(self, orchestrator):
        chain = orchestrator.create_chain("Review code", "hermes")
        hop = orchestrator.delegate(chain, "hermes", "cto")
        assert hop.from_profile == "hermes"
        assert hop.to_profile == "cto"
        assert hop.status == HopStatus.DELEGATED
        assert hop.message_id is not None
        assert chain.status == ChainStatus.ACTIVE

    def test_delegate_two_hops(self, orchestrator):
        chain = orchestrator.create_chain("Build API", "hermes")
        hop1 = orchestrator.delegate(chain, "hermes", "cto")
        hop2 = orchestrator.delegate(chain, "cto", "pm-alpha")
        assert len(chain.hops) == 2
        assert chain.hops[0].to_profile == "cto"
        assert chain.hops[1].to_profile == "pm-alpha"

    def test_delegate_creates_ipc_message(self, orchestrator, bus):
        chain = orchestrator.create_chain("Deploy service", "hermes")
        hop = orchestrator.delegate(chain, "hermes", "cto")
        # The message should be in the bus
        messages = bus.poll("cto")
        assert len(messages) == 1
        assert messages[0].message_type == MessageType.TASK_REQUEST
        assert messages[0].payload["task"] == "Deploy service"
        assert messages[0].payload["chain_id"] == chain.chain_id

    def test_delegate_invalid_hierarchy(self, orchestrator):
        """CEO cannot delegate directly to pm-alpha (not direct report)."""
        chain = orchestrator.create_chain("Task", "hermes")
        with pytest.raises(InvalidDelegation):
            orchestrator.delegate(chain, "hermes", "pm-alpha")

    def test_delegate_wrong_direction(self, orchestrator):
        """PM cannot delegate to CTO (upward delegation)."""
        chain = orchestrator.create_chain("Task", "pm-alpha")
        with pytest.raises(InvalidDelegation):
            orchestrator.delegate(chain, "pm-alpha", "cto")

    def test_delegate_circular(self, orchestrator):
        """Cannot delegate back to a profile already targeted in the chain."""
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        orchestrator.delegate(chain, "cto", "pm-alpha")
        # pm-alpha is already targeted — even if we somehow tried to reach it again
        # We test circular by re-adding an existing target
        with pytest.raises(CircularDelegation):
            orchestrator.delegate(chain, "cto", "pm-alpha")

    def test_delegate_completed_chain_raises(self, orchestrator):
        chain = orchestrator.create_chain("Task", "hermes")
        chain.complete()
        with pytest.raises(ChainAlreadyComplete):
            orchestrator.delegate(chain, "hermes", "cto")

    def test_delegate_with_priority(self, orchestrator, bus):
        chain = orchestrator.create_chain("Urgent fix", "hermes")
        orchestrator.delegate(chain, "hermes", "cto", priority=MessagePriority.URGENT)
        messages = bus.poll("cto")
        assert messages[0].priority == MessagePriority.URGENT

    def test_delegate_down_chain_full_path(self, orchestrator):
        """delegate_down_chain resolves CEO → CTO → PM-alpha."""
        chain = orchestrator.create_chain("Full delegation", "hermes")
        hops = orchestrator.delegate_down_chain(chain, "pm-alpha")
        assert len(hops) == 2
        assert hops[0].from_profile == "hermes"
        assert hops[0].to_profile == "cto"
        assert hops[1].from_profile == "cto"
        assert hops[1].to_profile == "pm-alpha"
        assert chain.status == ChainStatus.ACTIVE

    def test_delegate_down_chain_single_hop(self, orchestrator):
        """CEO → CTO is a single hop."""
        chain = orchestrator.create_chain("Direct delegation", "hermes")
        hops = orchestrator.delegate_down_chain(chain, "cto")
        assert len(hops) == 1

    def test_delegate_down_chain_invalid_path(self, orchestrator):
        """Cannot delegate from PM to CEO's child in another branch."""
        chain = orchestrator.create_chain("Task", "cmo")
        with pytest.raises(InvalidDelegation):
            orchestrator.delegate_down_chain(chain, "pm-alpha")


# ===========================================================================
# Worker Management
# ===========================================================================

class TestWorkerManagement:
    """Tests for spawn_worker and complete_worker."""

    def test_spawn_worker(self, orchestrator, worker_registries):
        chain = orchestrator.create_chain("Implement feature", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        orchestrator.delegate(chain, "cto", "pm-alpha")
        subagent_id = orchestrator.spawn_worker(chain, "pm-alpha", "Write tests")
        assert subagent_id.startswith("sa-")
        assert subagent_id in chain.workers

    def test_spawn_worker_marks_hop_working(self, orchestrator):
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        orchestrator.delegate(chain, "cto", "pm-alpha")
        orchestrator.spawn_worker(chain, "pm-alpha", "Do work")
        # Find PM's hop
        pm_hop = [h for h in chain.hops if h.to_profile == "pm-alpha"][0]
        assert pm_hop.status == HopStatus.WORKING

    def test_spawn_worker_records_in_registry(self, orchestrator, worker_registries):
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        subagent_id = orchestrator.spawn_worker(chain, "pm-alpha", "Write code")
        # Verify in SubagentRegistry
        wreg = worker_registries("pm-alpha")
        subagent = wreg.get(subagent_id, project_manager="pm-alpha")
        assert subagent.task_goal == "Write code"
        assert subagent.parent_request_id == chain.chain_id

    def test_spawn_multiple_workers(self, orchestrator):
        chain = orchestrator.create_chain("Multi-worker task", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        id1 = orchestrator.spawn_worker(chain, "pm-alpha", "Task A")
        id2 = orchestrator.spawn_worker(chain, "pm-alpha", "Task B")
        assert len(chain.workers) == 2
        assert id1 != id2

    def test_spawn_worker_on_completed_chain_raises(self, orchestrator):
        chain = orchestrator.create_chain("Task", "hermes")
        chain.complete()
        with pytest.raises(ChainAlreadyComplete):
            orchestrator.spawn_worker(chain, "pm-alpha", "Work")

    def test_complete_worker(self, orchestrator, worker_registries):
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        sid = orchestrator.spawn_worker(chain, "pm-alpha", "Build")
        orchestrator.complete_worker(chain, "pm-alpha", sid, "Done: built module X")
        wreg = worker_registries("pm-alpha")
        subagent = wreg.get(sid, project_manager="pm-alpha")
        assert subagent.status == "completed"
        assert subagent.result_summary == "Done: built module X"


# ===========================================================================
# Result Propagation
# ===========================================================================

class TestResultPropagation:
    """Tests for propagate_result and fail_chain."""

    def test_propagate_result_single_hop(self, orchestrator, bus):
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        orchestrator.propagate_result(chain, "Result: success")
        assert chain.status == ChainStatus.COMPLETED
        # CEO should have received a response
        responses = bus.poll("hermes", message_type=MessageType.TASK_RESPONSE)
        assert len(responses) == 1
        assert responses[0].payload["result"] == "Result: success"

    def test_propagate_result_multi_hop(self, orchestrator, bus):
        chain = orchestrator.create_chain("Multi-hop task", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        orchestrator.propagate_result(chain, "All tests pass")
        assert chain.status == ChainStatus.COMPLETED
        # Both CEO and CTO should have responses
        ceo_msgs = bus.poll("hermes", message_type=MessageType.TASK_RESPONSE)
        cto_msgs = bus.poll("cto", message_type=MessageType.TASK_RESPONSE)
        assert len(ceo_msgs) == 1
        assert len(cto_msgs) == 1

    def test_propagate_result_marks_hops_completed(self, orchestrator):
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        orchestrator.propagate_result(chain, "Done")
        for hop in chain.hops:
            assert hop.status == HopStatus.COMPLETED

    def test_fail_chain(self, orchestrator, bus):
        chain = orchestrator.create_chain("Failing task", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        orchestrator.fail_chain(chain, "Out of budget")
        assert chain.status == ChainStatus.FAILED
        responses = bus.poll("hermes", message_type=MessageType.TASK_RESPONSE)
        assert len(responses) == 1
        assert "Out of budget" in responses[0].payload["error"]

    def test_fail_chain_marks_hops_failed(self, orchestrator):
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        orchestrator.fail_chain(chain, "Error occurred")
        for hop in chain.hops:
            assert hop.status == HopStatus.FAILED

    def test_propagate_result_with_correlation(self, orchestrator, bus):
        """Result messages use the chain_id as correlation_id."""
        chain = orchestrator.create_chain("Task", "hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        orchestrator.propagate_result(chain, "Result")
        correlated = bus.get_by_correlation(chain.chain_id)
        # The response(s) use chain_id as correlation
        assert len(correlated) >= 1
        assert correlated[0].correlation_id == chain.chain_id


# ===========================================================================
# End-to-End Scenarios
# ===========================================================================

class TestEndToEnd:
    """Full delegation chain scenarios."""

    def test_full_chain_ceo_to_worker(self, orchestrator, bus, worker_registries):
        """CEO → CTO → PM → Worker → result back up."""
        # 1. CEO creates task and delegates down to PM
        chain = orchestrator.create_chain("Build login page", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        assert chain.status == ChainStatus.ACTIVE
        assert len(chain.hops) == 2

        # 2. PM spawns worker
        sid = orchestrator.spawn_worker(chain, "pm-alpha", "Code the login form")
        assert len(chain.workers) == 1

        # 3. Worker completes
        orchestrator.complete_worker(chain, "pm-alpha", sid, "Login form built")

        # 4. Result propagates up
        orchestrator.propagate_result(chain, "Login page complete")
        assert chain.status == ChainStatus.COMPLETED

        # 5. Verify messages at each level
        ceo_msgs = bus.poll("hermes", message_type=MessageType.TASK_RESPONSE)
        cto_msgs = bus.poll("cto", message_type=MessageType.TASK_RESPONSE)
        assert len(ceo_msgs) == 1
        assert len(cto_msgs) == 1

    def test_cross_department_delegation(self, orchestrator, bus):
        """CEO delegates to both CTO and CMO."""
        chain_eng = orchestrator.create_chain("Build backend", "hermes")
        chain_mkt = orchestrator.create_chain("Plan launch", "hermes")

        orchestrator.delegate_down_chain(chain_eng, "pm-alpha")
        orchestrator.delegate_down_chain(chain_mkt, "pm-mktg")

        # Both chains active
        assert chain_eng.status == ChainStatus.ACTIVE
        assert chain_mkt.status == ChainStatus.ACTIVE

        # Complete engineering first
        orchestrator.propagate_result(chain_eng, "Backend ready")
        assert chain_eng.status == ChainStatus.COMPLETED
        assert chain_mkt.status == ChainStatus.ACTIVE

        # Then marketing
        orchestrator.propagate_result(chain_mkt, "Launch plan ready")
        assert chain_mkt.status == ChainStatus.COMPLETED

    def test_multiple_workers_per_pm(self, orchestrator, worker_registries):
        """PM spawns multiple workers for a single task."""
        chain = orchestrator.create_chain("Big feature", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")

        sid1 = orchestrator.spawn_worker(chain, "pm-alpha", "Frontend")
        sid2 = orchestrator.spawn_worker(chain, "pm-alpha", "Backend")
        sid3 = orchestrator.spawn_worker(chain, "pm-alpha", "Tests")

        assert len(chain.workers) == 3

        # Complete all workers
        orchestrator.complete_worker(chain, "pm-alpha", sid1, "Frontend done")
        orchestrator.complete_worker(chain, "pm-alpha", sid2, "Backend done")
        orchestrator.complete_worker(chain, "pm-alpha", sid3, "Tests pass")

        # Propagate aggregated result
        orchestrator.propagate_result(chain, "All 3 components built")
        assert chain.status == ChainStatus.COMPLETED

    def test_parallel_chains_independent(self, orchestrator, bus):
        """Multiple simultaneous chains don't interfere."""
        chains = []
        for i in range(5):
            c = orchestrator.create_chain(f"Task {i}", "hermes")
            orchestrator.delegate(c, "hermes", "cto")
            chains.append(c)

        # Complete them in reverse
        for c in reversed(chains):
            orchestrator.propagate_result(c, f"Done: {c.task_description}")

        for c in chains:
            assert c.status == ChainStatus.COMPLETED

        # All chains listed
        all_chains = orchestrator.list_chains()
        assert len(all_chains) == 5
