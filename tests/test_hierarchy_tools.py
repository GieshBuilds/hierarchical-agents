#!/usr/bin/env python3
"""
Tests for hierarchy_tools.py

Tests tool handlers directly using temporary databases.
Injects dependencies into the tool module's singletons for isolation.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the hierarchy project is on sys.path
PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Fixtures — set up temporary databases and inject into tool singletons
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_dir(tmp_path):
    """Create a temporary directory structure for hierarchy databases."""
    (tmp_path / "memory").mkdir()
    (tmp_path / "workers").mkdir()
    return tmp_path


@pytest.fixture
def setup_all(tmp_db_dir):
    """Set up all hierarchy components and inject into tool module singletons.

    This fixture:
    1. Creates ProfileRegistry, MessageBus, SubagentRegistry with temp DBs
    2. Creates test profiles (hermes CEO, test-cto dept head, test-pm PM)
    3. Injects all singletons into tools.hierarchy_tools module
    4. Sets HERMES_PROFILE env var to test-pm
    """
    import tools.hierarchy_tools as ht
    from core.registry.profile_registry import ProfileRegistry
    from core.registry.models import Role
    from core.ipc.message_bus import MessageBus
    from core.workers.subagent_registry import SubagentRegistry

    # Build registry
    reg_path = str(tmp_db_dir / "registry.db")
    registry = ProfileRegistry(reg_path)

    # Add test profiles (hermes CEO is auto-created)
    registry.create_profile(
        name="test-cto",
        display_name="Test CTO",
        role=Role.DEPARTMENT_HEAD.value,
        parent="hermes",
        department="engineering",
    )
    registry.create_profile(
        name="test-pm",
        display_name="Test PM",
        role=Role.PROJECT_MANAGER.value,
        parent="test-cto",
        department="engineering",
    )

    # Build message bus — use adapter to bridge .get() vs .get_profile()
    class _RegistryAdapter:
        def __init__(self, reg):
            self._reg = reg
        def get(self, name):
            return self._reg.get_profile(name)

    ipc_path = str(tmp_db_dir / "ipc.db")
    bus = MessageBus(db_path=ipc_path, profile_registry=_RegistryAdapter(registry))

    # Build subagent registry
    workers_path = str(tmp_db_dir / "workers")
    sub_reg = SubagentRegistry(base_path=workers_path, profile_registry=registry)

    # Inject into tool module singletons
    ht._profile_registry = registry
    ht._message_bus = bus
    ht._subagent_registry = sub_reg
    ht._memory_stores = {}
    ht._DB_BASE_DIR = tmp_db_dir

    # Set current profile
    old_profile = os.environ.get("HERMES_PROFILE")
    os.environ["HERMES_PROFILE"] = "test-pm"

    yield {
        "registry": registry,
        "bus": bus,
        "sub_reg": sub_reg,
        "db_dir": tmp_db_dir,
    }

    # Clean up
    ht._profile_registry = None
    ht._message_bus = None
    ht._subagent_registry = None
    ht._memory_stores = {}

    if old_profile is not None:
        os.environ["HERMES_PROFILE"] = old_profile
    else:
        os.environ.pop("HERMES_PROFILE", None)


@pytest.fixture
def setup_memory(setup_all):
    """Add memory entries to the test PM's memory store."""
    from core.memory.memory_store import MemoryStore
    from core.memory.models import (
        MemoryScope, MemoryEntry, MemoryEntryType, MemoryTier,
        generate_memory_id,
    )
    import tools.hierarchy_tools as ht

    db_path = str(setup_all["db_dir"] / "memory" / "test-pm.db")
    store = MemoryStore(
        db_path=db_path,
        profile_name="test-pm",
        profile_scope=MemoryScope.project,
    )

    # Add test entries with all required fields
    store.store(MemoryEntry(
        entry_id=generate_memory_id(),
        profile_name="test-pm",
        scope=MemoryScope.project,
        tier=MemoryTier.hot,
        entry_type=MemoryEntryType.decision,
        content="Decision: Use SQLite for all hierarchy databases",
    ))
    store.store(MemoryEntry(
        entry_id=generate_memory_id(),
        profile_name="test-pm",
        scope=MemoryScope.project,
        tier=MemoryTier.hot,
        entry_type=MemoryEntryType.learning,
        content="Learning: Tool registration requires name, schema, handler, check_fn",
    ))

    # Inject into tool module
    ht._memory_stores["test-pm"] = store
    return store


@pytest.fixture
def setup_workers(setup_all):
    """Register test workers in the SubagentRegistry."""
    sub_reg = setup_all["sub_reg"]

    w1 = sub_reg.register(project_manager="test-pm", task_goal="Implement feature X")
    w2 = sub_reg.register(project_manager="test-pm", task_goal="Write tests for feature X")
    sub_reg.complete(w2.subagent_id, result_summary="All 15 tests pass", project_manager="test-pm")

    return sub_reg, w1, w2


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSendToProfile:
    """Tests for send_to_profile tool."""

    def test_send_message_async(self, setup_all):
        """Test sending a message without waiting for response."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "test-cto",
            "message": "Status update: Phase 5 complete",
            "priority": "normal",
            "wait_for_response": False,
        }))

        assert "error" not in result
        assert result["status"] == "sent"
        assert result["to"] == "test-cto"
        assert result["from"] == "test-pm"
        assert "message_id" in result

    def test_send_message_urgent(self, setup_all):
        """Test sending an urgent message."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "hermes",
            "message": "Critical issue needs CEO attention",
            "priority": "urgent",
        }))

        assert "error" not in result
        assert result["priority"] == "urgent"

    def test_send_missing_to(self, setup_all):
        """Test error when 'to' is missing."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "message": "Hello",
        }))
        assert "error" in result

    def test_send_missing_message(self, setup_all):
        """Test error when 'message' is missing."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "test-cto",
        }))
        assert "error" in result

    def test_send_invalid_priority(self, setup_all):
        """Test error with invalid priority."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "test-cto",
            "message": "Test",
            "priority": "super-urgent",
        }))
        assert "error" in result

    def test_wait_for_response_no_agent(self, setup_all):
        """Test wait_for_response=True without parent_agent gives warning."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "test-cto",
            "message": "Need input on architecture",
            "wait_for_response": True,
        }))

        # Message should still be sent, but with a warning about no parent_agent
        assert result["status"] == "sent"
        assert "warning" in result

    def test_send_creates_ipc_message(self, setup_all):
        """Verify the message actually lands in the IPC bus."""
        from tools.hierarchy_tools import send_to_profile

        result = json.loads(send_to_profile({
            "to": "test-cto",
            "message": "Check IPC delivery",
        }))

        bus = setup_all["bus"]
        pending = bus.poll("test-cto", limit=10)
        assert len(pending) == 1
        assert pending[0].payload.get("message") == "Check IPC delivery"


class TestCheckInbox:
    """Tests for check_inbox tool."""

    def test_empty_inbox(self, setup_all):
        """Test checking an inbox with no messages."""
        from tools.hierarchy_tools import check_inbox

        result = json.loads(check_inbox({"profile": "test-pm"}))
        assert result["pending_count"] == 0
        assert result["messages"] == []

    def test_inbox_with_messages(self, setup_all):
        """Test checking inbox after receiving messages."""
        from core.ipc.models import MessageType, MessagePriority

        bus = setup_all["bus"]
        bus.send(
            from_profile="hermes",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Implement hierarchy tools"},
            priority=MessagePriority.NORMAL,
        )
        bus.send(
            from_profile="test-cto",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Review PR #42"},
            priority=MessagePriority.URGENT,
        )

        from tools.hierarchy_tools import check_inbox
        result = json.loads(check_inbox({"profile": "test-pm"}))

        assert result["pending_count"] == 2
        # Urgent should come first
        assert result["messages"][0]["priority"] == "urgent"
        assert result["messages"][0]["from"] == "test-cto"

    def test_inbox_default_profile(self, setup_all):
        """Test that inbox defaults to current profile."""
        from tools.hierarchy_tools import check_inbox

        result = json.loads(check_inbox({}))
        assert result["profile"] == "test-pm"  # From HERMES_PROFILE env


class TestOrgChart:
    """Tests for org_chart tool."""

    def test_org_chart_display(self, setup_all):
        """Test org chart renders correctly."""
        from tools.hierarchy_tools import org_chart_tool

        result = json.loads(org_chart_tool({}))
        assert "org_chart" in result
        chart = result["org_chart"]

        # Should contain our test profiles
        assert "Hermes" in chart
        assert "Test CTO" in chart
        assert "Test PM" in chart

        # Should show role labels
        assert "ceo" in chart
        assert "department_head" in chart
        assert "project_manager" in chart

    def test_org_chart_tree_structure(self, setup_all):
        """Test org chart has proper tree connectors."""
        from tools.hierarchy_tools import org_chart_tool

        result = json.loads(org_chart_tool({}))
        chart = result["org_chart"]

        # Should have tree connectors
        assert "└──" in chart or "├──" in chart


class TestProfileStatus:
    """Tests for profile_status tool."""

    def test_profile_status_basic(self, setup_all):
        """Test basic status for a profile."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-pm"}))

        assert result["profile"] == "test-pm"
        assert result["info"]["role"] == "project_manager"
        assert result["info"]["display_name"] == "Test PM"
        assert result["pending_messages"] == 0

    def test_profile_status_with_memory(self, setup_all, setup_memory):
        """Test status includes memory stats."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-pm"}))

        assert result["memory"]["total_entries"] == 2
        assert result["memory"]["total_bytes"] > 0

    def test_profile_status_with_workers(self, setup_all, setup_memory, setup_workers):
        """Test status includes worker info for PM profiles."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-pm"}))

        assert "workers" in result
        assert result["workers"]["total"] == 2
        assert "running" in result["workers"]["by_status"]
        assert "completed" in result["workers"]["by_status"]

    def test_profile_status_missing_profile(self, setup_all):
        """Test error when profile parameter is missing."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({}))
        assert "error" in result

    def test_profile_status_ceo(self, setup_all):
        """Test status for CEO profile (no workers section)."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "hermes"}))

        assert result["info"]["role"] == "ceo"
        assert len(result["direct_reports"]) == 1  # test-cto
        assert "workers" not in result  # CEO is not a PM

    def test_profile_status_direct_reports(self, setup_all):
        """Test that direct reports are listed correctly."""
        from tools.hierarchy_tools import profile_status

        result = json.loads(profile_status({"profile": "test-cto"}))

        assert len(result["direct_reports"]) == 1
        assert result["direct_reports"][0]["name"] == "test-pm"


class TestSpawnTrackedWorker:
    """Tests for spawn_tracked_worker tool."""

    def test_spawn_without_parent_agent(self, setup_all):
        """Test that spawning without parent_agent gives error."""
        from tools.hierarchy_tools import spawn_tracked_worker

        result = json.loads(spawn_tracked_worker({
            "task": "Build the thing",
        }))
        assert "error" in result

    def test_spawn_missing_task(self, setup_all):
        """Test error when task is missing."""
        from tools.hierarchy_tools import spawn_tracked_worker

        result = json.loads(spawn_tracked_worker({}))
        assert "error" in result


class TestGetProjectStatus:
    """Tests for get_project_status tool."""

    def test_project_status(self, setup_all, setup_workers):
        """Test getting project status for a PM."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({"pm": "test-pm"}))

        assert result["pm"] == "test-pm"
        assert result["total_workers"] == 2
        assert "running" in result["by_status"]
        assert "completed" in result["by_status"]
        assert len(result["running"]) == 1
        assert len(result["recent_completions"]) == 1
        assert "All 15 tests pass" in result["recent_completions"][0]["summary"]

    def test_project_status_missing_pm(self, setup_all):
        """Test error when PM parameter is missing."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({}))
        assert "error" in result

    def test_project_status_empty(self, setup_all):
        """Test status for a PM with no workers."""
        from tools.hierarchy_tools import get_project_status

        result = json.loads(get_project_status({"pm": "test-pm"}))
        assert result["total_workers"] == 0
        assert result["running"] == []
        assert result["recent_completions"] == []


class TestContextBuilding:
    """Tests for _build_profile_context helper."""

    def test_build_context_with_memory(self, setup_all, setup_memory):
        """Test context includes memory entries."""
        from tools.hierarchy_tools import _build_profile_context

        context = _build_profile_context("test-pm")
        assert "SCOPED MEMORY" in context
        assert "SQLite" in context  # from our test entry

    def test_build_context_with_pending_messages(self, setup_all):
        """Test context includes pending IPC messages."""
        from core.ipc.models import MessageType, MessagePriority

        bus = setup_all["bus"]
        bus.send(
            from_profile="hermes",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Priority task from CEO"},
            priority=MessagePriority.URGENT,
        )

        from tools.hierarchy_tools import _build_profile_context
        context = _build_profile_context("test-pm")
        assert "PENDING MESSAGES" in context
        assert "hermes" in context

    def test_build_context_no_soul(self, setup_all):
        """Test context works when SOUL.md doesn't exist."""
        from tools.hierarchy_tools import _build_profile_context

        context = _build_profile_context("test-pm")
        assert isinstance(context, str)
        # Should not contain SOUL section
        assert "IDENTITY" not in context or "No additional context" in context


class TestAvailabilityCheck:
    """Tests for check_hierarchy_requirements."""

    def test_requirements_met(self, setup_all):
        """Test availability when hierarchy is set up."""
        from tools.hierarchy_tools import check_hierarchy_requirements
        assert check_hierarchy_requirements() is True

    def test_requirements_no_db(self, tmp_path):
        """Test availability when no registry DB exists."""
        import tools.hierarchy_tools as ht
        original = ht._DB_BASE_DIR
        ht._DB_BASE_DIR = tmp_path / "nonexistent"

        try:
            assert ht.check_hierarchy_requirements() is False
        finally:
            ht._DB_BASE_DIR = original


class TestSchemaStructure:
    """Tests that tool schemas are well-formed."""

    def test_all_schemas_have_required_fields(self):
        """Verify all schemas have name, description, parameters."""
        from tools.hierarchy_tools import (
            SEND_TO_PROFILE_SCHEMA,
            CHECK_INBOX_SCHEMA,
            ORG_CHART_SCHEMA,
            PROFILE_STATUS_SCHEMA,
            SPAWN_TRACKED_WORKER_SCHEMA,
            GET_PROJECT_STATUS_SCHEMA,
        )

        schemas = [
            SEND_TO_PROFILE_SCHEMA,
            CHECK_INBOX_SCHEMA,
            ORG_CHART_SCHEMA,
            PROFILE_STATUS_SCHEMA,
            SPAWN_TRACKED_WORKER_SCHEMA,
            GET_PROJECT_STATUS_SCHEMA,
        ]

        for schema in schemas:
            assert "name" in schema, f"Schema missing 'name'"
            assert "description" in schema, f"Schema {schema.get('name')} missing 'description'"
            assert "parameters" in schema, f"Schema {schema.get('name')} missing 'parameters'"
            assert schema["parameters"]["type"] == "object"
            assert "properties" in schema["parameters"]
            assert "required" in schema["parameters"]

    def test_schema_names_are_unique(self):
        """Verify all tool names are unique."""
        from tools.hierarchy_tools import (
            SEND_TO_PROFILE_SCHEMA,
            CHECK_INBOX_SCHEMA,
            ORG_CHART_SCHEMA,
            PROFILE_STATUS_SCHEMA,
            SPAWN_TRACKED_WORKER_SCHEMA,
            GET_PROJECT_STATUS_SCHEMA,
        )

        names = [
            SEND_TO_PROFILE_SCHEMA["name"],
            CHECK_INBOX_SCHEMA["name"],
            ORG_CHART_SCHEMA["name"],
            PROFILE_STATUS_SCHEMA["name"],
            SPAWN_TRACKED_WORKER_SCHEMA["name"],
            GET_PROJECT_STATUS_SCHEMA["name"],
        ]
        assert len(names) == len(set(names)), "Duplicate tool names found"

    def test_all_schemas_count(self):
        """Verify we have exactly 6 tool schemas."""
        from tools.hierarchy_tools import (
            SEND_TO_PROFILE_SCHEMA,
            CHECK_INBOX_SCHEMA,
            ORG_CHART_SCHEMA,
            PROFILE_STATUS_SCHEMA,
            SPAWN_TRACKED_WORKER_SCHEMA,
            GET_PROJECT_STATUS_SCHEMA,
        )

        schemas = [
            SEND_TO_PROFILE_SCHEMA,
            CHECK_INBOX_SCHEMA,
            ORG_CHART_SCHEMA,
            PROFILE_STATUS_SCHEMA,
            SPAWN_TRACKED_WORKER_SCHEMA,
            GET_PROJECT_STATUS_SCHEMA,
        ]
        assert len(schemas) == 6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
