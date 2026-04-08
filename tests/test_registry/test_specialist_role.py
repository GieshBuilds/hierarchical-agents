"""Tests for the specialist role — persistent agents under any manager.

Specialists are the 4th level of the hierarchy:
    CEO → Department Head → Project Manager → Specialist

They have persistent profiles, task-scoped memory, and can send/receive
IPC messages across the org.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.registry.models import Role
from core.registry.profile_registry import ProfileRegistry
from core.registry.exceptions import InvalidHierarchy
from core.registry.integrity import (
    RULE_SPECIALIST_PARENT_PM,
    scan_integrity,
)
from core.memory.models import ROLE_SCOPE_MAP, MemoryScope, scope_for_role


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> ProfileRegistry:
    """Registry with hermes → cto → pm hierarchy."""
    reg = ProfileRegistry(str(tmp_path / "registry.db"))
    reg.create_profile(name="cto", role="department_head", parent="hermes", _skip_onboarding=True)
    reg.create_profile(name="pm", role="project_manager", parent="cto", _skip_onboarding=True)
    yield reg
    reg.close()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestSpecialistCRUD:
    """Create, read, list specialists."""

    def test_create_specialist_under_pm(self, registry: ProfileRegistry) -> None:
        p = registry.create_profile(
            name="api-agent",
            display_name="API Agent",
            role="specialist",
            parent="pm",
            department="engineering",
        )
        assert p.role == Role.SPECIALIST.value
        assert p.parent_profile == "pm"

    def test_create_multiple_specialists(self, registry: ProfileRegistry) -> None:
        registry.create_profile(name="agent-a", role="specialist", parent="pm")
        registry.create_profile(name="agent-b", role="specialist", parent="pm")
        reports = registry.list_reports("pm")
        specialist_names = [r.profile_name for r in reports if r.role == "specialist"]
        assert set(specialist_names) == {"agent-a", "agent-b"}

    def test_list_by_role(self, registry: ProfileRegistry) -> None:
        registry.create_profile(name="s1", role="specialist", parent="pm")
        specs = registry.list_profiles(role="specialist")
        assert len(specs) == 1
        assert specs[0].profile_name == "s1"

    def test_specialist_in_org_tree(self, registry: ProfileRegistry) -> None:
        registry.create_profile(name="tree-agent", role="specialist", parent="pm")
        tree = registry.get_org_tree()
        # Walk: hermes -> cto -> pm -> tree-agent
        pm_node = tree["children"][0]["children"][0]
        assert pm_node["profile_name"] == "pm"
        assert len(pm_node["children"]) == 1
        assert pm_node["children"][0]["profile_name"] == "tree-agent"
        assert pm_node["children"][0]["role"] == "specialist"


# ---------------------------------------------------------------------------
# Hierarchy validation
# ---------------------------------------------------------------------------


class TestSpecialistHierarchyValidation:
    """Specialists can be under CEO, dept head, or PM — not under other specialists."""

    def test_specialist_under_ceo_allowed(self, registry: ProfileRegistry) -> None:
        p = registry.create_profile(name="ceo-spec", role="specialist", parent="hermes")
        assert p.parent_profile == "hermes"

    def test_specialist_under_dept_head_allowed(self, registry: ProfileRegistry) -> None:
        p = registry.create_profile(name="cto-spec", role="specialist", parent="cto")
        assert p.parent_profile == "cto"

    def test_specialist_no_parent_rejected(self, registry: ProfileRegistry) -> None:
        with pytest.raises(InvalidHierarchy, match="requires a parent"):
            registry.create_profile(name="bad", role="specialist", parent=None)

    def test_specialist_under_specialist_allowed(self, registry: ProfileRegistry) -> None:
        """Flexible hierarchy allows specialists to parent to other specialists."""
        registry.create_profile(name="s1", role="specialist", parent="pm")
        s2 = registry.create_profile(name="s2", role="specialist", parent="s1")
        assert s2.parent_profile == "s1"


# ---------------------------------------------------------------------------
# Chain of command
# ---------------------------------------------------------------------------


class TestSpecialistChainOfCommand:
    """Specialists appear in chain-of-command queries."""

    def test_chain_of_command_includes_all_levels(self, registry: ProfileRegistry) -> None:
        registry.create_profile(name="deep-agent", role="specialist", parent="pm")
        chain = registry.get_chain_of_command("deep-agent")
        names = [p.profile_name for p in chain]
        assert names == ["deep-agent", "pm", "cto", "hermes"]

    def test_delegation_path_reaches_specialist(self, registry: ProfileRegistry) -> None:
        """ChainOrchestrator.delegate_down_chain works to specialist depth."""
        from core.integration.orchestrator import ChainOrchestrator
        from core.ipc.message_bus import MessageBus
        from core.workers.subagent_registry import SubagentRegistry

        registry.create_profile(name="spec-agent", role="specialist", parent="pm")

        bus = MessageBus(":memory:")
        orch = ChainOrchestrator(
            registry=registry,
            bus=bus,
            worker_registry_factory=lambda pm: SubagentRegistry(":memory:"),
        )

        chain = orch.create_chain("Deep delegation", originator="hermes")
        hops = orch.delegate_down_chain(chain, "spec-agent")

        assert len(hops) == 3
        assert hops[0].from_profile == "hermes"
        assert hops[0].to_profile == "cto"
        assert hops[1].from_profile == "cto"
        assert hops[1].to_profile == "pm"
        assert hops[2].from_profile == "pm"
        assert hops[2].to_profile == "spec-agent"
        bus.close()


# ---------------------------------------------------------------------------
# IPC messaging
# ---------------------------------------------------------------------------


class TestSpecialistIPC:
    """Specialists can send and receive IPC messages."""

    def test_specialist_sends_and_receives(self, registry: ProfileRegistry) -> None:
        from core.ipc.message_bus import MessageBus
        from core.ipc.models import MessageType, MessagePriority

        registry.create_profile(name="ipc-agent", role="specialist", parent="pm")

        # Build adapter for bus validation
        class _Adapter:
            def __init__(self, r): self._r = r
            def get(self, name): return self._r.get_profile(name)

        bus = MessageBus(":memory:", profile_registry=_Adapter(registry))

        # Specialist sends to CEO
        msg_id = bus.send(
            from_profile="ipc-agent",
            to_profile="hermes",
            message_type=MessageType.STATUS_RESPONSE,
            payload={"status": "task complete"},
        )
        assert msg_id

        # CEO sends to specialist
        msg_id2 = bus.send(
            from_profile="hermes",
            to_profile="ipc-agent",
            message_type=MessageType.TASK_REQUEST,
            payload={"task": "do something"},
        )

        msgs = bus.poll("ipc-agent")
        assert len(msgs) == 1
        assert msgs[0].payload["task"] == "do something"
        bus.close()


# ---------------------------------------------------------------------------
# Integrity checking
# ---------------------------------------------------------------------------


class TestSpecialistIntegrity:
    """Integrity scanner catches misplaced specialists."""

    def test_clean_specialist_no_issues(self, registry: ProfileRegistry) -> None:
        registry.create_profile(name="clean-spec", role="specialist", parent="pm")
        issues = scan_integrity(registry)
        spec_issues = [i for i in issues if i.rule_violated == RULE_SPECIALIST_PARENT_PM]
        assert len(spec_issues) == 0

    def test_specialist_under_specialist_no_integrity_issue(self, registry: ProfileRegistry) -> None:
        """Flexible hierarchy allows specialist under specialist — no integrity error."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        registry.create_profile(name="valid-spec", role="specialist", parent="pm")
        # Raw-insert a specialist under another specialist
        with registry._cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO profiles VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("nested-spec", "Nested Spec", "specialist", "valid-spec",
                 "engineering", "active", now, now, None, None),
            )

        issues = scan_integrity(registry)
        # No role-specific parent issues should be raised
        role_issues = [i for i in issues if i.profile_name == "nested-spec"]
        assert len(role_issues) == 0


# ---------------------------------------------------------------------------
# Memory scoping
# ---------------------------------------------------------------------------


class TestSpecialistMemoryScope:
    """Specialists get task-scoped memory."""

    def test_role_scope_map_has_specialist(self) -> None:
        assert "specialist" in ROLE_SCOPE_MAP
        assert ROLE_SCOPE_MAP["specialist"] == MemoryScope.task

    def test_scope_for_role_specialist(self) -> None:
        assert scope_for_role("specialist") == MemoryScope.task
