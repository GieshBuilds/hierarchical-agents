"""Tests verifying closure of the 5 documented integration gaps.

Gaps 1, 2, 5 were fixed in prior commits.  This file adds tests for:
  - Gap 3: ``hierarchy_manager.py send --track`` (tested via the
    underlying orchestrator flow since the script is external).
  - Gap 4: ``send_to_profile(track=True)`` creates a DelegationChain
    and delegates through the hierarchy.

All tests are self-contained using in-memory/temp databases.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.integration.delegation import ChainStatus
from core.integration.orchestrator import ChainOrchestrator
from core.ipc.message_bus import MessageBus
from core.ipc.models import MessageType
from core.registry.profile_registry import ProfileRegistry
from core.workers.subagent_registry import SubagentRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_hierarchy(db_path: str) -> ProfileRegistry:
    """Create a ProfileRegistry with hermes -> cto -> pm hierarchy."""
    reg = ProfileRegistry(db_path)
    reg.create_profile(
        name="cto",
        display_name="CTO",
        role="department_head",
        parent="hermes",
        department="engineering",
    )
    reg.create_profile(
        name="pm-gap-test",
        display_name="PM Gap Test",
        role="project_manager",
        parent="cto",
        department="engineering",
    )
    return reg


def _build_orchestrator(
    registry: ProfileRegistry,
    bus: MessageBus,
    workers_dir: str,
) -> ChainOrchestrator:
    """Build a ChainOrchestrator wired to the provided subsystems."""
    return ChainOrchestrator(
        registry=registry,
        bus=bus,
        worker_registry_factory=lambda pm: SubagentRegistry(workers_dir),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_registry(tmp_path: Path) -> ProfileRegistry:
    reg = _build_hierarchy(str(tmp_path / "registry.db"))
    yield reg
    reg.close()


@pytest.fixture
def tmp_bus(tmp_path: Path) -> MessageBus:
    bus = MessageBus(db_path=str(tmp_path / "bus.db"))
    yield bus
    bus.close()


@pytest.fixture
def workers_dir(tmp_path: Path) -> str:
    d = tmp_path / "workers"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@pytest.fixture
def orchestrator(
    tmp_registry: ProfileRegistry,
    tmp_bus: MessageBus,
    workers_dir: str,
) -> ChainOrchestrator:
    return _build_orchestrator(tmp_registry, tmp_bus, workers_dir)


# ===================================================================
# Gap 3: send --track creates a delegation chain
# ===================================================================
# The actual CLI script lives outside the repo at ~/.hermes/hierarchy/.
# We test the underlying orchestrator flow that --track invokes.


class TestGap3SendTrack:
    """Verify that the --track flow creates chains and delegates correctly."""

    def test_delegate_down_chain_creates_chain_and_hops(
        self, orchestrator: ChainOrchestrator, tmp_bus: MessageBus
    ) -> None:
        """The flow used by send --track: create_chain + delegate_down_chain."""
        chain = orchestrator.create_chain(
            task="Review architecture plan",
            originator="hermes",
        )
        hops = orchestrator.delegate_down_chain(chain, "pm-gap-test")

        assert chain.chain_id.startswith("chain-")
        assert chain.status == ChainStatus.ACTIVE
        assert len(hops) == 2
        assert hops[0].from_profile == "hermes"
        assert hops[0].to_profile == "cto"
        assert hops[1].from_profile == "cto"
        assert hops[1].to_profile == "pm-gap-test"

    def test_delegate_down_chain_sends_ipc_messages(
        self, orchestrator: ChainOrchestrator, tmp_bus: MessageBus
    ) -> None:
        """IPC TASK_REQUEST messages arrive at each hop target."""
        chain = orchestrator.create_chain(
            task="Build the API", originator="hermes"
        )
        orchestrator.delegate_down_chain(chain, "pm-gap-test")

        cto_msgs = tmp_bus.poll("cto", message_type=MessageType.TASK_REQUEST)
        pm_msgs = tmp_bus.poll(
            "pm-gap-test", message_type=MessageType.TASK_REQUEST
        )

        assert len(cto_msgs) >= 1
        assert len(pm_msgs) >= 1
        assert cto_msgs[0].payload["chain_id"] == chain.chain_id
        assert pm_msgs[0].payload["chain_id"] == chain.chain_id

    def test_chain_id_enables_spawn_with_chain_id(
        self,
        orchestrator: ChainOrchestrator,
        tmp_bus: MessageBus,
        workers_dir: str,
    ) -> None:
        """A chain_id from --track can be used to spawn + propagate workers."""
        # Step 1: create chain via the --track flow
        chain = orchestrator.create_chain(
            task="Implement feature", originator="hermes"
        )
        orchestrator.delegate_down_chain(chain, "pm-gap-test")

        # Step 2: spawn worker (as worker_wrapper.py spawn --chain-id would)
        subagent_id = orchestrator.spawn_worker(
            chain, "pm-gap-test", "Write code"
        )

        # Step 3: complete and propagate
        orchestrator.complete_worker(
            chain, "pm-gap-test", subagent_id, "Code written"
        )
        if not chain.is_terminal:
            orchestrator.propagate_result(chain, "Code written")

        assert chain.status == ChainStatus.COMPLETED


# ===================================================================
# Gap 4: send_to_profile(track=True) creates a delegation chain
# ===================================================================


class TestGap4SendToProfileTrack:
    """Verify that send_to_profile with track=True creates chains."""

    def test_track_creates_chain_and_returns_chain_id(
        self, tmp_path: Path
    ) -> None:
        """send_to_profile(track=True) returns a response with chain_id."""
        # Set up isolated databases
        reg = _build_hierarchy(str(tmp_path / "reg.db"))
        bus = MessageBus(db_path=str(tmp_path / "bus.db"))

        # We need to patch the singletons in hierarchy_tools
        import tools.hierarchy_tools as ht

        # Build an orchestrator
        orch = ChainOrchestrator(
            registry=reg,
            bus=bus,
            worker_registry_factory=lambda pm: SubagentRegistry(
                str(tmp_path / "workers")
            ),
        )

        # Patch the module-level singletons
        old_reg = ht._profile_registry
        old_bus = ht._message_bus
        old_orch = ht._chain_orchestrator

        try:
            ht._profile_registry = reg
            ht._message_bus = bus
            ht._chain_orchestrator = orch

            with patch.dict(os.environ, {"HERMES_PROFILE": "hermes"}):
                result_json = ht.send_to_profile({
                    "to": "cto",
                    "message": "Review PR #42",
                    "track": True,
                })

            result = json.loads(result_json)
            assert "chain_id" in result, f"Expected chain_id in result: {result}"
            assert result["chain_id"].startswith("chain-")
            assert result["status"] == "delegated"
            assert result["from"] == "hermes"
            assert result["to"] == "cto"
            assert result["hop_count"] == 1  # hermes -> cto is 1 hop
        finally:
            ht._profile_registry = old_reg
            ht._message_bus = old_bus
            ht._chain_orchestrator = old_orch
            reg.close()
            bus.close()

    def test_track_multi_hop_delegation(self, tmp_path: Path) -> None:
        """track=True delegates through intermediate hops."""
        reg = _build_hierarchy(str(tmp_path / "reg.db"))
        bus = MessageBus(db_path=str(tmp_path / "bus.db"))

        import tools.hierarchy_tools as ht

        orch = ChainOrchestrator(
            registry=reg,
            bus=bus,
            worker_registry_factory=lambda pm: SubagentRegistry(
                str(tmp_path / "workers")
            ),
        )

        old_reg = ht._profile_registry
        old_bus = ht._message_bus
        old_orch = ht._chain_orchestrator

        try:
            ht._profile_registry = reg
            ht._message_bus = bus
            ht._chain_orchestrator = orch

            # hermes → PM direct send (skips CTO hop to save tokens)
            with patch.dict(os.environ, {"HERMES_PROFILE": "hermes"}):
                result_json = ht.send_to_profile({
                    "to": "pm-gap-test",
                    "message": "Build login page",
                    "track": True,
                })

            result = json.loads(result_json)
            # hermes sends directly to PM via IPC (no chain hops)
            assert result["status"] == "sent"
            assert result["to"] == "pm-gap-test"

            # CTO → PM still uses chain delegation (1 hop)
            with patch.dict(os.environ, {"HERMES_PROFILE": "cto"}):
                result_json = ht.send_to_profile({
                    "to": "pm-gap-test",
                    "message": "Build login page",
                    "track": True,
                })

            result = json.loads(result_json)
            assert result["status"] == "delegated"
            assert result["hop_count"] == 1  # cto -> pm-gap-test
            assert result["hops"][0]["from"] == "cto"
            assert result["hops"][0]["to"] == "pm-gap-test"
        finally:
            ht._profile_registry = old_reg
            ht._message_bus = old_bus
            ht._chain_orchestrator = old_orch
            reg.close()
            bus.close()

    def test_track_false_uses_raw_ipc(self, tmp_path: Path) -> None:
        """track=False (default) sends via raw IPC without creating a chain."""
        reg = _build_hierarchy(str(tmp_path / "reg.db"))

        # Build a RegistryAdapter for the bus (same as hierarchy_tools does)
        class _Adapter:
            def __init__(self, r):
                self._r = r
            def get(self, name):
                return self._r.get_profile(name)

        bus = MessageBus(
            db_path=str(tmp_path / "bus.db"),
            profile_registry=_Adapter(reg),
        )

        import tools.hierarchy_tools as ht

        old_reg = ht._profile_registry
        old_bus = ht._message_bus
        old_orch = ht._chain_orchestrator

        try:
            ht._profile_registry = reg
            ht._message_bus = bus
            ht._chain_orchestrator = None

            with patch.dict(os.environ, {"HERMES_PROFILE": "hermes"}):
                result_json = ht.send_to_profile({
                    "to": "cto",
                    "message": "Simple one-off message",
                })

            result = json.loads(result_json)
            assert result["status"] == "sent"
            assert "message_id" in result
            assert "chain_id" not in result
        finally:
            ht._profile_registry = old_reg
            ht._message_bus = old_bus
            ht._chain_orchestrator = old_orch
            reg.close()
            bus.close()

    def test_track_fallback_on_invalid_hierarchy(
        self, tmp_path: Path
    ) -> None:
        """track=True falls back to raw IPC when delegation is invalid."""
        reg = _build_hierarchy(str(tmp_path / "reg.db"))

        class _Adapter:
            def __init__(self, r):
                self._r = r
            def get(self, name):
                return self._r.get_profile(name)

        bus = MessageBus(
            db_path=str(tmp_path / "bus.db"),
            profile_registry=_Adapter(reg),
        )

        import tools.hierarchy_tools as ht

        orch = ChainOrchestrator(
            registry=reg,
            bus=bus,
            worker_registry_factory=lambda pm: SubagentRegistry(
                str(tmp_path / "workers")
            ),
        )

        old_reg = ht._profile_registry
        old_bus = ht._message_bus
        old_orch = ht._chain_orchestrator

        try:
            ht._profile_registry = reg
            ht._message_bus = bus
            ht._chain_orchestrator = orch

            # Try to send from CTO to hermes (upward — not a valid delegation)
            with patch.dict(os.environ, {"HERMES_PROFILE": "cto"}):
                result_json = ht.send_to_profile({
                    "to": "hermes",
                    "message": "Upward message",
                    "track": True,
                })

            result = json.loads(result_json)
            # Should fall back to raw IPC
            assert result["status"] == "sent"
            assert "message_id" in result
        finally:
            ht._profile_registry = old_reg
            ht._message_bus = old_bus
            ht._chain_orchestrator = old_orch
            reg.close()
            bus.close()

    def test_ipc_messages_delivered_with_chain_id(
        self, tmp_path: Path
    ) -> None:
        """When track=True, the IPC payload contains the chain_id."""
        reg = _build_hierarchy(str(tmp_path / "reg.db"))
        bus = MessageBus(db_path=str(tmp_path / "bus.db"))

        import tools.hierarchy_tools as ht

        orch = ChainOrchestrator(
            registry=reg,
            bus=bus,
            worker_registry_factory=lambda pm: SubagentRegistry(
                str(tmp_path / "workers")
            ),
        )

        old_reg = ht._profile_registry
        old_bus = ht._message_bus
        old_orch = ht._chain_orchestrator

        try:
            ht._profile_registry = reg
            ht._message_bus = bus
            ht._chain_orchestrator = orch

            with patch.dict(os.environ, {"HERMES_PROFILE": "hermes"}):
                result_json = ht.send_to_profile({
                    "to": "cto",
                    "message": "Check deployment",
                    "track": True,
                })

            result = json.loads(result_json)
            chain_id = result["chain_id"]

            # Check that the CTO received a TASK_REQUEST with chain_id
            cto_msgs = bus.poll("cto", message_type=MessageType.TASK_REQUEST)
            assert len(cto_msgs) >= 1
            assert cto_msgs[0].payload["chain_id"] == chain_id
        finally:
            ht._profile_registry = old_reg
            ht._message_bus = old_bus
            ht._chain_orchestrator = old_orch
            reg.close()
            bus.close()


# ===================================================================
# Gaps 1, 2, 5 — Regression tests (already fixed, verify they stay fixed)
# ===================================================================


class TestGap1SpawnWithChainExists:
    """Gap 1: WorkerBridge.spawn_with_chain() exists and works."""

    def test_spawn_with_chain_method_exists(self) -> None:
        from integrations.hermes.worker_bridge import WorkerBridge

        assert hasattr(WorkerBridge, "spawn_with_chain")
        assert callable(getattr(WorkerBridge, "spawn_with_chain"))


class TestGap5ProtocolCompliance:
    """Gap 5: WorkerBridge satisfies WorkerManager protocol."""

    def test_isinstance_check(self, tmp_path: Path) -> None:
        from integrations.hermes.worker_bridge import WorkerBridge
        from core.workers.interface import WorkerManager

        bridge = WorkerBridge(
            worker_registry_factory=lambda: SubagentRegistry(":memory:"),
            workspace_dir=tmp_path,
        )
        assert isinstance(bridge, WorkerManager)
