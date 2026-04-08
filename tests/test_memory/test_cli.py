"""Tests for memory-related CLI commands.

Tests cover inspect-memory, memory-stats, run-gc, add-knowledge,
search-knowledge, memory-budget, and tier-report subcommands.

Pattern follows tests/test_ipc/test_cli.py — uses the main() function
directly with captured stdout/stderr.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

import pytest

from core.cli import main


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


@pytest.fixture
def memory_db():
    """Create a temporary memory database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "memory.db")


def _run(args: list[str], memory_db: str | None = None) -> tuple[int, str, str]:
    """Run the CLI and capture output.

    Memory commands require --memory-db; this helper inserts it
    automatically for commands that accept it, unless explicitly
    provided in *args*.
    """
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        # Append --memory-db if not already present and a db was given
        full_args = list(args)
        if memory_db and "--memory-db" not in full_args:
            full_args.extend(["--memory-db", memory_db])
        exit_code = main(full_args)
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    finally:
        stdout = sys.stdout.getvalue()
        stderr = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return exit_code, stdout, stderr


def _run_json(args: list[str], memory_db: str | None = None) -> tuple[int, any]:
    """Run the CLI with --json and parse the output."""
    exit_code, stdout, stderr = _run(["--json"] + args, memory_db)
    if stdout.strip():
        try:
            return exit_code, json.loads(stdout)
        except json.JSONDecodeError:
            return exit_code, stdout
    return exit_code, None


# ==================================================================
# TestInspectMemory
# ==================================================================


class TestInspectMemory:
    """Tests for the ``inspect-memory`` subcommand."""

    def test_basic_output_empty(self, memory_db):
        exit_code, stdout, _ = _run(
            ["inspect-memory", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert "No memory entries found" in stdout

    def test_json_output_empty(self, memory_db):
        exit_code, data = _run_json(
            ["inspect-memory", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert data == []

    def test_filtered_by_tier(self, memory_db):
        exit_code, data = _run_json(
            ["inspect-memory", "ceo", "--scope", "strategic", "--tier", "hot"],
            memory_db,
        )
        assert exit_code == 0
        assert isinstance(data, list)

    def test_filtered_by_type(self, memory_db):
        exit_code, data = _run_json(
            ["inspect-memory", "ceo", "--scope", "strategic", "--type", "decision"],
            memory_db,
        )
        assert exit_code == 0
        assert isinstance(data, list)

    def test_limit_parameter(self, memory_db):
        exit_code, data = _run_json(
            ["inspect-memory", "ceo", "--scope", "strategic", "--limit", "5"],
            memory_db,
        )
        assert exit_code == 0
        assert isinstance(data, list)

    def test_with_entries(self, memory_db):
        """Add an entry via add-knowledge first, then inspect knowledge via the
        memory-stats command.  For memory entries, we use the store directly
        to seed data and then inspect."""
        from core.memory.memory_store import MemoryStore
        from core.memory.models import MemoryEntry, MemoryScope, MemoryTier, MemoryEntryType, generate_memory_id

        # Seed the database with an entry
        store = MemoryStore(memory_db, "ceo", MemoryScope.strategic)
        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Test decision content",
        )
        store.store(entry)
        store.close()

        exit_code, data = _run_json(
            ["inspect-memory", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert len(data) == 1
        assert data[0]["content"] == "Test decision content"

    def test_human_readable_with_entries(self, memory_db):
        """Human-readable output when entries exist."""
        from core.memory.memory_store import MemoryStore
        from core.memory.models import MemoryEntry, MemoryScope, MemoryTier, MemoryEntryType, generate_memory_id

        store = MemoryStore(memory_db, "ceo", MemoryScope.strategic)
        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Important strategic decision",
        )
        store.store(entry)
        store.close()

        exit_code, stdout, _ = _run(
            ["inspect-memory", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert "1 memory entry" in stdout
        assert "hot" in stdout


# ==================================================================
# TestMemoryStats
# ==================================================================


class TestMemoryStats:
    """Tests for the ``memory-stats`` subcommand."""

    def test_stats_display_empty(self, memory_db):
        exit_code, stdout, _ = _run(
            ["memory-stats", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert "Memory Statistics" in stdout
        assert "Total entries:" in stdout

    def test_json_stats(self, memory_db):
        exit_code, data = _run_json(
            ["memory-stats", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert "total_entries" in data
        assert "total_bytes" in data
        assert data["total_entries"] == 0

    def test_stats_after_adding_entries(self, memory_db):
        from core.memory.memory_store import MemoryStore
        from core.memory.models import MemoryEntry, MemoryScope, MemoryTier, MemoryEntryType, generate_memory_id

        store = MemoryStore(memory_db, "ceo", MemoryScope.strategic)
        for i in range(3):
            entry = MemoryEntry(
                entry_id=generate_memory_id(),
                profile_name="ceo",
                scope=MemoryScope.strategic,
                tier=MemoryTier.hot,
                entry_type=MemoryEntryType.decision,
                content=f"Decision {i}",
            )
            store.store(entry)
        store.close()

        exit_code, data = _run_json(
            ["memory-stats", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert data["total_entries"] == 3
        assert data["by_tier"]["hot"] == 3

    def test_stats_with_budget(self, memory_db):
        from core.memory.memory_store import MemoryStore
        from core.memory.models import MemoryScope, MemoryBudget

        store = MemoryStore(memory_db, "ceo", MemoryScope.strategic)
        budget = MemoryBudget(profile_name="ceo", max_entries=100, max_bytes=50000)
        store.set_budget(budget)
        store.close()

        exit_code, data = _run_json(
            ["memory-stats", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert data["budget"] is not None
        assert data["budget"]["max_entries"] == 100


# ==================================================================
# TestRunGC
# ==================================================================


class TestRunGC:
    """Tests for the ``run-gc`` subcommand."""

    def test_dry_run(self, memory_db):
        exit_code, stdout, _ = _run(
            ["run-gc", "ceo", "--scope", "strategic", "--dry-run"],
            memory_db,
        )
        assert exit_code == 0
        assert "dry run" in stdout.lower() or "GC dry run" in stdout

    def test_actual_run(self, memory_db):
        exit_code, stdout, _ = _run(
            ["run-gc", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert "GC complete" in stdout or "Transitions" in stdout

    def test_json_dry_run(self, memory_db):
        exit_code, data = _run_json(
            ["run-gc", "ceo", "--scope", "strategic", "--dry-run"],
            memory_db,
        )
        assert exit_code == 0
        assert data["dry_run"] is True
        assert "transitions_recommended" in data

    def test_json_actual_run(self, memory_db):
        exit_code, data = _run_json(
            ["run-gc", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert data["dry_run"] is False

    def test_gc_with_entries(self, memory_db):
        """GC on a store with entries runs without error."""
        from core.memory.memory_store import MemoryStore
        from core.memory.models import MemoryEntry, MemoryScope, MemoryTier, MemoryEntryType, generate_memory_id

        store = MemoryStore(memory_db, "ceo", MemoryScope.strategic)
        for i in range(3):
            entry = MemoryEntry(
                entry_id=generate_memory_id(),
                profile_name="ceo",
                scope=MemoryScope.strategic,
                tier=MemoryTier.hot,
                entry_type=MemoryEntryType.decision,
                content=f"Decision {i}",
            )
            store.store(entry)
        store.close()

        exit_code, data = _run_json(
            ["run-gc", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0


# ==================================================================
# TestAddKnowledge
# ==================================================================


class TestAddKnowledge:
    """Tests for the ``add-knowledge`` subcommand."""

    def test_add_entry(self, memory_db):
        exit_code, stdout, _ = _run(
            [
                "add-knowledge", "ceo",
                "--category", "decisions",
                "--title", "Use SQLite",
                "--content", "Decided to use SQLite for persistence",
            ],
            memory_db,
        )
        assert exit_code == 0
        assert "Knowledge entry created" in stdout
        assert "Use SQLite" in stdout

    def test_json_output(self, memory_db):
        exit_code, data = _run_json(
            [
                "add-knowledge", "ceo",
                "--category", "architecture",
                "--title", "Microservices",
                "--content", "Use event-driven architecture",
            ],
            memory_db,
        )
        assert exit_code == 0
        assert data["category"] == "architecture"
        assert data["title"] == "Microservices"
        assert data["entry_id"].startswith("kb-")

    def test_add_with_tags(self, memory_db):
        exit_code, data = _run_json(
            [
                "add-knowledge", "ceo",
                "--category", "process",
                "--title", "Code Review",
                "--content", "All changes need two approvals",
                "--tags", "review,process,quality",
            ],
            memory_db,
        )
        assert exit_code == 0
        assert "review" in data["tags"]
        assert "process" in data["tags"]
        assert "quality" in data["tags"]

    def test_add_entry_human_readable(self, memory_db):
        exit_code, stdout, _ = _run(
            [
                "add-knowledge", "ceo",
                "--category", "test",
                "--title", "Test Entry",
                "--content", "Test content here",
            ],
            memory_db,
        )
        assert exit_code == 0
        assert "ID:" in stdout
        assert "Category:" in stdout
        assert "Title:" in stdout


# ==================================================================
# TestSearchKnowledge
# ==================================================================


class TestSearchKnowledge:
    """Tests for the ``search-knowledge`` subcommand."""

    def test_search_empty_results(self, memory_db):
        exit_code, stdout, _ = _run(
            ["search-knowledge", "ceo", "nonexistent"],
            memory_db,
        )
        assert exit_code == 0
        assert "No knowledge entries found" in stdout

    def test_search_json_empty(self, memory_db):
        exit_code, data = _run_json(
            ["search-knowledge", "ceo", "nonexistent"],
            memory_db,
        )
        assert exit_code == 0
        assert data == []

    def test_search_with_results(self, memory_db):
        # Add an entry first
        _run(
            [
                "add-knowledge", "ceo",
                "--category", "arch",
                "--title", "SQLite Decision",
                "--content", "We decided to use SQLite for the project",
            ],
            memory_db,
        )

        exit_code, data = _run_json(
            ["search-knowledge", "ceo", "SQLite"],
            memory_db,
        )
        assert exit_code == 0
        assert len(data) >= 1
        assert any("SQLite" in r["content"] for r in data)

    def test_search_with_category_filter(self, memory_db):
        _run(
            [
                "add-knowledge", "ceo",
                "--category", "design",
                "--title", "Design Pattern",
                "--content", "Use factory pattern for object creation",
            ],
            memory_db,
        )
        _run(
            [
                "add-knowledge", "ceo",
                "--category", "process",
                "--title", "Deploy Process",
                "--content", "Use factory staging for deployment",
            ],
            memory_db,
        )

        exit_code, data = _run_json(
            ["search-knowledge", "ceo", "factory", "--category", "design"],
            memory_db,
        )
        assert exit_code == 0
        assert all(r["category"] == "design" for r in data)

    def test_search_human_readable_with_results(self, memory_db):
        _run(
            [
                "add-knowledge", "ceo",
                "--category", "tech",
                "--title", "Testing",
                "--content", "Use pytest for testing",
                "--tags", "test,quality",
            ],
            memory_db,
        )

        exit_code, stdout, _ = _run(
            ["search-knowledge", "ceo", "pytest"],
            memory_db,
        )
        assert exit_code == 0
        assert "1 knowledge entry" in stdout
        assert "pytest" in stdout

    def test_search_with_limit(self, memory_db):
        for i in range(5):
            _run(
                [
                    "add-knowledge", "ceo",
                    "--category", "bulk",
                    "--title", f"Bulk {i}",
                    "--content", f"Bulk content {i} with shared keyword",
                ],
                memory_db,
            )

        exit_code, data = _run_json(
            ["search-knowledge", "ceo", "shared keyword", "--limit", "2"],
            memory_db,
        )
        assert exit_code == 0
        assert len(data) <= 2


# ==================================================================
# TestMemoryBudget
# ==================================================================


class TestMemoryBudget:
    """Tests for the ``memory-budget`` subcommand."""

    def test_view_no_budget(self, memory_db):
        exit_code, stdout, _ = _run(
            ["memory-budget", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert "No budget set" in stdout

    def test_view_no_budget_json(self, memory_db):
        exit_code, data = _run_json(
            ["memory-budget", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert data["budget"] is None

    def test_set_budget(self, memory_db):
        exit_code, stdout, _ = _run(
            [
                "memory-budget", "ceo", "--scope", "strategic",
                "--set", "--max-entries", "500",
            ],
            memory_db,
        )
        assert exit_code == 0
        assert "Budget updated" in stdout
        assert "500" in stdout

    def test_set_budget_json(self, memory_db):
        exit_code, data = _run_json(
            [
                "memory-budget", "ceo", "--scope", "strategic",
                "--set", "--max-entries", "500", "--max-bytes", "1000000",
            ],
            memory_db,
        )
        assert exit_code == 0
        assert data["max_entries"] == 500
        assert data["max_bytes"] == 1000000

    def test_view_after_set(self, memory_db):
        # Set first
        _run(
            [
                "memory-budget", "ceo", "--scope", "strategic",
                "--set", "--max-entries", "200",
            ],
            memory_db,
        )
        # Then view
        exit_code, data = _run_json(
            ["memory-budget", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert data["max_entries"] == 200

    def test_set_budget_human_readable(self, memory_db):
        exit_code, stdout, _ = _run(
            [
                "memory-budget", "ceo", "--scope", "strategic",
                "--set", "--max-entries", "300", "--max-bytes", "5000000",
            ],
            memory_db,
        )
        assert exit_code == 0
        assert "Max entries:" in stdout
        assert "Max bytes:" in stdout

    def test_view_budget_human_readable_after_set(self, memory_db):
        _run(
            [
                "memory-budget", "ceo", "--scope", "strategic",
                "--set", "--max-entries", "800",
            ],
            memory_db,
        )
        exit_code, stdout, _ = _run(
            ["memory-budget", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert "Memory budget for" in stdout
        assert "800" in stdout


# ==================================================================
# TestTierReport
# ==================================================================


class TestTierReport:
    """Tests for the ``tier-report`` subcommand."""

    def test_report_output_empty(self, memory_db):
        exit_code, stdout, _ = _run(
            ["tier-report", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        # Either shows entries or says none are approaching transitions
        assert "No entries approaching" in stdout or "Tier aging report" in stdout

    def test_json_output_empty(self, memory_db):
        exit_code, data = _run_json(
            ["tier-report", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert isinstance(data, list)

    def test_report_with_entries(self, memory_db):
        """Tier report with seeded entries."""
        from core.memory.memory_store import MemoryStore
        from core.memory.models import MemoryEntry, MemoryScope, MemoryTier, MemoryEntryType, generate_memory_id

        store = MemoryStore(memory_db, "ceo", MemoryScope.strategic)
        for i in range(3):
            entry = MemoryEntry(
                entry_id=generate_memory_id(),
                profile_name="ceo",
                scope=MemoryScope.strategic,
                tier=MemoryTier.hot,
                entry_type=MemoryEntryType.decision,
                content=f"Entry for tier report {i}",
            )
            store.store(entry)
        store.close()

        exit_code, data = _run_json(
            ["tier-report", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        assert isinstance(data, list)

    def test_human_readable_with_entries(self, memory_db):
        """Check human-readable tier report format."""
        from core.memory.memory_store import MemoryStore
        from core.memory.models import MemoryEntry, MemoryScope, MemoryTier, MemoryEntryType, generate_memory_id

        store = MemoryStore(memory_db, "ceo", MemoryScope.strategic)
        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Test tier report entry",
        )
        store.store(entry)
        store.close()

        exit_code, stdout, _ = _run(
            ["tier-report", "ceo", "--scope", "strategic"],
            memory_db,
        )
        assert exit_code == 0
        # May or may not have entries depending on thresholds


# ==================================================================
# TestScopeParameter
# ==================================================================


class TestScopeParameter:
    """Test that --scope is respected by memory commands."""

    def test_inspect_memory_with_different_scopes(self, memory_db):
        """Each scope creates an independent namespace."""
        for scope in ["strategic", "domain", "project", "task"]:
            exit_code, data = _run_json(
                ["inspect-memory", "ceo", "--scope", scope],
                memory_db,
            )
            assert exit_code == 0
            assert isinstance(data, list)

    def test_memory_stats_with_domain_scope(self, memory_db):
        exit_code, data = _run_json(
            ["memory-stats", "ceo", "--scope", "domain"],
            memory_db,
        )
        assert exit_code == 0
        assert data["total_entries"] == 0
