"""End-to-end integration test for the full propagation path via WorkerBridge.

Tests the complete lifecycle using WorkerBridge.spawn_with_chain():
  1. Build a ProfileRegistry with hermes → cto → pm-bridge-e2e hierarchy.
  2. Create an in-process MessageBus (in-memory SQLite).
  3. Wire a ChainOrchestrator and a WorkerBridge together.
  4. Spawn a worker using spawn_with_chain() to wire auto-propagation.
  5. Complete the worker — auto-propagation fires the callback.
  6. Verify that IPC TASK_RESPONSE messages were sent up the chain.

All databases use `:memory:` or temporary directories for full isolation.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from core.integration.delegation import ChainStatus, DelegationChain, HopStatus
from core.integration.orchestrator import ChainOrchestrator
from core.ipc.message_bus import MessageBus
from core.ipc.models import MessageType
from core.registry.profile_registry import ProfileRegistry
from core.workers.subagent_registry import SubagentRegistry
from core.workers.interface import WorkerManager
from integrations.hermes.worker_bridge import WorkerBridge


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _build_registry(db_path: str) -> ProfileRegistry:
    """Create a ProfileRegistry with the test hierarchy.

    Hierarchy::

        hermes (CEO, auto-created)
        └── cto (department_head)
            └── pm-bridge-e2e (project_manager)
    """
    reg = ProfileRegistry(db_path)
    reg.create_profile(
        name="cto",
        display_name="CTO",
        role="department_head",
        parent="hermes",
        department="engineering",
        description="Chief Technology Officer",
    )
    reg.create_profile(
        name="pm-bridge-e2e",
        display_name="PM Bridge E2E",
        role="project_manager",
        parent="cto",
        department="engineering",
        description="Project manager used exclusively by worker-bridge e2e tests",
    )
    return reg


def _make_worker_registry() -> SubagentRegistry:
    """Return an in-memory SubagentRegistry."""
    return SubagentRegistry(base_path=":memory:")


def _build_orchestrator(
    registry: ProfileRegistry,
    bus: MessageBus,
    worker_reg: SubagentRegistry,
) -> ChainOrchestrator:
    """Build a ChainOrchestrator wired to the provided subsystems."""
    return ChainOrchestrator(
        registry=registry,
        bus=bus,
        worker_registry_factory=lambda pm_name: worker_reg,
        context_manager_factory=None,
    )


def _build_bridge(
    worker_reg: SubagentRegistry,
    orchestrator: ChainOrchestrator,
    tmp_path: Path,
) -> WorkerBridge:
    """Return a WorkerBridge backed by *worker_reg* and *orchestrator*."""
    return WorkerBridge(
        worker_registry_factory=lambda: worker_reg,
        workspace_dir=tmp_path / "ws",
        chain_orchestrator=orchestrator,
        pm_profile="pm-bridge-e2e",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def worker_reg() -> SubagentRegistry:
    """Shared in-memory SubagentRegistry."""
    return _make_worker_registry()


@pytest.fixture
def tmp_registry(tmp_path: Path) -> ProfileRegistry:
    """ProfileRegistry with hermes → cto → pm-bridge-e2e hierarchy."""
    reg = _build_registry(str(tmp_path / "registry.db"))
    yield reg
    reg.close()


@pytest.fixture
def tmp_bus(tmp_path: Path) -> MessageBus:
    """MessageBus backed by a temporary SQLite database."""
    bus = MessageBus(db_path=str(tmp_path / "bus.db"))
    yield bus
    bus.close()


@pytest.fixture
def orchestrator(
    tmp_registry: ProfileRegistry,
    tmp_bus: MessageBus,
    worker_reg: SubagentRegistry,
) -> ChainOrchestrator:
    """ChainOrchestrator wired to all test subsystems."""
    return _build_orchestrator(tmp_registry, tmp_bus, worker_reg)


@pytest.fixture
def bridge(
    worker_reg: SubagentRegistry,
    orchestrator: ChainOrchestrator,
    tmp_path: Path,
) -> WorkerBridge:
    """WorkerBridge wired to the test orchestrator."""
    return _build_bridge(worker_reg, orchestrator, tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkerBridgeProtocolCompliance:
    """WorkerBridge must satisfy the WorkerManager protocol at import time."""

    def test_isinstance_worker_manager(self, bridge: WorkerBridge) -> None:
        """isinstance(bridge, WorkerManager) must be True."""
        assert isinstance(bridge, WorkerManager), (
            "WorkerBridge does not satisfy the WorkerManager protocol"
        )

    def test_has_spawn_worker(self, bridge: WorkerBridge) -> None:
        """WorkerBridge.spawn_worker must exist and be callable."""
        assert callable(getattr(bridge, "spawn_worker", None))

    def test_has_on_worker_complete(self, bridge: WorkerBridge) -> None:
        """WorkerBridge.on_worker_complete must exist and be callable."""
        assert callable(getattr(bridge, "on_worker_complete", None))

    def test_has_on_worker_error(self, bridge: WorkerBridge) -> None:
        """WorkerBridge.on_worker_error must exist and be callable."""
        assert callable(getattr(bridge, "on_worker_error", None))

    def test_has_resume_worker(self, bridge: WorkerBridge) -> None:
        """WorkerBridge.resume_worker must exist and be callable."""
        assert callable(getattr(bridge, "resume_worker", None))


class TestSpawnWithChain:
    """spawn_with_chain() wires auto-propagation and returns a valid subagent_id."""

    def test_returns_subagent_id(
        self,
        bridge: WorkerBridge,
        orchestrator: ChainOrchestrator,
    ) -> None:
        """spawn_with_chain() returns a string subagent_id."""
        chain = orchestrator.create_chain("Test task", originator="hermes")
        subagent_id = bridge.spawn_with_chain(
            pm_profile="pm-bridge-e2e",
            task="Do something",
            chain=chain,
        )
        assert isinstance(subagent_id, str)
        assert subagent_id.startswith("sa-")

    def test_worker_in_running_status(
        self,
        bridge: WorkerBridge,
        orchestrator: ChainOrchestrator,
        worker_reg: SubagentRegistry,
    ) -> None:
        """Worker spawned via spawn_with_chain() starts in 'running' status."""
        chain = orchestrator.create_chain("Status check", originator="hermes")
        subagent_id = bridge.spawn_with_chain(
            pm_profile="pm-bridge-e2e",
            task="Status check task",
            chain=chain,
        )
        subagent = worker_reg.get(subagent_id, project_manager="pm-bridge-e2e")
        assert subagent.status == "running"

    def test_no_chain_orchestrator_warns_but_still_spawns(
        self,
        tmp_path: Path,
        worker_reg: SubagentRegistry,
    ) -> None:
        """spawn_with_chain() without an orchestrator spawns the worker but skips autoprop."""
        # Build bridge WITHOUT a chain orchestrator
        bridge_no_orch = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_path / "ws",
            chain_orchestrator=None,
        )
        mock_chain = MagicMock()

        subagent_id = bridge_no_orch.spawn_with_chain(
            pm_profile="pm-bridge-e2e",
            task="Should still spawn",
            chain=mock_chain,
        )

        assert subagent_id.startswith("sa-")
        subagent = worker_reg.get(subagent_id, project_manager="pm-bridge-e2e")
        assert subagent.status == "running"


class TestE2EAutoPropagationViaSpawnWithChain:
    """Full end-to-end: spawn_with_chain → complete → auto-propagate → IPC sent.

    This is the primary gap test.  It verifies that when a worker is spawned
    via :meth:`WorkerBridge.spawn_with_chain`, completing the worker through
    the :class:`SubagentRegistry` triggers the auto-propagation callback,
    which sends IPC TASK_RESPONSE messages up the delegation chain.
    """

    def test_auto_propagation_fires_on_complete(
        self,
        bridge: WorkerBridge,
        orchestrator: ChainOrchestrator,
        worker_reg: SubagentRegistry,
    ) -> None:
        """Completing a worker registered via spawn_with_chain auto-propagates.

        Steps
        -----
        1. Create chain, delegate to pm-bridge-e2e.
        2. Spawn worker via spawn_with_chain() (wires auto-propagation).
        3. Call registry.complete() on the worker — the callback fires.
        4. Assert chain.status reaches a terminal state (COMPLETED) because
           the ResultCollector auto-propagated when all workers reported.
        """
        # 1. Create chain and delegate
        chain = orchestrator.create_chain(
            "E2E auto-propagation", originator="hermes"
        )
        orchestrator.delegate_down_chain(chain, "pm-bridge-e2e")
        assert chain.status == ChainStatus.ACTIVE

        # 2. Spawn worker with auto-propagation wired
        subagent_id = bridge.spawn_with_chain(
            pm_profile="pm-bridge-e2e",
            task="Implement feature X",
            chain=chain,
        )

        # Register this worker on the chain so ResultCollector knows it
        chain.add_worker(subagent_id)
        assert subagent_id in chain.workers

        # 3. Complete the worker via the registry — triggers the callback
        worker_reg.complete(
            subagent_id,
            result_summary="Feature X complete",
            project_manager="pm-bridge-e2e",
        )

        # 4. Chain should now be terminal (COMPLETED) because
        #    auto-propagation ran when all registered workers reported.
        assert chain.is_terminal, (
            f"Chain expected to be terminal after auto-propagation, "
            f"got status={chain.status!r}"
        )

    def test_ipc_messages_sent_after_auto_propagation(
        self,
        bridge: WorkerBridge,
        orchestrator: ChainOrchestrator,
        worker_reg: SubagentRegistry,
        tmp_bus: MessageBus,
    ) -> None:
        """After auto-propagation, TASK_RESPONSE IPC messages reach hermes and cto.

        This verifies that the full IPC pipeline is exercised, not just
        the in-memory chain state.
        """
        # Setup
        chain = orchestrator.create_chain(
            "IPC flow via bridge", originator="hermes"
        )
        orchestrator.delegate_down_chain(chain, "pm-bridge-e2e")

        subagent_id = bridge.spawn_with_chain(
            pm_profile="pm-bridge-e2e",
            task="Write tests",
            chain=chain,
        )
        chain.add_worker(subagent_id)

        # Complete via the registry to trigger the auto-propagation callback
        worker_reg.complete(
            subagent_id,
            result_summary="Tests written and passing",
            project_manager="pm-bridge-e2e",
        )

        # Verify IPC messages were sent up the chain
        hermes_responses = tmp_bus.poll(
            "hermes", message_type=MessageType.TASK_RESPONSE
        )
        assert len(hermes_responses) >= 1, (
            "hermes should have received at least one TASK_RESPONSE"
        )

        cto_responses = tmp_bus.poll(
            "cto", message_type=MessageType.TASK_RESPONSE
        )
        assert len(cto_responses) >= 1, (
            "cto should have received at least one TASK_RESPONSE"
        )

        # Verify the result payload was delivered
        result_msgs = [
            m for m in hermes_responses
            if m.payload.get("result") is not None
        ]
        assert result_msgs, "At least one TASK_RESPONSE must carry a result payload"
        assert result_msgs[0].payload.get("chain_id") == chain.chain_id

    def test_propagate_result_called_on_mock_orchestrator(
        self,
        tmp_path: Path,
        worker_reg: SubagentRegistry,
    ) -> None:
        """spawn_with_chain wires a callback that calls orchestrator.propagate_result.

        Uses a mock orchestrator to make the assertion crisp and fast.
        """
        mock_orch = MagicMock()

        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_path / "ws",
            chain_orchestrator=mock_orch,
        )

        mock_chain = MagicMock()

        subagent_id = bridge.spawn_with_chain(
            pm_profile="pm-bridge-e2e",
            task="Mock propagation task",
            chain=mock_chain,
        )

        # Completing directly via the WorkerBridge should trigger propagation
        bridge.complete(
            pm_profile="pm-bridge-e2e",
            subagent_id=subagent_id,
            result="mock result",
        )

        # The auto-propagation callback registered by setup_auto_propagation
        # should have called orchestrator.propagate_result
        mock_orch.propagate_result.assert_called_once_with(mock_chain, "mock result")


class TestSpawnWithChainMultipleWorkers:
    """Multiple workers with spawn_with_chain — each completion can propagate."""

    def test_two_workers_each_complete_independently(
        self,
        bridge: WorkerBridge,
        orchestrator: ChainOrchestrator,
        worker_reg: SubagentRegistry,
    ) -> None:
        """Two workers can be spawned and each completes independently.

        Note: WorkerBridge.setup_auto_propagation() registers a callback that
        calls orchestrator.propagate_result() immediately when ANY worker
        completes (it does not wait for all workers).  This is the per-event
        design.  For all-workers-must-complete semantics, use
        ChainOrchestrator.setup_event_driven_propagation() which employs
        ResultCollector.collect_worker_result(auto_propagate=True).

        This test verifies that spawning two workers succeeds and that
        completing the first worker completes the chain (first callback fires),
        and a second completion on an already-terminal chain is handled without
        crashing (the second callback logs the error and swallows it).
        """
        chain = orchestrator.create_chain(
            "Two workers test", originator="hermes"
        )
        orchestrator.delegate_down_chain(chain, "pm-bridge-e2e")

        # Spawn two workers; setup_auto_propagation is called twice
        sid1 = bridge.spawn_with_chain(
            pm_profile="pm-bridge-e2e",
            task="Part A",
            chain=chain,
        )
        chain.add_worker(sid1)

        sid2 = bridge.spawn_with_chain(
            pm_profile="pm-bridge-e2e",
            task="Part B",
            chain=chain,
        )
        chain.add_worker(sid2)

        # Both workers registered
        assert sid1 in chain.workers
        assert sid2 in chain.workers

        # Complete first worker — auto-propagation fires the first callback and
        # completes the chain immediately (WorkerBridge design: each completion
        # propagates up rather than waiting for all workers).
        worker_reg.complete(
            sid1,
            result_summary="Part A done",
            project_manager="pm-bridge-e2e",
        )
        assert chain.is_terminal, (
            "Chain should be terminal after the first worker's auto-propagation fires"
        )

        # Second worker completes on an already-terminal chain.  The callback
        # raises ChainAlreadyComplete which the registry catches and logs.
        # Completing the subagent in the registry itself must still succeed.
        worker_reg.complete(
            sid2,
            result_summary="Part B done",
            project_manager="pm-bridge-e2e",
        )
        sa2 = worker_reg.get(sid2, project_manager="pm-bridge-e2e")
        assert sa2.status == "completed", (
            "Second worker should still be marked completed in the registry"
        )

    def test_orchestrator_event_driven_propagation_waits_for_all_workers(
        self,
        orchestrator: ChainOrchestrator,
        worker_reg: SubagentRegistry,
        tmp_bus: MessageBus,
    ) -> None:
        """ChainOrchestrator.setup_event_driven_propagation waits for ALL workers.

        This is the preferred approach when you need all workers to report
        before propagation fires.  Contrast with WorkerBridge.spawn_with_chain()
        which propagates per-worker-completion.

        Note: setup_event_driven_propagation uses ResultCollector.propagate_up()
        internally which sends IPC messages and marks hops COMPLETED, but does
        NOT call chain.complete() (that is done explicitly by
        ChainOrchestrator.propagate_result()).  So after both workers report,
        both worker_results are recorded and IPC messages are sent, but the
        chain status stays ACTIVE until an explicit propagate_result() call.
        """
        chain = orchestrator.create_chain(
            "All-workers-first test", originator="hermes"
        )
        orchestrator.delegate_down_chain(chain, "pm-bridge-e2e")

        # Spawn two workers via the orchestrator
        sid1 = orchestrator.spawn_worker(chain, "pm-bridge-e2e", "Part A")
        sid2 = orchestrator.spawn_worker(chain, "pm-bridge-e2e", "Part B")

        # Wire event-driven propagation via the orchestrator
        orchestrator.setup_event_driven_propagation(chain, "pm-bridge-e2e")

        # Complete first worker — propagation should NOT have fired yet because
        # sid2 has not reported.  Verify by checking worker_results.
        worker_reg.complete(
            sid1,
            result_summary="Part A done",
            project_manager="pm-bridge-e2e",
        )
        # Only sid1 is recorded; sid2 hasn't reported yet
        assert sid1 in chain.worker_results, (
            "sid1 result should be recorded after first completion"
        )
        assert sid2 not in chain.worker_results, (
            "sid2 result must NOT be recorded before second completion"
        )
        # IPC messages should not have been sent yet (propagation hasn't fired)
        hermes_msgs_after_first = tmp_bus.poll(
            "hermes", message_type=MessageType.TASK_RESPONSE
        )
        assert len(hermes_msgs_after_first) == 0, (
            "No TASK_RESPONSE should be sent after only the first worker completes"
        )

        # Complete second worker — now all workers done, propagation fires,
        # IPC messages flow up the chain.
        worker_reg.complete(
            sid2,
            result_summary="Part B done",
            project_manager="pm-bridge-e2e",
        )
        # Both workers recorded
        assert sid1 in chain.worker_results
        assert sid2 in chain.worker_results

        # IPC messages should now be present for hermes and cto
        hermes_msgs = tmp_bus.poll("hermes", message_type=MessageType.TASK_RESPONSE)
        assert len(hermes_msgs) >= 1, (
            "hermes should receive TASK_RESPONSE after all workers complete"
        )
        cto_msgs = tmp_bus.poll("cto", message_type=MessageType.TASK_RESPONSE)
        assert len(cto_msgs) >= 1, (
            "cto should receive TASK_RESPONSE after all workers complete"
        )
