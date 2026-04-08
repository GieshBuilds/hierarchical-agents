"""Tests for the execution layer fixes.

Covers:
1. ChainStore — persistent SQLite storage for delegation chains
2. ChainOrchestrator with ChainStore — chains survive across instances
3. GatewayHook task execution — TASK_REQUEST spawns workers
4. HermesProfileActivator with gateway_factory — on-demand activation
5. End-to-end: send → persist → activate → execute → propagate
"""
from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from core.integration.chain_store import ChainStore
from core.integration.delegation import (
    ChainStatus,
    DelegationChain,
    DelegationHop,
    HopStatus,
)
from core.integration.exceptions import ChainNotFound
from core.integration.orchestrator import ChainOrchestrator
from core.ipc.message_bus import MessageBus
from core.ipc.models import Message, MessagePriority, MessageType
from core.registry.profile_registry import ProfileRegistry
from core.workers.subagent_registry import SubagentRegistry

from integrations.hermes.activation import HermesProfileActivator
from integrations.hermes.config import HermesConfig
from integrations.hermes.gateway_hook import GatewayHook
from integrations.hermes.message_router import HermesMessageRouter
from integrations.hermes.worker_bridge import WorkerBridge


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Return a temp directory for databases."""
    return tmp_path


@pytest.fixture
def chain_store(tmp_db: Path) -> Generator[ChainStore, None, None]:
    store = ChainStore(str(tmp_db / "chains.db"))
    yield store
    store.close()


@pytest.fixture
def registry(tmp_db: Path) -> Generator[ProfileRegistry, None, None]:
    reg = ProfileRegistry(str(tmp_db / "registry.db"))
    reg.create_profile(
        name="cto",
        display_name="CTO",
        role="department_head",
        parent="hermes",
        department="engineering",
    )
    reg.create_profile(
        name="pm",
        display_name="PM",
        role="project_manager",
        parent="cto",
        department="engineering",
    )
    yield reg
    reg.close()


class RegistryAdapter:
    def __init__(self, registry: ProfileRegistry):
        self._registry = registry

    def get(self, name: str):
        return self._registry.get_profile(name)

    def get_profile(self, name: str):
        return self._registry.get_profile(name)

    def get_chain_of_command(self, name: str):
        return self._registry.get_chain_of_command(name)


@pytest.fixture
def bus(tmp_db: Path, registry: ProfileRegistry) -> Generator[MessageBus, None, None]:
    mb = MessageBus(str(tmp_db / "ipc.db"), profile_registry=RegistryAdapter(registry))
    yield mb
    mb.close()


@pytest.fixture
def worker_registry(tmp_db: Path) -> SubagentRegistry:
    return SubagentRegistry(":memory:")


@pytest.fixture
def orchestrator(
    registry: ProfileRegistry,
    bus: MessageBus,
    worker_registry: SubagentRegistry,
    chain_store: ChainStore,
) -> ChainOrchestrator:
    return ChainOrchestrator(
        registry=registry,
        bus=bus,
        worker_registry_factory=lambda pm: worker_registry,
        chain_store=chain_store,
    )


# ======================================================================
# 1. ChainStore persistence
# ======================================================================


class TestChainStore:
    """Test SQLite persistence for delegation chains."""

    def test_save_and_get(self, chain_store: ChainStore) -> None:
        chain = DelegationChain(
            task_description="Test task",
            originator="hermes",
        )
        chain_store.save(chain)
        loaded = chain_store.get(chain.chain_id)
        assert loaded.chain_id == chain.chain_id
        assert loaded.task_description == "Test task"
        assert loaded.originator == "hermes"
        assert loaded.status == ChainStatus.PENDING

    def test_get_not_found_raises(self, chain_store: ChainStore) -> None:
        with pytest.raises(ChainNotFound):
            chain_store.get("chain-nonexistent")

    def test_save_with_hops(self, chain_store: ChainStore) -> None:
        chain = DelegationChain(task_description="t", originator="hermes")
        hop = chain.add_hop("hermes", "cto")
        hop.mark_delegated("msg-123")
        chain_store.save(chain)

        loaded = chain_store.get(chain.chain_id)
        assert len(loaded.hops) == 1
        assert loaded.hops[0].from_profile == "hermes"
        assert loaded.hops[0].to_profile == "cto"
        assert loaded.hops[0].status == HopStatus.DELEGATED
        assert loaded.hops[0].message_id == "msg-123"

    def test_save_with_workers_and_results(self, chain_store: ChainStore) -> None:
        chain = DelegationChain(task_description="t", originator="hermes")
        chain.workers = ["sa-111", "sa-222"]
        chain.worker_results = {"sa-111": "done"}
        chain_store.save(chain)

        loaded = chain_store.get(chain.chain_id)
        assert loaded.workers == ["sa-111", "sa-222"]
        assert loaded.worker_results == {"sa-111": "done"}

    def test_update_overwrites(self, chain_store: ChainStore) -> None:
        chain = DelegationChain(task_description="t", originator="hermes")
        chain_store.save(chain)

        chain.activate()
        chain_store.save(chain)

        loaded = chain_store.get(chain.chain_id)
        assert loaded.status == ChainStatus.ACTIVE

    def test_list_all(self, chain_store: ChainStore) -> None:
        for i in range(3):
            chain = DelegationChain(task_description=f"task-{i}", originator="hermes")
            chain_store.save(chain)
        assert len(chain_store.list()) == 3

    def test_list_filter_by_status(self, chain_store: ChainStore) -> None:
        c1 = DelegationChain(task_description="a", originator="hermes")
        c2 = DelegationChain(task_description="b", originator="hermes")
        c2.activate()
        chain_store.save(c1)
        chain_store.save(c2)

        pending = chain_store.list(status=ChainStatus.PENDING)
        active = chain_store.list(status=ChainStatus.ACTIVE)
        assert len(pending) == 1
        assert len(active) == 1

    def test_list_filter_by_originator(self, chain_store: ChainStore) -> None:
        c1 = DelegationChain(task_description="a", originator="hermes")
        c2 = DelegationChain(task_description="b", originator="cto")
        chain_store.save(c1)
        chain_store.save(c2)

        result = chain_store.list(originator="cto")
        assert len(result) == 1
        assert result[0].originator == "cto"

    def test_delete(self, chain_store: ChainStore) -> None:
        chain = DelegationChain(task_description="t", originator="hermes")
        chain_store.save(chain)
        chain_store.delete(chain.chain_id)
        with pytest.raises(ChainNotFound):
            chain_store.get(chain.chain_id)


# ======================================================================
# 2. ChainOrchestrator with persistence
# ======================================================================


class TestOrchestratorPersistence:
    """Test that ChainOrchestrator persists chains to ChainStore."""

    def test_create_chain_persists(
        self, orchestrator: ChainOrchestrator, chain_store: ChainStore
    ) -> None:
        chain = orchestrator.create_chain(task="build it", originator="hermes")
        loaded = chain_store.get(chain.chain_id)
        assert loaded.task_description == "build it"

    def test_delegate_persists(
        self, orchestrator: ChainOrchestrator, chain_store: ChainStore
    ) -> None:
        chain = orchestrator.create_chain(task="build it", originator="hermes")
        orchestrator.delegate(chain, "hermes", "cto")
        loaded = chain_store.get(chain.chain_id)
        assert loaded.status == ChainStatus.ACTIVE
        assert len(loaded.hops) == 1
        assert loaded.hops[0].status == HopStatus.DELEGATED

    def test_chain_survives_new_orchestrator(
        self,
        registry: ProfileRegistry,
        bus: MessageBus,
        worker_registry: SubagentRegistry,
        chain_store: ChainStore,
    ) -> None:
        """A chain created by one orchestrator can be loaded by another."""
        orch1 = ChainOrchestrator(
            registry=registry,
            bus=bus,
            worker_registry_factory=lambda pm: worker_registry,
            chain_store=chain_store,
        )
        chain = orch1.create_chain(task="persist me", originator="hermes")
        chain_id = chain.chain_id

        # New orchestrator (simulating a new process) with same store
        orch2 = ChainOrchestrator(
            registry=registry,
            bus=bus,
            worker_registry_factory=lambda pm: worker_registry,
            chain_store=chain_store,
        )
        loaded = orch2.get_chain(chain_id)
        assert loaded.task_description == "persist me"

    def test_list_chains_uses_store(
        self,
        registry: ProfileRegistry,
        bus: MessageBus,
        worker_registry: SubagentRegistry,
        chain_store: ChainStore,
    ) -> None:
        orch1 = ChainOrchestrator(
            registry=registry,
            bus=bus,
            worker_registry_factory=lambda pm: worker_registry,
            chain_store=chain_store,
        )
        orch1.create_chain(task="t1", originator="hermes")
        orch1.create_chain(task="t2", originator="hermes")

        orch2 = ChainOrchestrator(
            registry=registry,
            bus=bus,
            worker_registry_factory=lambda pm: worker_registry,
            chain_store=chain_store,
        )
        chains = orch2.list_chains()
        assert len(chains) == 2


# ======================================================================
# 3. GatewayHook task execution
# ======================================================================


class TestGatewayHookExecution:
    """Test that GatewayHook spawns workers for TASK_REQUEST messages."""

    def test_task_request_spawns_worker(
        self, tmp_db: Path, registry: ProfileRegistry, bus: MessageBus
    ) -> None:
        worker_reg = SubagentRegistry(":memory:")
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
        )

        msg = Message(
            from_profile="cto",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "write tests", "chain_id": None},
        )
        hook.handle_message(msg)

        workers = worker_reg.list(project_manager="pm")
        assert len(workers) == 1
        assert "write tests" in workers[0].task_goal

    def test_task_request_with_chain_links(
        self,
        tmp_db: Path,
        registry: ProfileRegistry,
        bus: MessageBus,
        chain_store: ChainStore,
    ) -> None:
        """Worker is linked to chain via spawn_with_chain when chain_id is provided."""
        chain = DelegationChain(task_description="linked task", originator="hermes")
        chain.activate()
        chain_store.save(chain)

        worker_reg = SubagentRegistry(":memory:")
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
            chain_store=chain_store,
        )

        msg = Message(
            from_profile="cto",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "linked work", "chain_id": chain.chain_id},
        )
        hook.handle_message(msg)

        workers = worker_reg.list(project_manager="pm")
        assert len(workers) == 1

    def test_task_executor_runs_and_completes(
        self, tmp_db: Path
    ) -> None:
        """When a task_executor is provided, it runs and completes the worker."""
        worker_reg = SubagentRegistry(":memory:")
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        def my_executor(task, subagent_id, pm_profile):
            return f"Executed: {task}"

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
            task_executor=my_executor,
        )

        msg = Message(
            from_profile="cto",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "do something"},
        )
        hook.handle_message(msg)

        workers = worker_reg.list(project_manager="pm")
        assert len(workers) == 1
        assert workers[0].status == "completed"
        assert "Executed: do something" in workers[0].result_summary

    def test_non_task_request_no_worker(self, tmp_db: Path) -> None:
        """Non-TASK_REQUEST messages don't spawn workers."""
        worker_reg = SubagentRegistry(":memory:")
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
        )

        msg = Message(
            from_profile="cto",
            to_profile="pm",
            message_type=MessageType.BROADCAST,
            payload={"info": "hello"},
        )
        hook.handle_message(msg)

        workers = worker_reg.list(project_manager="pm")
        assert len(workers) == 0

    def test_no_worker_bridge_falls_back_to_log_only(self) -> None:
        """Without worker_bridge, handle_message just logs (no crash)."""
        hook = GatewayHook(profile_name="pm")

        msg = Message(
            from_profile="cto",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "ignored"},
        )
        # Should not raise
        hook.handle_message(msg)
        assert hook.stats.processed == 1

    def test_empty_task_skipped(self, tmp_db: Path) -> None:
        """TASK_REQUEST with empty task payload is skipped."""
        worker_reg = SubagentRegistry(":memory:")
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
        )

        msg = Message(
            from_profile="cto",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": ""},
        )
        hook.handle_message(msg)

        workers = worker_reg.list(project_manager="pm")
        assert len(workers) == 0


# ======================================================================
# 4. HermesProfileActivator with gateway_factory
# ======================================================================


class TestActivatorWithGateway:
    """Test that HermesProfileActivator launches real gateways."""

    def test_activate_launches_gateway(self) -> None:
        """activate_profile creates and starts a gateway via factory."""
        launched = []

        class FakeGateway:
            def __init__(self, name):
                self.name = name
                self._running = False

            def start(self):
                self._running = True
                launched.append(self.name)

            @property
            def is_running(self):
                return self._running

            def close(self):
                self._running = False

        config = HermesConfig()
        activator = HermesProfileActivator(
            config=config,
            gateway_factory=lambda name: FakeGateway(name),
        )

        assert activator.activate_profile("pm") is True
        assert activator.is_active("pm") is True
        assert launched == ["pm"]

    def test_deactivate_stops_gateway(self) -> None:
        closed = []

        class FakeGateway:
            def __init__(self, name):
                self.name = name
                self._running = False

            def start(self):
                self._running = True

            @property
            def is_running(self):
                return self._running

            def close(self):
                self._running = False
                closed.append(self.name)

        config = HermesConfig()
        activator = HermesProfileActivator(
            config=config,
            gateway_factory=lambda name: FakeGateway(name),
        )
        activator.activate_profile("pm")
        activator.deactivate_profile("pm")

        assert activator.is_active("pm") is False
        assert closed == ["pm"]

    def test_dead_gateway_detected(self) -> None:
        """is_active returns False if the gateway process has died."""

        class DyingGateway:
            def __init__(self, name):
                self._running = False

            def start(self):
                self._running = True

            @property
            def is_running(self):
                return self._running

            def close(self):
                self._running = False

        config = HermesConfig()
        gw_ref = [None]

        def factory(name):
            gw = DyingGateway(name)
            gw_ref[0] = gw
            return gw

        activator = HermesProfileActivator(config=config, gateway_factory=factory)
        activator.activate_profile("pm")
        assert activator.is_active("pm") is True

        # Simulate the gateway dying
        gw_ref[0]._running = False
        assert activator.is_active("pm") is False

    def test_activate_idempotent(self) -> None:
        call_count = [0]

        class FakeGateway:
            def __init__(self, name):
                self._running = False

            def start(self):
                self._running = True
                call_count[0] += 1

            @property
            def is_running(self):
                return self._running

            def close(self):
                self._running = False

        config = HermesConfig()
        activator = HermesProfileActivator(
            config=config,
            gateway_factory=lambda name: FakeGateway(name),
        )
        activator.activate_profile("pm")
        activator.activate_profile("pm")
        assert call_count[0] == 1  # Only started once

    def test_shutdown_stops_all(self) -> None:
        closed = []

        class FakeGateway:
            def __init__(self, name):
                self.name = name
                self._running = False

            def start(self):
                self._running = True

            @property
            def is_running(self):
                return self._running

            def close(self):
                self._running = False
                closed.append(self.name)

        config = HermesConfig()
        activator = HermesProfileActivator(
            config=config,
            gateway_factory=lambda name: FakeGateway(name),
        )
        activator.activate_profile("cto")
        activator.activate_profile("pm")
        activator.shutdown()

        assert activator.get_active_profiles() == []
        assert set(closed) == {"cto", "pm"}

    def test_stub_mode_backward_compatible(self) -> None:
        """Without gateway_factory, acts as in-memory stub."""
        activator = HermesProfileActivator(config=HermesConfig())
        assert activator.activate_profile("pm") is True
        assert activator.is_active("pm") is True
        assert activator.deactivate_profile("pm") is True
        assert activator.is_active("pm") is False


# ======================================================================
# 5. TASK_RESPONSE sent back to originator
# ======================================================================


class TestTaskResponseDelivery:
    """Verify that _run_and_complete sends TASK_RESPONSE via IPC."""

    def test_success_sends_response_to_originator(
        self, tmp_db: Path, registry: ProfileRegistry, bus: MessageBus
    ) -> None:
        """On success, originator receives TASK_RESPONSE with result."""
        worker_reg = SubagentRegistry(":memory:")
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        def executor(task, subagent_id, pm_profile):
            return "All done!"

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
            task_executor=executor,
            message_bus=bus,
        )

        msg = Message(
            from_profile="cto",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "build feature"},
        )
        hook.handle_message(msg)

        # cto should have a TASK_RESPONSE in their inbox
        responses = bus.poll("cto", limit=10)
        assert len(responses) == 1
        resp = responses[0]
        assert resp.message_type == MessageType.TASK_RESPONSE
        assert resp.from_profile == "pm"
        assert resp.to_profile == "cto"
        assert resp.payload["result"] == "All done!"
        assert resp.payload["task"] == "build feature"

    def test_failure_sends_error_response_to_originator(
        self, tmp_db: Path, registry: ProfileRegistry, bus: MessageBus
    ) -> None:
        """On failure, originator receives TASK_RESPONSE with error."""
        worker_reg = SubagentRegistry(":memory:")
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        def failing_executor(task, subagent_id, pm_profile):
            raise RuntimeError("disk full")

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
            task_executor=failing_executor,
            message_bus=bus,
        )

        msg = Message(
            from_profile="cto",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "write report"},
        )
        hook.handle_message(msg)

        responses = bus.poll("cto", limit=10)
        assert len(responses) == 1
        resp = responses[0]
        assert resp.message_type == MessageType.TASK_RESPONSE
        assert "disk full" in resp.payload["error"]

    def test_response_sent_without_chain(
        self, tmp_db: Path, registry: ProfileRegistry, bus: MessageBus
    ) -> None:
        """TASK_RESPONSE is sent even when no chain exists."""
        worker_reg = SubagentRegistry(":memory:")
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
            task_executor=lambda t, s, p: "no chain result",
            message_bus=bus,
        )

        msg = Message(
            from_profile="hermes",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "chainless task"},
        )
        hook.handle_message(msg)

        responses = bus.poll("hermes", limit=10)
        assert len(responses) == 1
        assert responses[0].payload["result"] == "no chain result"

    def test_response_carries_correlation_id(
        self, tmp_db: Path, registry: ProfileRegistry, bus: MessageBus
    ) -> None:
        """TASK_RESPONSE correlation_id matches the chain_id from the request."""
        worker_reg = SubagentRegistry(":memory:")
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
            task_executor=lambda t, s, p: "correlated",
            message_bus=bus,
        )

        msg = Message(
            from_profile="cto",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "tracked work", "chain_id": "chain-abc-123"},
        )
        hook.handle_message(msg)

        responses = bus.poll("cto", limit=10)
        assert len(responses) == 1
        assert responses[0].correlation_id == "chain-abc-123"

    def test_response_with_chain_and_orchestrator(
        self,
        tmp_db: Path,
        registry: ProfileRegistry,
        bus: MessageBus,
        chain_store: ChainStore,
    ) -> None:
        """TASK_RESPONSE is sent even when chain+orchestrator path is used."""
        worker_reg = SubagentRegistry(":memory:")
        orchestrator = ChainOrchestrator(
            registry=registry,
            bus=bus,
            worker_registry_factory=lambda pm: worker_reg,
            chain_store=chain_store,
        )

        chain = orchestrator.create_chain(task="chained work", originator="hermes")
        chain.activate()
        chain_store.save(chain)

        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            pm_profile="pm",
        )

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
            chain_store=chain_store,
            chain_orchestrator=orchestrator,
            task_executor=lambda t, s, p: "chain result",
            message_bus=bus,
        )

        msg = Message(
            from_profile="hermes",
            to_profile="pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "chained work", "chain_id": chain.chain_id},
        )
        hook.handle_message(msg)

        # hermes should receive the direct TASK_RESPONSE
        responses = bus.poll("hermes", limit=10)
        task_responses = [
            r for r in responses
            if r.message_type == MessageType.TASK_RESPONSE
        ]
        assert len(task_responses) >= 1
        assert any(r.payload.get("result") == "chain result" for r in task_responses)


# ======================================================================
# 6. End-to-end: send → persist → execute → propagate
# ======================================================================


class TestEndToEndExecution:
    """Full flow: create chain, delegate, gateway receives, worker executes."""

    def test_full_send_execute_propagate(
        self,
        tmp_db: Path,
        registry: ProfileRegistry,
        bus: MessageBus,
        chain_store: ChainStore,
    ) -> None:
        """End-to-end: hermes → cto → pm, PM gateway executes and completes."""
        worker_reg = SubagentRegistry(":memory:")

        # 1. Create orchestrator with persistence
        orchestrator = ChainOrchestrator(
            registry=registry,
            bus=bus,
            worker_registry_factory=lambda pm: worker_reg,
            chain_store=chain_store,
        )

        # 2. Create chain and delegate hermes → cto → pm
        chain = orchestrator.create_chain(task="ship the feature", originator="hermes")
        hops = orchestrator.delegate_down_chain(chain, target_profile="pm")
        assert len(hops) == 2  # hermes→cto, cto→pm

        # 3. Verify chain is persisted
        persisted = chain_store.get(chain.chain_id)
        assert persisted.status == ChainStatus.ACTIVE

        # 4. PM's gateway picks up the message and spawns a worker
        bridge = WorkerBridge(
            worker_registry_factory=lambda: worker_reg,
            workspace_dir=tmp_db / "workspace",
            chain_orchestrator=orchestrator,
            pm_profile="pm",
        )

        def executor(task, subagent_id, pm_profile):
            return "Feature shipped!"

        hook = GatewayHook(
            profile_name="pm",
            worker_bridge=bridge,
            chain_store=chain_store,
            chain_orchestrator=orchestrator,
            task_executor=executor,
            message_bus=bus,
        )

        # 5. Process the pending message for pm
        pm_messages = bus.poll("pm", limit=10)
        assert len(pm_messages) > 0

        for msg in pm_messages:
            hook.handle_message(msg)

        # 6. Verify worker was spawned and completed
        workers = worker_reg.list(project_manager="pm")
        assert len(workers) >= 1
        completed = [w for w in workers if w.status == "completed"]
        assert len(completed) >= 1
        assert "Feature shipped!" in completed[0].result_summary

        # 7. Verify chain state was fully updated
        final_chain = chain_store.get(chain.chain_id)
        assert final_chain.status == ChainStatus.COMPLETED
        assert len(final_chain.workers) == 1
        assert len(final_chain.worker_results) == 1
        assert "Feature shipped!" in list(final_chain.worker_results.values())[0]

        # 8. Verify hermes received the TASK_RESPONSE via IPC
        hermes_msgs = bus.poll("hermes", limit=10)
        task_responses = [
            m for m in hermes_msgs
            if m.message_type == MessageType.TASK_RESPONSE
        ]
        assert len(task_responses) >= 1
        assert any(
            "Feature shipped!" in (m.payload.get("result") or "")
            for m in task_responses
        )
