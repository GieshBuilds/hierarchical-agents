#!/usr/bin/env python3
"""
Comprehensive test suite for integrations/claude-code/

Covers all five modules:
  - config.py          — ClaudeCodeConfig
  - profile_adapter.py — ClaudeCodeProfileAdapter
  - message_bridge.py  — ClaudeCodeMessageBridge
  - memory_adapter.py  — ClaudeCodeMemoryAdapter
  - adapter.py         — ClaudeCodeAdapter (facade)

Strategy
--------
- All file I/O uses pytest tmp_path — no real ~/.claude/ is touched.
- IPC bus and registry are real SQLite instances in temp directories.
- Memory entries are constructed with real MemoryEntry objects.
- All tests are self-contained; no external services needed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

import pytest

# Ensure the project root is on sys.path
PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dirs(tmp_path):
    """Create the directory layout used by Claude Code integration."""
    dirs = {
        "projects": tmp_path / "projects",
        "tasks": tmp_path / "tasks",
        "db": tmp_path / "db",
        "profiles": tmp_path / "profiles",
        "memory": tmp_path / "db" / "memory",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


@pytest.fixture
def config(tmp_dirs):
    """ClaudeCodeConfig pointed at temp directories."""
    from integrations.claude_code.config import ClaudeCodeConfig

    return ClaudeCodeConfig(
        projects_dir=tmp_dirs["projects"],
        tasks_dir=tmp_dirs["tasks"],
        db_base_dir=tmp_dirs["db"],
        profiles_dir=tmp_dirs["profiles"],
        include_memory_in_claude_md=True,
        max_memory_entries=10,
        include_pending_messages=True,
        max_pending_messages=5,
    )


@pytest.fixture
def registry(tmp_dirs):
    """ProfileRegistry backed by a temp SQLite database."""
    from core.registry.profile_registry import ProfileRegistry
    from core.registry.models import Role

    db_path = str(tmp_dirs["db"] / "registry.db")
    reg = ProfileRegistry(db_path)

    # Build a 3-level test hierarchy
    reg.create_profile(
        name="test-cto",
        display_name="Test CTO",
        role=Role.DEPARTMENT_HEAD.value,
        parent="hermes",
        department="engineering",
    )
    reg.create_profile(
        name="test-pm",
        display_name="Test PM",
        role=Role.PROJECT_MANAGER.value,
        parent="test-cto",
        department="engineering",
    )
    reg.create_profile(
        name="test-pm-b",
        display_name="Test PM B",
        role=Role.PROJECT_MANAGER.value,
        parent="test-cto",
        department="engineering",
    )
    return reg


@pytest.fixture
def bus(tmp_dirs, registry):
    """MessageBus with RegistryAdapter for the temp registry."""
    from core.ipc.message_bus import MessageBus

    class _RegistryAdapter:
        def __init__(self, reg):
            self._reg = reg

        def get(self, name):
            return self._reg.get_profile(name)

    ipc_path = str(tmp_dirs["db"] / "ipc.db")
    return MessageBus(
        db_path=ipc_path,
        profile_registry=_RegistryAdapter(registry),
    )


@pytest.fixture
def memory_entries(tmp_dirs):
    """A list of real MemoryEntry objects for test-pm."""
    from core.memory.memory_store import MemoryStore
    from core.memory.models import (
        MemoryEntry,
        MemoryEntryType,
        MemoryScope,
        MemoryTier,
        generate_memory_id,
    )

    db_path = str(tmp_dirs["memory"] / "test-pm.db")
    store = MemoryStore(
        db_path=db_path,
        profile_name="test-pm",
        profile_scope=MemoryScope.project,
    )

    entries_data = [
        ("Decision: Use SQLite for hierarchy databases", MemoryEntryType.decision, MemoryTier.hot),
        ("Learning: Tool registration requires name, schema, handler", MemoryEntryType.learning, MemoryTier.hot),
        ("Context: Phase 6 integration complete", MemoryEntryType.context, MemoryTier.warm),
    ]

    for content, entry_type, tier in entries_data:
        store.store(
            MemoryEntry(
                entry_id=generate_memory_id(),
                profile_name="test-pm",
                scope=MemoryScope.project,
                tier=tier,
                entry_type=entry_type,
                content=content,
            )
        )

    return store.list_entries(limit=20)


@pytest.fixture
def soul_file(tmp_dirs):
    """Create a SOUL.md for test-pm."""
    pm_dir = tmp_dirs["profiles"] / "test-pm"
    pm_dir.mkdir(parents=True, exist_ok=True)
    soul = pm_dir / "SOUL.md"
    soul.write_text(
        "# Test PM\nYou are the Test PM responsible for hierarchy features.",
        encoding="utf-8",
    )
    return soul


# ---------------------------------------------------------------------------
# 1. ClaudeCodeConfig
# ---------------------------------------------------------------------------


class TestClaudeCodeConfig:
    """Tests for config creation, defaults, and env var loading."""

    def test_default_config_has_expected_paths(self):
        """Default config sets paths relative to ~."""
        from integrations.claude_code.config import ClaudeCodeConfig

        cfg = ClaudeCodeConfig()
        assert ".claude" in str(cfg.projects_dir)
        assert ".claude" in str(cfg.tasks_dir)
        assert ".hermes" in str(cfg.db_base_dir)
        assert ".hermes" in str(cfg.profiles_dir)

    def test_default_memory_flags_are_true(self):
        """By default memory and messages are embedded."""
        from integrations.claude_code.config import ClaudeCodeConfig

        cfg = ClaudeCodeConfig()
        assert cfg.include_memory_in_claude_md is True
        assert cfg.include_pending_messages is True

    def test_default_limits(self):
        """Default limits are 10 memory entries and 5 messages."""
        from integrations.claude_code.config import ClaudeCodeConfig

        cfg = ClaudeCodeConfig()
        assert cfg.max_memory_entries == 10
        assert cfg.max_pending_messages == 5

    def test_from_dict_path_conversion(self, tmp_dirs):
        """from_dict converts string paths to Path objects."""
        from integrations.claude_code.config import ClaudeCodeConfig

        cfg = ClaudeCodeConfig.from_dict({
            "projects_dir": str(tmp_dirs["projects"]),
            "tasks_dir": str(tmp_dirs["tasks"]),
            "db_base_dir": str(tmp_dirs["db"]),
            "profiles_dir": str(tmp_dirs["profiles"]),
        })

        assert cfg.projects_dir == tmp_dirs["projects"]
        assert isinstance(cfg.projects_dir, Path)

    def test_from_dict_bool_fields(self):
        """from_dict correctly handles boolean fields."""
        from integrations.claude_code.config import ClaudeCodeConfig

        cfg = ClaudeCodeConfig.from_dict({
            "include_memory_in_claude_md": False,
            "include_pending_messages": False,
        })
        assert cfg.include_memory_in_claude_md is False
        assert cfg.include_pending_messages is False

    def test_from_dict_int_fields(self):
        """from_dict correctly handles integer fields."""
        from integrations.claude_code.config import ClaudeCodeConfig

        cfg = ClaudeCodeConfig.from_dict({
            "max_memory_entries": 25,
            "max_pending_messages": 3,
        })
        assert cfg.max_memory_entries == 25
        assert cfg.max_pending_messages == 3

    def test_from_env_reads_environment(self, tmp_dirs, monkeypatch):
        """from_env reads CLAUDE_CODE_* and HERMES_* variables."""
        from integrations.claude_code.config import ClaudeCodeConfig

        monkeypatch.setenv("CLAUDE_CODE_PROJECTS_DIR", str(tmp_dirs["projects"]))
        monkeypatch.setenv("CLAUDE_CODE_TASKS_DIR", str(tmp_dirs["tasks"]))
        monkeypatch.setenv("HERMES_DB_BASE_DIR", str(tmp_dirs["db"]))
        monkeypatch.setenv("HERMES_PROFILES_DIR", str(tmp_dirs["profiles"]))
        monkeypatch.setenv("CLAUDE_CODE_INCLUDE_MEMORY", "true")
        monkeypatch.setenv("CLAUDE_CODE_MAX_MEMORY_ENTRIES", "15")
        monkeypatch.setenv("CLAUDE_CODE_INCLUDE_MESSAGES", "false")
        monkeypatch.setenv("CLAUDE_CODE_MAX_MESSAGES", "3")

        cfg = ClaudeCodeConfig.from_env()

        assert cfg.projects_dir == tmp_dirs["projects"]
        assert cfg.max_memory_entries == 15
        assert cfg.include_pending_messages is False
        assert cfg.max_pending_messages == 3

    def test_from_env_false_values(self, monkeypatch):
        """from_env treats '0', 'false', 'no' as False."""
        from integrations.claude_code.config import ClaudeCodeConfig

        for false_val in ("0", "false", "no"):
            monkeypatch.setenv("CLAUDE_CODE_INCLUDE_MEMORY", false_val)
            cfg = ClaudeCodeConfig.from_env()
            assert cfg.include_memory_in_claude_md is False

    def test_config_custom_values_preserved(self, tmp_dirs):
        """Custom config values round-trip correctly."""
        from integrations.claude_code.config import ClaudeCodeConfig

        cfg = ClaudeCodeConfig(
            projects_dir=tmp_dirs["projects"],
            max_memory_entries=42,
            include_memory_in_claude_md=False,
        )
        assert cfg.max_memory_entries == 42
        assert cfg.include_memory_in_claude_md is False


# ---------------------------------------------------------------------------
# 2. ClaudeCodeProfileAdapter
# ---------------------------------------------------------------------------


class TestClaudeCodeProfileAdapter:
    """Tests for CLAUDE.md generation from ProfileRegistry data."""

    def test_generate_claude_md_returns_string(self, registry, config):
        """generate_claude_md returns a non-empty string."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm")

        assert isinstance(content, str)
        assert len(content) > 0

    def test_claude_md_contains_profile_name(self, registry, config):
        """Generated CLAUDE.md mentions the profile name."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm")

        assert "test-pm" in content

    def test_claude_md_contains_display_name(self, registry, config):
        """Generated CLAUDE.md contains the profile display name."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm")

        assert "Test PM" in content

    def test_claude_md_contains_role(self, registry, config):
        """Generated CLAUDE.md mentions the profile's role."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm")

        assert "Project Manager" in content

    def test_claude_md_contains_parent_info(self, registry, config):
        """Generated CLAUDE.md shows who the profile reports to."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm")

        assert "test-cto" in content

    def test_claude_md_contains_tools_section(self, registry, config):
        """Generated CLAUDE.md includes the hierarchy tools quick reference."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm")

        assert "send_to_profile" in content
        assert "check_inbox" in content
        assert "org_chart" in content

    def test_claude_md_contains_direct_reports_for_cto(self, registry, config):
        """CTO's CLAUDE.md lists both PM direct reports."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-cto")

        assert "test-pm" in content
        assert "test-pm-b" in content

    def test_claude_md_includes_soul_md_when_present(self, registry, config, soul_file):
        """When SOUL.md exists, its content is embedded in CLAUDE.md."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm")

        assert "hierarchy features" in content
        assert "SOUL.md" in content or "Identity" in content

    def test_claude_md_with_memory_entries(self, registry, config, memory_entries):
        """Memory entries appear in the CLAUDE.md when provided."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm", memory_entries=memory_entries)

        assert "SQLite" in content
        assert "hierarchy databases" in content

    def test_claude_md_without_memory_entries_no_memory_section(self, registry, config):
        """When memory is disabled, no memory section appears."""
        from integrations.claude_code.config import ClaudeCodeConfig
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        cfg = ClaudeCodeConfig(
            projects_dir=config.projects_dir,
            tasks_dir=config.tasks_dir,
            profiles_dir=config.profiles_dir,
            include_memory_in_claude_md=False,
        )
        adapter = ClaudeCodeProfileAdapter(registry, cfg)
        content = adapter.generate_claude_md("test-pm", memory_entries=memory_entries)

        assert "Scoped Memory" not in content

    def test_write_claude_md_creates_file(self, registry, config):
        """write_claude_md creates the CLAUDE.md file on disk."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        path = adapter.write_claude_md("test-pm")

        assert path.exists()
        assert path.name == "CLAUDE.md"
        content = path.read_text(encoding="utf-8")
        assert len(content) > 0

    def test_write_claude_md_creates_project_directory(self, registry, config):
        """write_claude_md creates the project directory if it doesn't exist."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        project_dir = adapter.get_project_dir("test-pm")

        assert not project_dir.exists()
        adapter.write_claude_md("test-pm")
        assert project_dir.exists()

    def test_get_project_dir_returns_correct_path(self, registry, config):
        """get_project_dir returns projects_dir/<profile_name>."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        project_dir = adapter.get_project_dir("test-pm")

        assert project_dir == config.projects_dir / "test-pm"

    def test_sync_all_profiles_generates_all_files(self, registry, config):
        """sync_all_profiles creates CLAUDE.md for every registered profile."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        result = adapter.sync_all_profiles()

        # Should have a path for every registered profile
        all_profiles = {p.profile_name for p in registry.list_profiles()}
        for profile_name in all_profiles:
            assert profile_name in result
            assert result[profile_name].exists()

    def test_unknown_profile_generates_fallback_content(self, registry, config):
        """generate_claude_md for an unknown profile returns fallback content (no crash)."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("nonexistent-profile")

        assert isinstance(content, str)
        assert len(content) > 0

    def test_claude_md_has_separator_sections(self, registry, config):
        """CLAUDE.md sections are separated by --- dividers."""
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm")

        assert "---" in content

    def test_claude_md_with_pending_messages_section(self, registry, config, bus):
        """Pending IPC messages appear in CLAUDE.md when provided."""
        from core.ipc.models import MessagePriority, MessageType
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        # Send a message to test-pm
        bus.send(
            from_profile="hermes",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Deploy phase 6"},
            priority=MessagePriority.NORMAL,
        )
        pending = bus.poll("test-pm", limit=10)

        adapter = ClaudeCodeProfileAdapter(registry, config)
        content = adapter.generate_claude_md("test-pm", pending_messages=pending)

        assert "Pending" in content
        assert "hermes" in content


# ---------------------------------------------------------------------------
# 3. ClaudeCodeMessageBridge
# ---------------------------------------------------------------------------


class TestClaudeCodeMessageBridge:
    """Tests for IPC ↔ task file translation."""

    def _make_mock_message(self, msg_id="msg-001", from_profile="hermes",
                            to_profile="test-pm", priority="normal",
                            msg_type="task_request", payload=None):
        """Build a mock IPC message object."""
        from core.ipc.models import MessagePriority, MessageType
        from datetime import datetime, timezone

        msg = MagicMock()
        msg.message_id = msg_id
        msg.from_profile = from_profile
        msg.to_profile = to_profile
        msg.message_type = MessageType.TASK_REQUEST
        msg.priority = MessagePriority.NORMAL
        msg.payload = payload or {"message": "Test task"}
        msg.created_at = datetime.now(tz=timezone.utc)
        return msg

    def test_export_pending_messages_creates_files(self, config):
        """export_pending_messages writes one JSON file per message."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        messages = [
            self._make_mock_message("msg-001"),
            self._make_mock_message("msg-002"),
        ]

        report = bridge.export_pending_messages("test-pm", messages)

        assert len(report.exported) == 2
        assert len(report.errors) == 0

        # Verify files exist
        pending_dir = config.tasks_dir / "test-pm" / "pending"
        assert (pending_dir / "msg-001.json").exists()
        assert (pending_dir / "msg-002.json").exists()

    def test_export_message_file_contains_correct_fields(self, config):
        """Exported task JSON has all required fields."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        msg = self._make_mock_message("msg-123", payload={"message": "Do something"})
        bridge.export_pending_messages("test-pm", [msg])

        task_file = config.tasks_dir / "test-pm" / "pending" / "msg-123.json"
        data = json.loads(task_file.read_text())

        assert data["message_id"] == "msg-123"
        assert data["from_profile"] == "hermes"
        assert data["to_profile"] == "test-pm"
        assert "message_type" in data
        assert "priority" in data
        assert "payload" in data
        assert "created_at" in data

    def test_export_skips_existing_files(self, config):
        """export_pending_messages skips files that already exist."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        msg = self._make_mock_message("msg-dup")

        # Export once
        report1 = bridge.export_pending_messages("test-pm", [msg])
        assert len(report1.exported) == 1

        # Export again — should skip
        report2 = bridge.export_pending_messages("test-pm", [msg])
        assert len(report2.skipped) == 1
        assert len(report2.exported) == 0

    def test_export_empty_messages_list(self, config):
        """Exporting an empty list produces an empty report."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        report = bridge.export_pending_messages("test-pm", [])

        assert len(report.exported) == 0
        assert len(report.skipped) == 0
        assert len(report.errors) == 0

    def test_write_result_file_creates_file(self, config):
        """write_result_file creates a JSON result file in results/."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        path = bridge.write_result_file(
            profile_name="test-pm",
            message_id="msg-abc",
            result="Task completed successfully",
            from_profile="hermes",
            status="completed",
        )

        assert path.exists()
        assert path.name == "msg-abc.json"
        data = json.loads(path.read_text())
        assert data["message_id"] == "msg-abc"
        assert data["result"] == "Task completed successfully"
        assert data["status"] == "completed"
        assert data["from_profile"] == "hermes"
        assert "completed_at" in data

    def test_write_result_file_failed_status(self, config):
        """write_result_file supports failed status."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        path = bridge.write_result_file(
            profile_name="test-pm",
            message_id="msg-failed",
            result="Error: out of memory",
            from_profile="hermes",
            status="failed",
        )

        data = json.loads(path.read_text())
        assert data["status"] == "failed"

    def test_import_results_sends_response_to_bus(self, config, bus):
        """import_results reads result files and sends TASK_RESPONSE to IPC bus."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)

        # Write a result file
        bridge.write_result_file(
            profile_name="test-pm",
            message_id="msg-xyz",
            result="Done!",
            from_profile="hermes",
        )

        report = bridge.import_results("test-pm", bus)

        assert len(report.imported) == 1
        assert "msg-xyz" in report.imported

        # Verify result file was deleted
        result_file = config.tasks_dir / "test-pm" / "results" / "msg-xyz.json"
        assert not result_file.exists()

        # Verify message appeared in the hermes inbox
        pending = bus.poll("hermes", limit=10)
        assert len(pending) == 1
        assert pending[0].payload.get("original_message_id") == "msg-xyz"

    def test_import_results_no_results_dir(self, config, bus):
        """import_results returns empty report when results directory doesn't exist."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        report = bridge.import_results("no-such-profile", bus)

        assert len(report.imported) == 0
        assert len(report.errors) == 0

    def test_import_multiple_results(self, config, bus):
        """import_results processes multiple result files in one call."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)

        for i in range(3):
            bridge.write_result_file(
                profile_name="test-pm",
                message_id=f"msg-multi-{i}",
                result=f"Done task {i}",
                from_profile="hermes",
            )

        report = bridge.import_results("test-pm", bus)
        assert len(report.imported) == 3

    def test_list_pending_task_files_returns_sorted_list(self, config):
        """list_pending_task_files returns sorted list of pending JSON files."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        msgs = [
            self._make_mock_message(f"msg-{i:03d}")
            for i in range(3)
        ]
        bridge.export_pending_messages("test-pm", msgs)

        files = bridge.list_pending_task_files("test-pm")
        assert len(files) == 3
        names = [f.stem for f in files]
        assert names == sorted(names)

    def test_list_pending_task_files_empty_when_no_dir(self, config):
        """list_pending_task_files returns [] when pending directory doesn't exist."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        files = bridge.list_pending_task_files("nonexistent-profile")

        assert files == []

    def test_read_task_file_parses_json(self, config):
        """read_task_file parses a task JSON file correctly."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        msg = self._make_mock_message("msg-read", payload={"message": "Parse me"})
        bridge.export_pending_messages("test-pm", [msg])

        files = bridge.list_pending_task_files("test-pm")
        assert len(files) == 1

        data = bridge.read_task_file(files[0])
        assert data["message_id"] == "msg-read"
        assert data["payload"]["message"] == "Parse me"

    def test_clear_pending_file_removes_file(self, config):
        """clear_pending_file deletes the task file and returns True."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        msg = self._make_mock_message("msg-clear")
        bridge.export_pending_messages("test-pm", [msg])

        result = bridge.clear_pending_file("test-pm", "msg-clear")
        assert result is True

        pending_file = config.tasks_dir / "test-pm" / "pending" / "msg-clear.json"
        assert not pending_file.exists()

    def test_clear_pending_file_returns_false_when_not_found(self, config):
        """clear_pending_file returns False when file doesn't exist."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        result = bridge.clear_pending_file("test-pm", "msg-does-not-exist")

        assert result is False

    def test_get_profile_task_dir(self, config):
        """get_profile_task_dir returns tasks_dir/<profile>."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge

        bridge = ClaudeCodeMessageBridge(config)
        task_dir = bridge.get_profile_task_dir("test-pm")

        assert task_dir == config.tasks_dir / "test-pm"


# ---------------------------------------------------------------------------
# 4. ClaudeCodeMemoryAdapter
# ---------------------------------------------------------------------------


class TestClaudeCodeMemoryAdapter:
    """Tests for MemoryStore ↔ CLAUDE.md memory section conversion."""

    def test_format_entries_for_claude_md_returns_string(self, config, memory_entries):
        """format_entries_for_claude_md returns a non-empty string."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        result = adapter.format_entries_for_claude_md(memory_entries)

        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_entries_contains_content(self, config, memory_entries):
        """Formatted CLAUDE.md section contains entry content."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        result = adapter.format_entries_for_claude_md(memory_entries)

        assert "SQLite" in result
        assert "hierarchy databases" in result

    def test_format_entries_contains_tier_labels(self, config, memory_entries):
        """Formatted section uses tier labels (🔥, 📋, 🗄️)."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        result = adapter.format_entries_for_claude_md(memory_entries)

        # At minimum the hot tier should appear
        assert "🔥" in result or "Active Context" in result

    def test_format_entries_empty_list(self, config):
        """Empty entries list returns a 'no entries' message."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        result = adapter.format_entries_for_claude_md([])

        assert "No memory entries" in result

    def test_format_entries_custom_title(self, config, memory_entries):
        """Custom section_title appears in the output."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        result = adapter.format_entries_for_claude_md(
            memory_entries, section_title="My Custom Memory"
        )

        assert "My Custom Memory" in result

    def test_format_entries_truncates_long_content(self, config):
        """Long content (>400 chars for hot tier) is truncated with '...'."""
        from core.memory.models import (
            MemoryEntry, MemoryEntryType, MemoryScope, MemoryTier, generate_memory_id
        )
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        long_entry = MagicMock()
        long_entry.tier = MemoryTier.hot
        long_entry.entry_type = MemoryEntryType.context
        long_entry.content = "X" * 600  # Exceeds 400-char hot limit

        adapter = ClaudeCodeMemoryAdapter(config)
        result = adapter.format_entries_for_claude_md([long_entry])

        assert "..." in result

    def test_format_entries_respects_max_limit(self, config):
        """Only max_memory_entries entries are shown."""
        from integrations.claude_code.config import ClaudeCodeConfig
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        cfg = ClaudeCodeConfig(max_memory_entries=2)
        adapter = ClaudeCodeMemoryAdapter(cfg)

        # Create 5 mock entries
        entries = []
        for i in range(5):
            entry = MagicMock()
            entry.tier = MagicMock()
            entry.tier.value = "hot"
            entry.entry_type = MagicMock()
            entry.entry_type.value = "context"
            entry.content = f"Entry {i}"
            entries.append(entry)

        result = adapter.format_entries_for_claude_md(entries)

        # Only 2 should appear in the section (+ overflow message)
        assert "older entries not shown" in result

    def test_format_entries_as_context_dict(self, config, memory_entries):
        """format_entries_as_context_dict returns a dict grouped by tier."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        result = adapter.format_entries_as_context_dict(memory_entries)

        assert isinstance(result, dict)
        # Should have at least one tier key
        assert len(result) > 0
        # Each value is a list of dicts
        for tier_key, entries_list in result.items():
            assert isinstance(entries_list, list)
            for item in entries_list:
                assert "entry_id" in item
                assert "type" in item
                assert "content" in item

    def test_format_entries_as_context_dict_empty(self, config):
        """format_entries_as_context_dict returns empty dict for no entries."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        result = adapter.format_entries_as_context_dict([])

        assert result == {}

    def test_parse_claude_md_annotations_extracts_entries(self, config):
        """parse_claude_md_annotations finds memory blocks in CLAUDE.md."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)

        claude_md = """
# Test Profile

Some content here.

<!-- memory: type="decision" tier="hot" -->
Decided to use SQLite for the hierarchy.
<!-- /memory -->

<!-- memory: type="learning" tier="warm" -->
Tool registration requires name, schema, handler, check_fn.
<!-- /memory -->
"""
        annotations = adapter.parse_claude_md_annotations(claude_md)

        assert len(annotations) == 2

        tiers = {a[0] for a in annotations}
        types = {a[1] for a in annotations}
        assert "hot" in tiers
        assert "warm" in tiers
        assert "decision" in types
        assert "learning" in types

    def test_parse_claude_md_annotations_empty_file(self, config):
        """parse_claude_md_annotations returns empty list for no annotations."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        annotations = adapter.parse_claude_md_annotations("# No memory annotations here")

        assert annotations == []

    def test_parse_claude_md_annotations_extracts_content(self, config):
        """Annotation content is correctly extracted."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)

        claude_md = """<!-- memory: type="context" tier="hot" -->
This is the extracted content.
<!-- /memory -->"""

        annotations = adapter.parse_claude_md_annotations(claude_md)
        assert len(annotations) == 1
        tier, entry_type, content = annotations[0]
        assert "extracted content" in content

    def test_get_memory_summary(self, config, memory_entries):
        """get_memory_summary returns total_entries, tier_counts, total_bytes."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        summary = adapter.get_memory_summary(memory_entries)

        assert summary["total_entries"] == len(memory_entries)
        assert isinstance(summary["tier_counts"], dict)
        assert summary["total_bytes"] > 0

    def test_get_memory_summary_empty(self, config):
        """get_memory_summary handles empty list."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter

        adapter = ClaudeCodeMemoryAdapter(config)
        summary = adapter.get_memory_summary([])

        assert summary["total_entries"] == 0
        assert summary["tier_counts"] == {}
        assert summary["total_bytes"] == 0


# ---------------------------------------------------------------------------
# 5. ClaudeCodeAdapter (facade)
# ---------------------------------------------------------------------------


class TestClaudeCodeAdapter:
    """Tests for the ClaudeCodeAdapter top-level facade."""

    def test_adapter_creates_sub_adapters(self, registry, config):
        """ClaudeCodeAdapter initializes all three sub-adapters."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(registry=registry, config=config)

        assert adapter.profile_adapter is not None
        assert adapter.message_bridge is not None
        assert adapter.memory_adapter is not None

    def test_adapter_exposes_config_and_registry(self, registry, config):
        """config and registry properties return the injected objects."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(registry=registry, config=config)

        assert adapter.config is config
        assert adapter.registry is registry

    def test_adapter_uses_default_config_when_none_given(self, registry):
        """Omitting config creates a default ClaudeCodeConfig."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter
        from integrations.claude_code.config import ClaudeCodeConfig

        adapter = ClaudeCodeAdapter(registry=registry)

        assert isinstance(adapter.config, ClaudeCodeConfig)

    def test_prepare_session_returns_session_report(self, registry, config):
        """prepare_session returns a SessionReport with the profile name."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter, SessionReport

        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        report = adapter.prepare_session("test-pm")

        assert isinstance(report, SessionReport)
        assert report.profile_name == "test-pm"

    def test_prepare_session_generates_claude_md(self, registry, config):
        """prepare_session writes a CLAUDE.md file and reports its path."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        report = adapter.prepare_session("test-pm")

        assert report.claude_md_path is not None
        assert report.claude_md_path.exists()

    def test_prepare_session_is_ok_when_no_errors(self, registry, config):
        """is_ok() returns True for a successful prepare_session."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        report = adapter.prepare_session("test-pm")

        assert report.is_ok()
        assert report.errors == []

    def test_prepare_session_with_message_bus_exports_messages(self, registry, config, bus):
        """prepare_session with a message_bus exports pending messages."""
        from core.ipc.models import MessagePriority, MessageType
        from integrations.claude_code.adapter import ClaudeCodeAdapter

        # Send 2 messages to test-pm
        for i in range(2):
            bus.send(
                from_profile="hermes",
                to_profile="test-pm",
                message_type=MessageType.TASK_REQUEST,
                payload={"message": f"Task {i}"},
                priority=MessagePriority.NORMAL,
            )

        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        report = adapter.prepare_session("test-pm", message_bus=bus)

        assert report.exported_messages == 2

    def test_prepare_session_with_memory_store_embeds_entries(
        self, registry, config, memory_entries
    ):
        """prepare_session with a memory_store embeds entries in CLAUDE.md."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter

        # Build a mock memory store that returns our real entries
        mock_store = MagicMock()
        mock_store.list_entries.return_value = memory_entries

        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        report = adapter.prepare_session("test-pm", memory_store=mock_store)

        assert report.memory_entries_embedded == len(memory_entries)

        # CLAUDE.md should contain memory content
        content = report.claude_md_path.read_text(encoding="utf-8")
        assert "SQLite" in content

    def test_prepare_session_str_representation(self, registry, config):
        """SessionReport str() includes profile name and key metrics."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        report = adapter.prepare_session("test-pm")

        report_str = str(report)
        assert "test-pm" in report_str

    def test_sync_all_profiles_returns_dict(self, registry, config):
        """sync_all_profiles returns a dict of profile_name → SessionReport."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter, SessionReport

        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        results = adapter.sync_all_profiles()

        assert isinstance(results, dict)
        # Should have an entry for each registered profile
        all_profiles = {p.profile_name for p in registry.list_profiles()}
        for profile_name in all_profiles:
            assert profile_name in results
            assert isinstance(results[profile_name], SessionReport)

    def test_sync_all_profiles_all_ok(self, registry, config):
        """sync_all_profiles: all reports are ok for a clean registry."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        results = adapter.sync_all_profiles()

        for profile_name, report in results.items():
            assert report.is_ok(), f"Profile '{profile_name}' had errors: {report.errors}"

    def test_import_results_delegates_to_bridge(self, registry, config, bus):
        """import_results uses the message bridge to import results."""
        from integrations.claude_code.adapter import ClaudeCodeAdapter
        from integrations.claude_code.message_bridge import ImportReport

        # Write a result file directly via bridge
        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        adapter.message_bridge.write_result_file(
            profile_name="test-pm",
            message_id="msg-facade",
            result="Facade test done",
            from_profile="hermes",
        )

        report = adapter.import_results("test-pm", bus)

        assert isinstance(report, ImportReport)
        assert "msg-facade" in report.imported


# ---------------------------------------------------------------------------
# 6. Package-level imports (__init__.py)
# ---------------------------------------------------------------------------


class TestPackageImports:
    """Verify all public symbols are importable from integrations.claude_code."""

    def test_all_exports_importable(self):
        """All names in __all__ can be imported from the package."""
        from integrations.claude_code import (
            ClaudeCodeAdapter,
            ClaudeCodeConfig,
            ClaudeCodeMemoryAdapter,
            ClaudeCodeMessageBridge,
            ClaudeCodeProfileAdapter,
        )

        assert ClaudeCodeAdapter is not None
        assert ClaudeCodeConfig is not None
        assert ClaudeCodeMemoryAdapter is not None
        assert ClaudeCodeMessageBridge is not None
        assert ClaudeCodeProfileAdapter is not None

    def test_all_list_matches_exports(self):
        """__all__ contains the expected 5 public classes."""
        import integrations.claude_code as pkg

        expected = {
            "ClaudeCodeAdapter",
            "ClaudeCodeConfig",
            "ClaudeCodeMemoryAdapter",
            "ClaudeCodeMessageBridge",
            "ClaudeCodeProfileAdapter",
        }
        assert set(pkg.__all__) == expected


# ---------------------------------------------------------------------------
# 7. End-to-end flows
# ---------------------------------------------------------------------------


class TestEndToEndFlows:
    """Cross-component integration tests."""

    def test_full_session_prep_roundtrip(self, registry, config, bus, memory_entries):
        """Full flow: send IPC message → prepare_session → verify CLAUDE.md."""
        from core.ipc.models import MessagePriority, MessageType
        from integrations.claude_code.adapter import ClaudeCodeAdapter

        # Send a message to test-pm
        bus.send(
            from_profile="hermes",
            to_profile="test-pm",
            message_type=MessageType.TASK_REQUEST,
            payload={"message": "Implement the auth module"},
            priority=MessagePriority.URGENT,
        )

        # Mock memory store
        mock_store = MagicMock()
        mock_store.list_entries.return_value = memory_entries

        adapter = ClaudeCodeAdapter(registry=registry, config=config)
        report = adapter.prepare_session("test-pm", message_bus=bus, memory_store=mock_store)

        # Session should be OK
        assert report.is_ok()
        assert report.exported_messages == 1
        assert report.memory_entries_embedded == len(memory_entries)

        # CLAUDE.md should contain both memory and message context
        content = report.claude_md_path.read_text(encoding="utf-8")
        assert "test-pm" in content
        assert "Project Manager" in content

    def test_export_then_import_result_roundtrip(self, config, bus):
        """Export a message as task file, write result, import back to bus."""
        from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge
        from core.ipc.models import MessagePriority, MessageType
        from unittest.mock import MagicMock
        from datetime import datetime, timezone

        bridge = ClaudeCodeMessageBridge(config)

        # Build a fake IPC message
        msg = MagicMock()
        msg.message_id = "msg-roundtrip"
        msg.from_profile = "hermes"
        msg.to_profile = "test-pm"
        msg.message_type = MessageType.TASK_REQUEST
        msg.priority = MessagePriority.NORMAL
        msg.payload = {"message": "Do the roundtrip"}
        msg.created_at = datetime.now(tz=timezone.utc)

        # Export
        export_report = bridge.export_pending_messages("test-pm", [msg])
        assert len(export_report.exported) == 1

        # Verify task file exists
        files = bridge.list_pending_task_files("test-pm")
        assert len(files) == 1

        # Simulate Claude Code completing the task
        bridge.write_result_file(
            profile_name="test-pm",
            message_id="msg-roundtrip",
            result="Roundtrip done!",
            from_profile="hermes",
        )

        # Import result back to bus
        import_report = bridge.import_results("test-pm", bus)
        assert len(import_report.imported) == 1

        # Verify response in bus
        pending = bus.poll("hermes", limit=10)
        assert len(pending) == 1
        assert "Roundtrip done!" in pending[0].payload.get("result", "")

    def test_memory_adapter_and_profile_adapter_integration(
        self, registry, config, memory_entries
    ):
        """MemoryAdapter output integrates correctly with ProfileAdapter CLAUDE.md."""
        from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter
        from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

        mem_adapter = ClaudeCodeMemoryAdapter(config)
        profile_adapter = ClaudeCodeProfileAdapter(registry, config)

        # Format entries using memory adapter
        memory_section = mem_adapter.format_entries_for_claude_md(memory_entries)

        # Generate CLAUDE.md with the entries
        content = profile_adapter.generate_claude_md("test-pm", memory_entries=memory_entries)

        # Content should integrate memory naturally
        assert "SQLite" in content
        assert "Test PM" in content


# ---------------------------------------------------------------------------
# Entry point for direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
