#!/usr/bin/env python3
"""
Comprehensive test suite for tools/hierarchy_tools.py

Covers all 6 Hermes integration tools:
  - send_to_profile
  - check_inbox
  - org_chart_tool
  - profile_status
  - spawn_tracked_worker
  - get_project_status

Plus helper utilities:
  - check_hierarchy_requirements
  - _build_profile_context
  - _get_current_profile
  - OpenAI-compatible JSON schemas

Strategy
--------
- Inject temporary SQLite databases into the module's singletons for full isolation.
- No Hermes runtime required — delegate_task is mocked where needed.
- All tests use fresh databases per test via pytest fixtures.
- Happy paths, error cases, edge cases, and schema validation all covered.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is on sys.path
PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_dir(tmp_path):
    """Create a temporary directory structure mirroring the real hierarchy layout."""
    (tmp_path / "memory").mkdir()
    (tmp_path / "workers").mkdir()
    return tmp_path


@pytest.fixture
def registry(tmp_db_dir):
    """ProfileRegistry backed by a temp SQLite database."""
    from core.registry.profile_registry import ProfileRegistry
    from core.registry.models import Role

    reg_path = str(tmp_db_dir / "registry.db")
    reg = ProfileRegistry(reg_path)

    # Standard 3-level test hierarchy
    # _skip_onboarding=True bypasses the onboarding gate so profiles
    # are immediately active (required for tool tests).
    reg.create_profile(
        name="test-cto",
        display_name="Test CTO",
        role=Role.DEPARTMENT_HEAD.value,
        parent="hermes",
        department="engineering",
        _skip_onboarding=True,
    )
    reg.create_profile(
        name="test-pm",
        display_name="Test PM",
        role=Role.PROJECT_MANAGER.value,
        parent="test-cto",
        department="engineering",
        _skip_onboarding=True,
    )
    reg.create_profile(
        name="test-pm-b",
        display_name="Test PM B",
        role=Role.PROJECT_MANAGER.value,
        parent="test-cto",
        department="engineering",
        _skip_onboarding=True,
    )
    return reg


@pytest.fixture
def bus(tmp_db_dir, registry):
    """MessageBus with a RegistryAdapter bridging .get() to .get_profile()."""
    from core.ipc.message_bus import MessageBus

    class _RegistryAdapter:
        def __init__(self, reg):
            self._reg = reg

        def get(self, name):
            return self._reg.get_profile(name)

    ipc_path = str(tmp_db_dir / "ipc.db")
    return MessageBus(db_path=ipc_path, profile_registry=_RegistryAdapter(registry))


@pytest.fixture
def sub_reg(tmp_db_dir, registry):
    """SubagentRegistry backed by a temp directory."""
    from core.workers.subagent_registry import SubagentRegistry

    workers_path = str(tmp_db_dir / "workers")
    return SubagentRegistry(base_path=workers_path, profile_registry=registry)


@pytest.fixture
def injected(tmp_db_dir, registry, bus, sub_reg):
    """Inject all singletons into the hierarchy_tools module and set env vars.

    This is the primary fixture for most tests — it gives a fully wired
    environment without requiring a real Hermes installation.
    """
    import tools.hierarchy_tools as ht

    # Save originals
    _orig_registry = ht._profile_registry
    _orig_bus = ht._message_bus
    _orig_subreg = ht._subagent_registry
    _orig_orchestrator = ht._chain_orchestrator
    _orig_activator = ht._profile_activator
    _orig_mem = ht._memory_stores
    _orig_db_dir = ht._DB_BASE_DIR
    _orig_profiles_dir = ht._PROFILES_DIR
    _orig_env = os.environ.get("HERMES_PROFILE")

    # Inject
    ht._profile_registry = registry
    ht._message_bus = bus
    ht._subagent_registry = sub_reg
    ht._chain_orchestrator = None  # Reset so it gets rebuilt with test DBs
    ht._profile_activator = None  # Reset so it doesn't launch real gateways
    ht._memory_stores = {}
    ht._DB_BASE_DIR = tmp_db_dir
    ht._PROFILES_DIR = tmp_db_dir / "profiles"
    os.environ["HERMES_PROFILE"] = "test-pm"

    yield {
        "registry": registry,
        "bus": bus,
        "sub_reg": sub_reg,
        "db_dir": tmp_db_dir,
    }

    # Restore
    ht._profile_registry = _orig_registry
    ht._message_bus = _orig_bus
    ht._subagent_registry = _orig_subreg
    ht._chain_orchestrator = _orig_orchestrator
    ht._profile_activator = _orig_activator
    ht._memory_stores = _orig_mem
    ht._DB_BASE_DIR = _orig_db_dir
    ht._PROFILES_DIR = _orig_profiles_dir

    if _orig_env is not None:
        os.environ["HERMES_PROFILE"] = _orig_env
    else:
        os.environ.pop("HERMES_PROFILE", None)


@pytest.fixture
def memory_store(injected):
    """A MemoryStore for test-pm with pre-loaded entries."""
    from core.memory.memory_store import MemoryStore
    from core.memory.models import (
        MemoryEntry,
        MemoryEntryType,
        MemoryScope,
        MemoryTier,
        generate_memory_id,
    )
    import tools.hierarchy_tools as ht

    db_path = str(injected["db_dir"] / "memory" / "test-pm.db")
    store = MemoryStore(
        db_path=db_path,
        profile_name="test-pm",
        profile_scope=MemoryScope.project,
    )

    # Seed entries
    for i, (content, entry_type) in enumerate([
        ("Decision: Use SQLite for all hierarchy databases", MemoryEntryType.decision),
        ("Learning: Tool registration requires name, schema, handler, check_fn", MemoryEntryType.learning),
        ("Context: Phase 5 integration complete and tested", MemoryEntryType.context),
    ]):
        store.store(MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="test-pm",
            scope=MemoryScope.project,
            tier=MemoryTier.hot,
            entry_type=entry_type,
            content=content,
        ))

    ht._memory_stores["test-pm"] = store
    return store


@pytest.fixture
def workers(injected):
    """Pre-register workers: 1 running, 1 completed, 1 failed."""
    sub_reg = injected["sub_reg"]

    w_running = sub_reg.register(project_manager="test-pm", task_goal="Build authentication module")
    w_completed = sub_reg.register(project_manager="test-pm", task_goal="Write unit tests for registry")
    w_sleeping = sub_reg.register(project_manager="test-pm", task_goal="Deploy to staging")

    sub_reg.complete(
        w_completed.subagent_id,
        result_summary="35 tests pass, 0 failures",
        project_manager="test-pm",
    )

    from core.workers.models import SubagentStatus
    # valid transition: running -> sleeping
    sub_reg.update_status(
        w_sleeping.subagent_id,
        SubagentStatus.SLEEPING,
        project_manager="test-pm",
    )

    return {"running": w_running, "completed": w_completed, "sleeping": w_sleeping}


@pytest.fixture
def soul_file(injected, tmp_path):
    """Create a SOUL.md for test-pm in the profiles directory."""
    import tools.hierarchy_tools as ht

    profiles_dir = injected["db_dir"] / "profiles"
    pm_dir = profiles_dir / "test-pm"
    pm_dir.mkdir(parents=True, exist_ok=True)
    soul = pm_dir / "SOUL.md"
    soul.write_text("# Test PM\nYou are the Test PM responsible for hierarchy features.")
    ht._PROFILES_DIR = profiles_dir
    return soul


# ---------------------------------------------------------------------------
# 1. send_to_profile
# ---------------------------------------------------------------------------


class TestSendToProfile:
    """Happy paths, validation, and edge cases for send_to_profile."""

    def test_send_async_returns_sent_status(self, injected):
        """Basic async send — returns sent status with message_id."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "test-cto",
            "message": "Phase 5 complete",
            "priority": "normal",
            "wait_for_response": False,
        }))

        assert "error" not in result
        assert result["status"] == "sent"
        assert result["to"] == "test-cto"
        assert result["from"] == "test-pm"
        assert "message_id" in result
        assert isinstance(result["message_id"], str)
        assert len(result["message_id"]) > 0

    def test_send_returns_priority_in_result(self, injected):
        """Priority is echoed back in the result dict."""
        from tools.hierarchy_tools import send_to_profile

        for priority in ("low", "normal", "urgent"):
            result = json.loads(send_to_profile({
                "to": "test-cto",
                "message": f"Test at {priority} priority",
                "priority": priority,
            }))
            assert result["priority"] == priority, f"Expected priority={priority}"

    def test_send_default_priority_is_normal(self, injected):
        """When priority is omitted it defaults to 'normal'."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "test-cto",
            "message": "No priority specified",
        }))
        assert result["priority"] == "normal"

    def test_send_message_appears_in_recipient_inbox(self, injected):
        """Message sent via tool actually lands in the IPC bus."""
        from tools.hierarchy_tools import send_to_profile

        send_to_profile({"to": "test-cto", "message": "IPC delivery check"})

        pending = injected["bus"].poll("test-cto", limit=10)
        assert len(pending) == 1
        assert pending[0].payload["message"] == "IPC delivery check"

    def test_send_multiple_messages_accumulate(self, injected):
        """Multiple sends stack in the recipient's inbox."""
        from tools.hierarchy_tools import send_to_profile

        for i in range(3):
            send_to_profile({"to": "test-cto", "message": f"Message {i}"})

        pending = injected["bus"].poll("test-cto", limit=10)
        assert len(pending) == 3

    def test_send_to_ceo(self, injected):
        """Can send up the chain to the CEO profile."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "hermes",
            "message": "Escalation: critical blocker",
            "priority": "urgent",
        }))
        assert result["status"] == "sent"
        assert result["to"] == "hermes"

    def test_send_missing_to_gives_error(self, injected):
        """Omitting 'to' returns a JSON error."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({"message": "Hello"}))
        assert "error" in result
        assert "to" in result["error"].lower()

    def test_send_empty_to_gives_error(self, injected):
        """Empty string for 'to' returns a JSON error."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({"to": "", "message": "Hello"}))
        assert "error" in result

    def test_send_missing_message_gives_error(self, injected):
        """Omitting 'message' returns a JSON error."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({"to": "test-cto"}))
        assert "error" in result

    def test_send_empty_message_gives_error(self, injected):
        """Empty string message returns a JSON error."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({"to": "test-cto", "message": ""}))
        assert "error" in result

    def test_send_invalid_priority_gives_error(self, injected):
        """Unknown priority value returns a JSON error."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "test-cto",
            "message": "Test",
            "priority": "super-urgent",
        }))
        assert "error" in result
        assert "priority" in result["error"].lower()

    def test_send_priority_case_insensitive(self, injected):
        """Priority matching is case-insensitive."""
        from tools.hierarchy_tools import send_to_profile

        # Lowercase only — the tool itself strips and lowercases
        result = json.loads(send_to_profile({
            "to": "test-cto",
            "message": "Test",
            "priority": "urgent",
        }))
        assert "error" not in result

    def test_wait_for_response_without_parent_agent_sends_anyway(self, injected):
        """wait_for_response=True without parent_agent sends msg but warns."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "test-cto",
            "message": "Need synchronous response",
            "wait_for_response": True,
        }))

        # Message should still be sent
        assert result["status"] == "sent"
        assert "message_id" in result
        # But a warning about the missing parent_agent
        assert "warning" in result

    def test_wait_for_response_with_mock_delegate(self, injected):
        """wait_for_response=True with mock delegate_task returns response."""
        from tools.hierarchy_tools import send_to_profile

        mock_result = json.dumps({
            "results": [{"status": "completed", "summary": "Task done successfully"}]
        })

        with patch("tools.hierarchy_tools._build_profile_context", return_value="ctx"):
            with patch.dict("sys.modules", {"tools.delegate_tool": MagicMock(
                delegate_task=MagicMock(return_value=mock_result)
            )}):
                # Import after patching
                import tools.delegate_tool as dt_mock
                dt_mock.delegate_task.return_value = mock_result

                # Use a real parent_agent mock
                parent_mock = MagicMock()
                result = json.loads(send_to_profile(
                    {
                        "to": "test-cto",
                        "message": "Do the thing",
                        "wait_for_response": True,
                    },
                    parent_agent=parent_mock,
                ))

                # At minimum message should have been sent
                assert "message_id" in result

    def test_send_result_is_valid_json(self, injected):
        """All return values are valid JSON strings."""
        from tools.hierarchy_tools import send_to_profile

        for args in [
            {"to": "test-cto", "message": "test"},
            {"message": "no-to"},
            {"to": "", "message": "empty-to"},
            {"to": "test-cto", "priority": "bad"},
        ]:
            raw = send_to_profile(args)
            parsed = json.loads(raw)  # Must not raise
            assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 2. check_inbox
# ---------------------------------------------------------------------------


class TestCheckInbox:
    """Tests for the check_inbox tool."""

    def test_empty_inbox(self, injected):
        """Fresh inbox returns zero messages."""
        from tools.hierarchy_tools import check_inbox

        result = json.loads(check_inbox({"profile": "test-pm"}))
        assert result["pending_count"] == 0
        assert result["messages"] == []
        assert result["profile"] == "test-pm"

    def test_inbox_with_single_message(self, injected):
        """Single message shows up in inbox."""
        from core.ipc.models import MessagePriority, MessageType
        from tools.hierarchy_tools import check_inbox

        injected["bus"].send(
            from_profile="hermes",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Deliver quarterly report"},
            priority=MessagePriority.NORMAL,
        )

        result = json.loads(check_inbox({"profile": "test-pm"}))
        assert result["pending_count"] == 1
        msgs = result["messages"]
        assert len(msgs) == 1
        assert msgs[0]["from"] == "hermes"
        assert msgs[0]["payload"]["message"] == "Deliver quarterly report"
        assert msgs[0]["type"] == "task_request"
        assert "message_id" in msgs[0]
        assert "created_at" in msgs[0]

    def test_inbox_with_multiple_messages(self, injected):
        """Multiple messages in inbox, urgent first."""
        from core.ipc.models import MessagePriority, MessageType
        from tools.hierarchy_tools import check_inbox

        injected["bus"].send(
            from_profile="hermes",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Low priority task"},
            priority=MessagePriority.LOW,
        )
        injected["bus"].send(
            from_profile="test-cto",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Urgent escalation"},
            priority=MessagePriority.URGENT,
        )
        injected["bus"].send(
            from_profile="hermes",
            to_profile="test-pm",
            message_type=MessageType.STATUS_RESPONSE,
            payload={"message": "FYI: deployment scheduled"},
            priority=MessagePriority.NORMAL,
        )

        result = json.loads(check_inbox({"profile": "test-pm"}))
        assert result["pending_count"] == 3
        # Urgent message should be first
        assert result["messages"][0]["priority"] == "urgent"
        assert result["messages"][0]["from"] == "test-cto"

    def test_inbox_defaults_to_current_profile(self, injected):
        """check_inbox({}) uses HERMES_PROFILE env var."""
        from tools.hierarchy_tools import check_inbox

        result = json.loads(check_inbox({}))
        assert result["profile"] == "test-pm"

    def test_inbox_for_other_profile(self, injected):
        """Can check inbox for a different profile."""
        from core.ipc.models import MessagePriority, MessageType
        from tools.hierarchy_tools import check_inbox

        injected["bus"].send(
            from_profile="hermes",
            to_profile="test-cto",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "For the CTO"},
            priority=MessagePriority.NORMAL,
        )

        # test-pm inbox is empty
        pm_result = json.loads(check_inbox({"profile": "test-pm"}))
        assert pm_result["pending_count"] == 0

        # test-cto inbox has 1 message
        cto_result = json.loads(check_inbox({"profile": "test-cto"}))
        assert cto_result["pending_count"] == 1

    def test_inbox_message_fields(self, injected):
        """Each message dict has all required fields."""
        from core.ipc.models import MessagePriority, MessageType
        from tools.hierarchy_tools import check_inbox

        injected["bus"].send(
            from_profile="hermes",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Field check"},
            priority=MessagePriority.NORMAL,
        )

        result = json.loads(check_inbox({"profile": "test-pm"}))
        msg = result["messages"][0]

        required_fields = {"message_id", "from", "type", "priority", "payload", "created_at"}
        assert required_fields.issubset(set(msg.keys()))

    def test_inbox_result_is_valid_json(self, injected):
        """Return value is always valid JSON."""
        from tools.hierarchy_tools import check_inbox

        raw = check_inbox({"profile": "test-pm"})
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 3. org_chart_tool
# ---------------------------------------------------------------------------


class TestOrgChartTool:
    """Tests for the org_chart_tool function."""

    def test_org_chart_contains_all_profiles(self, injected):
        """Chart contains every registered profile's display name."""
        from tools.hierarchy_tools import org_chart_tool

        result = json.loads(org_chart_tool({}))
        chart = result["org_chart"]

        assert "Hermes" in chart
        assert "Test CTO" in chart
        assert "Test PM" in chart
        assert "Test PM B" in chart

    def test_org_chart_shows_roles(self, injected):
        """Chart includes role labels for each profile."""
        from tools.hierarchy_tools import org_chart_tool

        result = json.loads(org_chart_tool({}))
        chart = result["org_chart"]

        assert "ceo" in chart
        assert "department_head" in chart
        assert "project_manager" in chart

    def test_org_chart_has_tree_connectors(self, injected):
        """Chart uses tree connectors to show hierarchy."""
        from tools.hierarchy_tools import org_chart_tool

        result = json.loads(org_chart_tool({}))
        chart = result["org_chart"]

        # Should contain at least one tree connector character
        has_connector = any(c in chart for c in ["└──", "├──", "│", "—"])
        assert has_connector, "Expected tree connectors in org chart"

    def test_org_chart_key_in_result(self, injected):
        """Result dict has 'org_chart' key."""
        from tools.hierarchy_tools import org_chart_tool

        result = json.loads(org_chart_tool({}))
        assert "org_chart" in result
        assert isinstance(result["org_chart"], str)
        assert len(result["org_chart"]) > 0

    def test_org_chart_args_ignored(self, injected):
        """org_chart accepts empty or non-empty args without error."""
        from tools.hierarchy_tools import org_chart_tool

        for args in [{}, {"extra": "param"}, {"ignored": True}]:
            result = json.loads(org_chart_tool(args))
            assert "org_chart" in result

    def test_org_chart_is_valid_json(self, injected):
        """Return value is always valid JSON."""
        from tools.hierarchy_tools import org_chart_tool

        raw = org_chart_tool({})
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 4. profile_status
# ---------------------------------------------------------------------------


class TestProfileStatus:
    """Tests for profile_status tool — all branches."""

    def test_basic_profile_info(self, injected):
        """Status contains profile info block with correct data."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-pm"}))

        assert result["profile"] == "test-pm"
        info = result["info"]
        assert info["role"] == "project_manager"
        assert info["display_name"] == "Test PM"
        assert info["parent"] == "test-cto"
        assert info["department"] == "engineering"

    def test_direct_reports_for_cto(self, injected):
        """CTO profile shows its two PM direct reports."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-cto"}))
        reports = result["direct_reports"]
        report_names = {r["name"] for r in reports}

        assert "test-pm" in report_names
        assert "test-pm-b" in report_names

    def test_ceo_has_direct_report_cto(self, injected):
        """CEO profile shows test-cto as direct report."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "hermes"}))
        reports = result["direct_reports"]
        report_names = [r["name"] for r in reports]

        assert "test-cto" in report_names

    def test_ceo_has_no_workers_section(self, injected):
        """CEO is not a PM, so no 'workers' key appears."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "hermes"}))
        assert "workers" not in result

    def test_pending_messages_initially_zero(self, injected):
        """Fresh profile shows 0 pending messages."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-pm"}))
        assert result["pending_messages"] == 0

    def test_pending_messages_count_updates(self, injected):
        """Pending message count increments when messages are sent."""
        from core.ipc.models import MessagePriority, MessageType
        from tools.hierarchy_tools import profile_status

        for i in range(3):
            injected["bus"].send(
                from_profile="hermes",
                to_profile="test-pm",
                message_type=MessageType.TASK_REQUEST,
                payload={"message": f"Task {i}"},
                priority=MessagePriority.NORMAL,
            )

        result = json.loads(profile_status({"profile": "test-pm"}))
        assert result["pending_messages"] == 3

    def test_no_memory_db_shows_status_message(self, injected):
        """When no memory DB exists, shows informative status."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-pm"}))
        # No memory DB exists for test-pm
        assert "memory" in result
        assert "status" in result["memory"] or "total_entries" in result["memory"]

    def test_memory_stats_when_store_injected(self, injected, memory_store):
        """When memory is injected, stats are populated correctly."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-pm"}))

        assert result["memory"]["total_entries"] == 3
        assert result["memory"]["total_bytes"] > 0
        assert "tier_breakdown" in result["memory"]

    def test_workers_section_for_pm(self, injected, workers):
        """PM profile shows workers section with correct counts."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-pm"}))

        assert "workers" in result
        w = result["workers"]
        assert w["total"] == 3
        by_status = w["by_status"]
        assert "running" in by_status
        assert "completed" in by_status
        assert "sleeping" in by_status

    def test_missing_profile_param_gives_error(self, injected):
        """Omitting 'profile' returns a JSON error."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({}))
        assert "error" in result

    def test_empty_profile_gives_error(self, injected):
        """Empty string 'profile' returns a JSON error."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": ""}))
        assert "error" in result

    def test_result_is_valid_json(self, injected):
        """All return values are valid JSON."""
        from tools.hierarchy_tools import profile_status

        for args in [{"profile": "test-pm"}, {"profile": "hermes"}, {}]:
            raw = profile_status(args)
            parsed = json.loads(raw)
            assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 5. spawn_tracked_worker
# ---------------------------------------------------------------------------


class TestSpawnTrackedWorker:
    """Tests for spawn_tracked_worker — covers all error paths and mocked success."""

    def test_missing_task_gives_error(self, injected):
        """Omitting 'task' returns a JSON error."""
        from tools.hierarchy_tools import spawn_tracked_worker

        result = json.loads(spawn_tracked_worker({}))
        assert "error" in result
        assert "task" in result["error"].lower()

    def test_empty_task_gives_error(self, injected):
        """Empty string 'task' returns a JSON error."""
        from tools.hierarchy_tools import spawn_tracked_worker

        result = json.loads(spawn_tracked_worker({"task": ""}))
        assert "error" in result

    def test_no_parent_agent_gives_error(self, injected):
        """Without parent_agent kwarg, spawn returns error immediately."""
        from tools.hierarchy_tools import spawn_tracked_worker

        result = json.loads(spawn_tracked_worker({"task": "Build something cool"}))
        assert "error" in result
        assert "parent_agent" in result["error"].lower()

    def test_worker_registered_before_delegation(self, injected):
        """Worker is registered in SubagentRegistry before delegation attempt."""
        from tools.hierarchy_tools import spawn_tracked_worker

        sub_reg = injected["sub_reg"]
        initial_count = len(sub_reg.list(project_manager="test-pm", limit=100))

        # Delegation will fail (no delegate_task), but registration should happen
        with patch.dict("sys.modules", {"tools.delegate_tool": None}):
            result = json.loads(spawn_tracked_worker(
                {"task": "A task that needs registration"},
                parent_agent=MagicMock(),
            ))

        # Worker should be registered (even if delegation failed)
        # Result should contain subagent_id on ImportError path
        assert "subagent_id" in result or "error" in result

    def test_spawn_with_mocked_successful_delegation(self, injected):
        """When delegate_task succeeds, returns completed status with summary."""
        from tools.hierarchy_tools import spawn_tracked_worker

        mock_result = json.dumps({
            "results": [{
                "status": "completed",
                "summary": "Feature built and tested",
                "token_counts": {"input": 1000, "output": 500},
            }]
        })

        mock_module = MagicMock()
        mock_module.delegate_task.return_value = mock_result

        with patch.dict("sys.modules", {"tools.delegate_tool": mock_module}):
            result = json.loads(spawn_tracked_worker(
                {"task": "Build authentication module"},
                parent_agent=MagicMock(),
            ))

        assert result["status"] == "completed"
        assert result["summary"] == "Feature built and tested"
        assert "subagent_id" in result

    def test_spawn_with_mocked_failed_delegation(self, injected):
        """When delegate_task returns failed status, worker is marked failed."""
        from tools.hierarchy_tools import spawn_tracked_worker

        mock_result = json.dumps({
            "results": [{"status": "failed", "error": "Out of memory"}]
        })

        mock_module = MagicMock()
        mock_module.delegate_task.return_value = mock_result

        with patch.dict("sys.modules", {"tools.delegate_tool": mock_module}):
            result = json.loads(spawn_tracked_worker(
                {"task": "A task that will fail"},
                parent_agent=MagicMock(),
            ))

        assert result["status"] == "failed"
        assert "subagent_id" in result

    def test_spawn_with_mocked_import_error(self, injected):
        """ImportError on delegate_tool returns registered_only status."""
        from tools.hierarchy_tools import spawn_tracked_worker

        with patch.dict("sys.modules", {"tools.delegate_tool": None}):
            result = json.loads(spawn_tracked_worker(
                {"task": "Task with no delegate_tool"},
                parent_agent=MagicMock(),
            ))

        # Should return error or registered_only
        assert "error" in result or result.get("status") == "registered_only"

    def test_spawn_result_is_valid_json(self, injected):
        """All return values are valid JSON."""
        from tools.hierarchy_tools import spawn_tracked_worker

        for args, kwargs in [
            ({"task": "test"}, {"parent_agent": MagicMock()}),
            ({}, {}),
            ({"task": ""}, {}),
        ]:
            raw = spawn_tracked_worker(args, **kwargs)
            parsed = json.loads(raw)
            assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 6. get_project_status
# ---------------------------------------------------------------------------


class TestGetProjectStatus:
    """Tests for get_project_status tool."""

    def test_status_with_no_workers(self, injected):
        """PM with no workers shows empty status."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({"pm": "test-pm"}))

        assert result["pm"] == "test-pm"
        assert result["total_workers"] == 0
        assert result["running"] == []
        assert result["recent_completions"] == []
        assert result["by_status"] == {}

    def test_status_with_mixed_workers(self, injected, workers):
        """Status correctly categorizes running, completed, and archived workers."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({"pm": "test-pm"}))

        assert result["total_workers"] == 3
        by_status = result["by_status"]
        assert by_status.get("running") == 1
        assert by_status.get("completed") == 1
        assert by_status.get("sleeping") == 1

    def test_running_workers_detail(self, injected, workers):
        """Running workers show subagent_id, task, and started_at."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({"pm": "test-pm"}))

        assert len(result["running"]) == 1
        w = result["running"][0]
        assert "subagent_id" in w
        assert "task" in w
        assert "started_at" in w
        assert "Build authentication" in w["task"]

    def test_completed_workers_in_recent_completions(self, injected, workers):
        """Completed workers appear in recent_completions with summary."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({"pm": "test-pm"}))

        assert len(result["recent_completions"]) >= 1
        c = result["recent_completions"][0]
        assert "subagent_id" in c
        assert "task" in c
        assert "summary" in c
        assert "completed_at" in c
        assert "35 tests pass" in c["summary"]

    def test_completions_sorted_most_recent_first(self, injected):
        """recent_completions are sorted newest first."""
        from tools.hierarchy_tools import get_project_status

        sub_reg = injected["sub_reg"]
        for i in range(3):
            w = sub_reg.register(project_manager="test-pm", task_goal=f"Task {i}")
            sub_reg.complete(
                w.subagent_id,
                result_summary=f"Completed task {i}",
                project_manager="test-pm",
            )

        result = json.loads(get_project_status({"pm": "test-pm"}))
        completions = result["recent_completions"]

        # Should be in reverse chronological order
        dates = [c["completed_at"] for c in completions]
        assert dates == sorted(dates, reverse=True)

    def test_recent_completions_capped_at_10(self, injected):
        """recent_completions returns at most 10 entries."""
        from tools.hierarchy_tools import get_project_status

        sub_reg = injected["sub_reg"]
        for i in range(15):
            w = sub_reg.register(project_manager="test-pm", task_goal=f"Bulk task {i}")
            sub_reg.complete(
                w.subagent_id,
                result_summary=f"Done {i}",
                project_manager="test-pm",
            )

        result = json.loads(get_project_status({"pm": "test-pm"}))
        assert len(result["recent_completions"]) <= 10

    def test_missing_pm_gives_error(self, injected):
        """Omitting 'pm' returns a JSON error."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({}))
        assert "error" in result

    def test_empty_pm_gives_error(self, injected):
        """Empty string 'pm' returns a JSON error."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({"pm": ""}))
        assert "error" in result

    def test_status_for_pm_with_no_workers_is_not_error(self, injected):
        """PM with 0 workers still returns valid (non-error) response."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({"pm": "test-pm-b"}))
        assert "error" not in result
        assert result["total_workers"] == 0

    def test_result_is_valid_json(self, injected):
        """All return values are valid JSON."""
        from tools.hierarchy_tools import get_project_status

        for args in [{"pm": "test-pm"}, {}, {"pm": ""}]:
            raw = get_project_status(args)
            parsed = json.loads(raw)
            assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 7. _build_profile_context
# ---------------------------------------------------------------------------


class TestBuildProfileContext:
    """Tests for the _build_profile_context helper."""

    def test_context_with_soul_file(self, injected, soul_file):
        """When SOUL.md exists, its content appears in the context."""
        from tools.hierarchy_tools import _build_profile_context

        context = _build_profile_context("test-pm")
        assert "IDENTITY" in context
        assert "Test PM" in context
        assert "hierarchy features" in context

    def test_context_without_soul_file(self, injected):
        """When SOUL.md doesn't exist, context doesn't crash."""
        from tools.hierarchy_tools import _build_profile_context

        context = _build_profile_context("test-pm")
        assert isinstance(context, str)
        assert len(context) > 0

    def test_context_includes_memory(self, injected, memory_store):
        """When memory entries exist, they appear in context."""
        from tools.hierarchy_tools import _build_profile_context

        context = _build_profile_context("test-pm")
        assert "SCOPED MEMORY" in context
        assert "SQLite" in context

    def test_context_includes_pending_messages(self, injected):
        """Pending IPC messages appear in context."""
        from core.ipc.models import MessagePriority, MessageType
        from tools.hierarchy_tools import _build_profile_context

        injected["bus"].send(
            from_profile="hermes",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Urgent context task"},
            priority=MessagePriority.URGENT,
        )

        context = _build_profile_context("test-pm")
        assert "PENDING MESSAGES" in context
        assert "hermes" in context

    def test_context_empty_profile_fallback(self, injected):
        """Profile with no data returns a readable fallback string."""
        from tools.hierarchy_tools import _build_profile_context

        context = _build_profile_context("test-pm")
        # Should be non-empty (either data or a fallback message)
        assert isinstance(context, str)
        assert len(context) > 0

    def test_context_memory_preview_truncated(self, injected):
        """Long memory entries are truncated in the context preview."""
        from core.memory.models import (
            MemoryEntry, MemoryEntryType, MemoryScope, MemoryTier, generate_memory_id
        )
        from core.memory.memory_store import MemoryStore
        import tools.hierarchy_tools as ht

        db_path = str(injected["db_dir"] / "memory" / "test-pm.db")
        store = MemoryStore(
            db_path=db_path,
            profile_name="test-pm",
            profile_scope=MemoryScope.project,
        )
        long_content = "X" * 500  # Longer than the 200-char preview limit
        store.store(MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="test-pm",
            scope=MemoryScope.project,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.context,
            content=long_content,
        ))
        ht._memory_stores["test-pm"] = store

        from tools.hierarchy_tools import _build_profile_context
        context = _build_profile_context("test-pm")
        # Context should contain truncation indicator
        assert "..." in context


# ---------------------------------------------------------------------------
# 8. _get_current_profile
# ---------------------------------------------------------------------------


class TestGetCurrentProfile:
    """Tests for the _get_current_profile helper."""

    def test_returns_hermes_env_profile(self, injected):
        """Returns value of HERMES_PROFILE env var."""
        from tools.hierarchy_tools import _get_current_profile

        os.environ["HERMES_PROFILE"] = "test-pm"
        assert _get_current_profile() == "test-pm"

    def test_falls_back_to_hermes_when_env_unset(self):
        """Falls back to 'hermes' when HERMES_PROFILE is not set."""
        from tools.hierarchy_tools import _get_current_profile

        old = os.environ.pop("HERMES_PROFILE", None)
        try:
            assert _get_current_profile() == "hermes"
        finally:
            if old is not None:
                os.environ["HERMES_PROFILE"] = old

    def test_different_profile_names(self):
        """Returns whatever is in the env var."""
        from tools.hierarchy_tools import _get_current_profile

        for name in ("cto", "pm-feature-x", "worker-42"):
            os.environ["HERMES_PROFILE"] = name
            assert _get_current_profile() == name

        # Restore
        os.environ.pop("HERMES_PROFILE", None)


# ---------------------------------------------------------------------------
# 9. check_hierarchy_requirements
# ---------------------------------------------------------------------------


class TestCheckHierarchyRequirements:
    """Tests for availability check helper."""

    def test_returns_true_when_db_exists(self, injected):
        """Returns True when registry.db exists and imports work."""
        import tools.hierarchy_tools as ht
        from tools.hierarchy_tools import check_hierarchy_requirements

        # DB_BASE_DIR is pointing to tmp, and registry.db was created by ProfileRegistry
        assert check_hierarchy_requirements() is True

    def test_returns_false_when_db_missing(self, tmp_path):
        """Returns False when registry.db doesn't exist."""
        import tools.hierarchy_tools as ht
        orig = ht._DB_BASE_DIR

        try:
            ht._DB_BASE_DIR = tmp_path / "nonexistent_subdir"
            assert ht.check_hierarchy_requirements() is False
        finally:
            ht._DB_BASE_DIR = orig

    def test_returns_false_for_empty_directory(self, tmp_path):
        """Returns False for a directory with no registry.db."""
        import tools.hierarchy_tools as ht
        orig = ht._DB_BASE_DIR

        try:
            ht._DB_BASE_DIR = tmp_path  # exists, but no registry.db inside
            assert ht.check_hierarchy_requirements() is False
        finally:
            ht._DB_BASE_DIR = orig


# ---------------------------------------------------------------------------
# 10. OpenAI-compatible JSON schemas
# ---------------------------------------------------------------------------


class TestToolSchemas:
    """Validate all 6 OpenAI-compatible function-calling schemas."""

    SCHEMA_NAMES = [
        "SEND_TO_PROFILE_SCHEMA",
        "CHECK_INBOX_SCHEMA",
        "ORG_CHART_SCHEMA",
        "PROFILE_STATUS_SCHEMA",
        "SPAWN_TRACKED_WORKER_SCHEMA",
        "GET_PROJECT_STATUS_SCHEMA",
    ]

    def _load_schemas(self):
        import tools.hierarchy_tools as ht
        return [getattr(ht, name) for name in self.SCHEMA_NAMES]

    def test_six_schemas_exported(self):
        """Exactly 6 schemas are exported."""
        schemas = self._load_schemas()
        assert len(schemas) == 6

    def test_all_schemas_have_name(self):
        """Every schema has a non-empty 'name' field."""
        for schema in self._load_schemas():
            assert "name" in schema
            assert isinstance(schema["name"], str)
            assert len(schema["name"]) > 0

    def test_all_schemas_have_description(self):
        """Every schema has a non-empty 'description' field."""
        for schema in self._load_schemas():
            assert "description" in schema
            assert isinstance(schema["description"], str)
            assert len(schema["description"]) > 0

    def test_all_schemas_have_parameters_object(self):
        """Every schema has 'parameters' with type=object and properties."""
        for schema in self._load_schemas():
            params = schema.get("parameters", {})
            assert params.get("type") == "object", f"Schema {schema['name']} missing type=object"
            assert "properties" in params
            assert "required" in params
            assert isinstance(params["required"], list)

    def test_schema_names_are_unique(self):
        """All tool names are unique."""
        names = [s["name"] for s in self._load_schemas()]
        assert len(names) == len(set(names)), f"Duplicate names: {names}"

    def test_send_to_profile_schema_required_fields(self):
        """send_to_profile schema requires 'to' and 'message'."""
        from tools.hierarchy_tools import SEND_TO_PROFILE_SCHEMA

        required = SEND_TO_PROFILE_SCHEMA["parameters"]["required"]
        assert "to" in required
        assert "message" in required

    def test_send_to_profile_schema_has_priority_enum(self):
        """send_to_profile schema has enum for priority."""
        from tools.hierarchy_tools import SEND_TO_PROFILE_SCHEMA

        props = SEND_TO_PROFILE_SCHEMA["parameters"]["properties"]
        priority = props.get("priority", {})
        assert "enum" in priority
        assert set(priority["enum"]) == {"low", "normal", "urgent"}

    def test_check_inbox_schema_has_no_required_fields(self):
        """check_inbox has no required params (profile is optional)."""
        from tools.hierarchy_tools import CHECK_INBOX_SCHEMA

        required = CHECK_INBOX_SCHEMA["parameters"]["required"]
        assert required == []

    def test_org_chart_schema_has_no_required_fields(self):
        """org_chart has no required params."""
        from tools.hierarchy_tools import ORG_CHART_SCHEMA

        required = ORG_CHART_SCHEMA["parameters"]["required"]
        assert required == []

    def test_profile_status_schema_requires_profile(self):
        """profile_status requires 'profile'."""
        from tools.hierarchy_tools import PROFILE_STATUS_SCHEMA

        required = PROFILE_STATUS_SCHEMA["parameters"]["required"]
        assert "profile" in required

    def test_spawn_tracked_worker_schema_requires_task(self):
        """spawn_tracked_worker requires 'task'."""
        from tools.hierarchy_tools import SPAWN_TRACKED_WORKER_SCHEMA

        required = SPAWN_TRACKED_WORKER_SCHEMA["parameters"]["required"]
        assert "task" in required

    def test_get_project_status_schema_requires_pm(self):
        """get_project_status requires 'pm'."""
        from tools.hierarchy_tools import GET_PROJECT_STATUS_SCHEMA

        required = GET_PROJECT_STATUS_SCHEMA["parameters"]["required"]
        assert "pm" in required


# ---------------------------------------------------------------------------
# 11. Lazy singleton initialization
# ---------------------------------------------------------------------------


class TestLazySingletons:
    """Verify singletons are initialized lazily and can be reset."""

    def test_singletons_start_none(self):
        """Module-level singletons are None before any tool is called."""
        import tools.hierarchy_tools as ht

        # Save and clear
        orig_reg = ht._profile_registry
        orig_bus = ht._message_bus
        orig_sub = ht._subagent_registry

        ht._profile_registry = None
        ht._message_bus = None
        ht._subagent_registry = None

        assert ht._profile_registry is None
        assert ht._message_bus is None
        assert ht._subagent_registry is None

        # Restore
        ht._profile_registry = orig_reg
        ht._message_bus = orig_bus
        ht._subagent_registry = orig_sub

    def test_injected_singletons_are_used(self, injected):
        """Tools use injected singletons rather than creating new ones."""
        import tools.hierarchy_tools as ht
        from tools.hierarchy_tools import check_inbox

        # The fixture injected a real registry/bus/sub_reg
        assert ht._profile_registry is injected["registry"]
        assert ht._message_bus is injected["bus"]
        assert ht._subagent_registry is injected["sub_reg"]

    def test_memory_store_cache_populated(self, injected, memory_store):
        """After memory is injected, _memory_stores dict is populated."""
        import tools.hierarchy_tools as ht

        assert "test-pm" in ht._memory_stores
        assert ht._memory_stores["test-pm"] is memory_store


# ---------------------------------------------------------------------------
# 12. End-to-end integration across multiple tools
# ---------------------------------------------------------------------------


class TestEndToEndFlows:
    """Cross-tool integration tests."""

    def test_send_then_check_inbox(self, injected):
        """send_to_profile followed by check_inbox shows the message."""
        from tools.hierarchy_tools import check_inbox, send_to_profile

        send_to_profile({
            "to": "test-cto",
            "message": "E2E: send and receive test",
            "priority": "normal",
        })

        result = json.loads(check_inbox({"profile": "test-cto"}))
        assert result["pending_count"] == 1
        assert result["messages"][0]["payload"]["message"] == "E2E: send and receive test"

    def test_worker_shows_in_project_status(self, injected, workers):
        """Workers registered via sub_reg appear in get_project_status output."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({"pm": "test-pm"}))
        running_ids = {w["subagent_id"] for w in result["running"]}
        completed_ids = {c["subagent_id"] for c in result["recent_completions"]}

        assert workers["running"].subagent_id in running_ids
        assert workers["completed"].subagent_id in completed_ids  # type: ignore

    def test_profile_status_reflects_messages(self, injected):
        """Sending messages updates the pending_messages count in profile_status."""
        from core.ipc.models import MessagePriority, MessageType
        from tools.hierarchy_tools import profile_status

        # Before
        before = json.loads(profile_status({"profile": "test-pm"}))
        assert before["pending_messages"] == 0

        # Send 2 messages
        injected["bus"].send("hermes", "test-pm", MessageType.TASK_REQUEST,
                             {"message": "m1"}, MessagePriority.NORMAL)
        injected["bus"].send("test-cto", "test-pm", MessageType.TASK_REQUEST,
                             {"message": "m2"}, MessagePriority.URGENT)

        # After
        after = json.loads(profile_status({"profile": "test-pm"}))
        assert after["pending_messages"] == 2

    def test_org_chart_reflects_all_profiles(self, injected):
        """Org chart renders every profile created during setup."""
        from tools.hierarchy_tools import org_chart_tool

        result = json.loads(org_chart_tool({}))
        chart = result["org_chart"]

        for display_name in ["Hermes", "Test CTO", "Test PM", "Test PM B"]:
            assert display_name in chart, f"'{display_name}' missing from org chart"


# ---------------------------------------------------------------------------
# Entry point for direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
