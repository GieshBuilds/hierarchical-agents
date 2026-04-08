"""End-to-end integration test for the full delegation → worker → propagation flow.

Tests the complete lifecycle:
  1. Build a ProfileRegistry with hermes → cto → pm-test-e2e hierarchy.
  2. Create an in-process MessageBus (temp SQLite).
  3. Wire everything together via ChainOrchestrator.
  4. Create a chain, delegate down to pm-test-e2e, spawn a worker, complete
     the worker, propagate the result.
  5. Assert final chain state, IPC messages, and worker_results.

The test is fully self-contained — it uses temporary directories/databases
created by pytest's ``tmp_path`` fixture and cleans up automatically.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.integration.delegation import ChainStatus, HopStatus
from core.integration.orchestrator import ChainOrchestrator
from core.ipc.message_bus import MessageBus
from core.ipc.models import MessageType
from core.registry.profile_registry import ProfileRegistry
from core.workers.subagent_registry import SubagentRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_registry(db_path: str) -> ProfileRegistry:
    """Create a ProfileRegistry with the test hierarchy.

    Hierarchy::

        hermes (CEO, auto-created)
        └── cto (department_head)
            └── pm-test-e2e (project_manager)

    Parameters
    ----------
    db_path:
        Path to the SQLite file for the registry.

    Returns
    -------
    ProfileRegistry
        Populated registry instance.  The caller is responsible for closing it.
    """
    reg = ProfileRegistry(db_path)
    # 'hermes' CEO is auto-created by ProfileRegistry.__init__
    reg.create_profile(
        name="cto",
        display_name="CTO",
        role="department_head",
        parent="hermes",
        department="engineering",
        description="Chief Technology Officer",
    )
    reg.create_profile(
        name="pm-test-e2e",
        display_name="PM E2E Test",
        role="project_manager",
        parent="cto",
        department="engineering",
        description="Project manager used exclusively by e2e tests",
    )
    return reg


def _build_bus(db_path: str) -> MessageBus:
    """Create a MessageBus backed by a temp SQLite file.

    Parameters
    ----------
    db_path:
        Path to the SQLite file for the bus.

    Returns
    -------
    MessageBus
        Ready-to-use bus.  The caller is responsible for closing it.
    """
    return MessageBus(db_path=db_path)


def _build_orchestrator(
    registry: ProfileRegistry,
    bus: MessageBus,
    workers_dir: str,
) -> ChainOrchestrator:
    """Build a ChainOrchestrator wired to the provided subsystems.

    Parameters
    ----------
    registry:
        ProfileRegistry instance.
    bus:
        MessageBus instance.
    workers_dir:
        Root directory for per-PM SubagentRegistry databases.

    Returns
    -------
    ChainOrchestrator
        Fully configured orchestrator.
    """

    def _worker_factory(pm_name: str) -> SubagentRegistry:
        """Return a SubagentRegistry rooted at workers_dir."""
        return SubagentRegistry(workers_dir)

    return ChainOrchestrator(
        registry=registry,
        bus=bus,
        worker_registry_factory=_worker_factory,
        context_manager_factory=None,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_registry(tmp_path: Path) -> ProfileRegistry:
    """ProfileRegistry with the hermes → cto → pm-test-e2e hierarchy."""
    reg = _build_registry(str(tmp_path / "registry.db"))
    yield reg
    reg.close()


@pytest.fixture
def tmp_bus(tmp_path: Path) -> MessageBus:
    """MessageBus backed by a temporary SQLite database."""
    bus = _build_bus(str(tmp_path / "bus.db"))
    yield bus
    bus.close()


@pytest.fixture
def workers_dir(tmp_path: Path) -> str:
    """Temporary directory for worker databases."""
    d = tmp_path / "workers"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@pytest.fixture
def orchestrator(
    tmp_registry: ProfileRegistry,
    tmp_bus: MessageBus,
    workers_dir: str,
) -> ChainOrchestrator:
    """ChainOrchestrator wired to all test subsystems."""
    return _build_orchestrator(tmp_registry, tmp_bus, workers_dir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestE2EPropagation:
    """Full end-to-end flow: create chain → delegate → worker → propagate."""

    def test_full_flow_chain_completed(
        self,
        orchestrator: ChainOrchestrator,
        tmp_bus: MessageBus,
        workers_dir: str,
    ) -> None:
        """Chain reaches COMPLETED status after full delegation → result flow.

        Steps
        -----
        1. Create chain originating from hermes.
        2. Delegate down to pm-test-e2e (hermes → cto → pm-test-e2e).
        3. Spawn a worker under pm-test-e2e.
        4. Complete the worker.
        5. Propagate the result.
        6. Assert chain.status == COMPLETED.
        """
        # 1. Create chain
        chain = orchestrator.create_chain(
            "Build e2e test scaffold",
            originator="hermes",
        )
        assert chain.status == ChainStatus.PENDING
        assert chain.chain_id.startswith("chain-")

        # 2. Delegate down to PM
        hops = orchestrator.delegate_down_chain(chain, "pm-test-e2e")
        assert len(hops) == 2
        assert hops[0].from_profile == "hermes"
        assert hops[0].to_profile == "cto"
        assert hops[1].from_profile == "cto"
        assert hops[1].to_profile == "pm-test-e2e"
        assert chain.status == ChainStatus.ACTIVE

        # 3. Spawn a worker
        subagent_id = orchestrator.spawn_worker(
            chain,
            pm_profile="pm-test-e2e",
            task="Write the e2e test file",
        )
        assert subagent_id.startswith("sa-")
        assert subagent_id in chain.workers

        # 4. Complete the worker
        result_str = "e2e test scaffold written (42 tests)"
        auto_propagated = orchestrator.complete_worker(
            chain,
            pm_profile="pm-test-e2e",
            subagent_id=subagent_id,
            result=result_str,
        )

        # Confirm the worker registry reflects the completion
        worker_reg = SubagentRegistry(workers_dir)
        subagent = worker_reg.get(subagent_id, project_manager="pm-test-e2e")
        assert subagent.status == "completed"
        assert subagent.result_summary == result_str

        # 5. Propagate result — only needed when auto-propagation did not fire.
        # The modified orchestrator's complete_worker() auto-propagates when
        # all registered workers have reported.  If it returned True the chain
        # may already be completed; call propagate_result() only when needed.
        if not chain.is_terminal:
            orchestrator.propagate_result(chain, result_str)

        # 6. Assert chain completed
        assert chain.status == ChainStatus.COMPLETED
        assert chain.completed_at is not None

    def test_ipc_messages_flow_up(
        self,
        orchestrator: ChainOrchestrator,
        tmp_bus: MessageBus,
    ) -> None:
        """After propagate_result, both hermes and cto receive TASK_RESPONSE.

        The result should flow from pm-test-e2e → cto → hermes as IPC
        TASK_RESPONSE messages.
        """
        chain = orchestrator.create_chain("Test IPC flow", originator="hermes")
        orchestrator.delegate_down_chain(chain, "pm-test-e2e")

        subagent_id = orchestrator.spawn_worker(
            chain,
            pm_profile="pm-test-e2e",
            task="Implement feature",
        )
        orchestrator.complete_worker(
            chain,
            pm_profile="pm-test-e2e",
            subagent_id=subagent_id,
            result="Feature implemented",
        )
        # Propagate result only if complete_worker() did not auto-complete the chain.
        if not chain.is_terminal:
            orchestrator.propagate_result(chain, "Feature implemented")

        # Hermes (CEO) receives at least one TASK_RESPONSE (possibly two when
        # complete_worker auto-propagates AND propagate_result is also called).
        hermes_responses = tmp_bus.poll(
            "hermes", message_type=MessageType.TASK_RESPONSE
        )
        assert len(hermes_responses) >= 1, (
            "hermes should have received at least one TASK_RESPONSE"
        )
        # At least one response carries the result payload.
        result_msgs = [
            m for m in hermes_responses
            if m.payload.get("result") is not None
        ]
        assert len(result_msgs) >= 1
        assert result_msgs[0].payload.get("chain_id") == chain.chain_id

        # CTO also receives a TASK_RESPONSE (pm-test-e2e → cto leg)
        cto_responses = tmp_bus.poll("cto", message_type=MessageType.TASK_RESPONSE)
        assert len(cto_responses) >= 1, (
            "cto should have received at least one TASK_RESPONSE"
        )

    def test_worker_results_on_chain(
        self,
        orchestrator: ChainOrchestrator,
        tmp_bus: MessageBus,
    ) -> None:
        """chain.worker_results contains the result after collect_worker_result.

        The ResultCollector.collect_worker_result() helper (Stream A) writes
        worker results to chain.worker_results[subagent_id].  We exercise
        this via the orchestrator's result_collector directly.
        """
        chain = orchestrator.create_chain("Test worker_results", originator="hermes")
        orchestrator.delegate_down_chain(chain, "pm-test-e2e")

        # Spawn two workers
        sid1 = orchestrator.spawn_worker(chain, "pm-test-e2e", "Part A")
        sid2 = orchestrator.spawn_worker(chain, "pm-test-e2e", "Part B")

        # Use the result_collector to record results on the chain
        orchestrator._result_collector.collect_worker_result(
            chain, sid1, "Part A result"
        )
        orchestrator._result_collector.collect_worker_result(
            chain, sid2, "Part B result"
        )

        assert chain.worker_results[sid1] == "Part A result"
        assert chain.worker_results[sid2] == "Part B result"

        # Propagate aggregated result
        orchestrator.propagate_result(chain, "Both parts complete")
        assert chain.status == ChainStatus.COMPLETED

    def test_hops_complete_after_propagation(
        self,
        orchestrator: ChainOrchestrator,
    ) -> None:
        """All hops are marked COMPLETED after propagate_result."""
        chain = orchestrator.create_chain("Hop status test", originator="hermes")
        orchestrator.delegate_down_chain(chain, "pm-test-e2e")

        sid = orchestrator.spawn_worker(chain, "pm-test-e2e", "Do work")
        orchestrator.complete_worker(chain, "pm-test-e2e", sid, "Done")
        # complete_worker may auto-propagate; only call propagate_result if needed.
        if not chain.is_terminal:
            orchestrator.propagate_result(chain, "Done")

        for hop in chain.hops:
            assert hop.status == HopStatus.COMPLETED, (
                f"Hop {hop.from_profile}→{hop.to_profile} expected COMPLETED, "
                f"got {hop.status}"
            )

    def test_task_request_messages_delivered_to_pm(
        self,
        orchestrator: ChainOrchestrator,
        tmp_bus: MessageBus,
    ) -> None:
        """Delegation sends TASK_REQUEST IPC messages to each intermediate hop.

        After delegate_down_chain(chain, 'pm-test-e2e'):
        - cto inbox has a TASK_REQUEST (hermes→cto)
        - pm-test-e2e inbox has a TASK_REQUEST (cto→pm-test-e2e)
        """
        chain = orchestrator.create_chain("Delegation messages", originator="hermes")
        orchestrator.delegate_down_chain(chain, "pm-test-e2e")

        cto_requests = tmp_bus.poll("cto", message_type=MessageType.TASK_REQUEST)
        pm_requests = tmp_bus.poll(
            "pm-test-e2e", message_type=MessageType.TASK_REQUEST
        )

        assert len(cto_requests) >= 1, "cto should have received a TASK_REQUEST"
        assert len(pm_requests) >= 1, (
            "pm-test-e2e should have received a TASK_REQUEST"
        )
        assert cto_requests[0].payload["chain_id"] == chain.chain_id
        assert pm_requests[0].payload["chain_id"] == chain.chain_id

    def test_chain_correlation_id_matches(
        self,
        orchestrator: ChainOrchestrator,
        tmp_bus: MessageBus,
    ) -> None:
        """Result messages use chain_id as the IPC correlation_id."""
        chain = orchestrator.create_chain("Correlation test", originator="hermes")
        orchestrator.delegate_down_chain(chain, "pm-test-e2e")
        orchestrator.propagate_result(chain, "Correlation verified")

        correlated = tmp_bus.get_by_correlation(chain.chain_id)
        assert len(correlated) >= 1, (
            "At least one message should be correlated with the chain_id"
        )
        for msg in correlated:
            assert msg.correlation_id == chain.chain_id

    def test_self_contained_no_live_state(self, tmp_path: Path) -> None:
        """Verify the test is fully self-contained — no shared state leaks.

        Creates its own registry / bus / orchestrator from scratch inside
        ``tmp_path`` and runs a minimal flow.  If this test passes it
        confirms each test run starts with a clean slate.
        """
        reg = _build_registry(str(tmp_path / "clean_registry.db"))
        bus = _build_bus(str(tmp_path / "clean_bus.db"))
        workers = str(tmp_path / "clean_workers")
        Path(workers).mkdir(parents=True, exist_ok=True)

        orch = _build_orchestrator(reg, bus, workers)

        chain = orch.create_chain("Isolated flow", originator="hermes")
        orch.delegate_down_chain(chain, "pm-test-e2e")
        sid = orch.spawn_worker(chain, "pm-test-e2e", "Isolated work")
        orch.complete_worker(chain, "pm-test-e2e", sid, "Isolated done")
        # Propagate only if complete_worker() did not already auto-complete the chain.
        if not chain.is_terminal:
            orch.propagate_result(chain, "Isolated done")

        assert chain.status == ChainStatus.COMPLETED

        bus.close()
        reg.close()
