"""Tests for memory database schema and initialization."""

from __future__ import annotations

import os
import sqlite3

import pytest
from core.memory.schema import (
    init_memory_db,
    get_schema_version,
    SCHEMA_VERSION,
    CREATE_INDEXES,
)


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------


class TestSchemaCreation:
    """Tests that all tables and indexes are created properly."""

    def test_creates_memory_entries_table(self, memory_db) -> None:
        cursor = memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_entries'"
        )
        assert cursor.fetchone() is not None

    def test_creates_knowledge_base_table(self, memory_db) -> None:
        cursor = memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_base'"
        )
        assert cursor.fetchone() is not None

    def test_creates_tier_transitions_table(self, memory_db) -> None:
        cursor = memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tier_transitions'"
        )
        assert cursor.fetchone() is not None

    def test_creates_memory_budgets_table(self, memory_db) -> None:
        cursor = memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_budgets'"
        )
        assert cursor.fetchone() is not None

    def test_creates_schema_version_table(self, memory_db) -> None:
        cursor = memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        assert cursor.fetchone() is not None

    def test_memory_entries_columns(self, memory_db) -> None:
        cursor = memory_db.execute("PRAGMA table_info(memory_entries)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "entry_id", "profile_name", "scope", "tier", "entry_type",
            "content", "metadata", "created_at", "updated_at",
            "accessed_at", "expires_at", "byte_size",
        }
        assert columns == expected

    def test_knowledge_base_columns(self, memory_db) -> None:
        cursor = memory_db.execute("PRAGMA table_info(knowledge_base)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "entry_id", "profile_name", "category", "title", "content",
            "source_profile", "source_context", "tags",
            "created_at", "updated_at",
        }
        assert columns == expected

    def test_tier_transitions_columns(self, memory_db) -> None:
        cursor = memory_db.execute("PRAGMA table_info(tier_transitions)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "transition_id", "entry_id", "from_tier", "to_tier",
            "reason", "transitioned_at",
        }
        assert columns == expected

    def test_memory_budgets_columns(self, memory_db) -> None:
        cursor = memory_db.execute("PRAGMA table_info(memory_budgets)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "profile_name", "max_entries", "max_bytes", "tier_quotas",
        }
        assert columns == expected

    def test_creates_indexes(self, memory_db) -> None:
        cursor = memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_memory_entries_profile" in indexes
        assert "idx_memory_entries_tier" in indexes
        assert "idx_memory_entries_scope" in indexes
        assert "idx_memory_entries_type" in indexes
        assert "idx_memory_entries_accessed" in indexes
        assert "idx_memory_entries_expires" in indexes
        assert "idx_knowledge_base_profile" in indexes
        assert "idx_knowledge_base_category" in indexes
        assert "idx_tier_transitions_entry" in indexes

    def test_row_factory_set(self, memory_db) -> None:
        assert memory_db.row_factory == sqlite3.Row

    def test_idempotent(self) -> None:
        """Calling init_memory_db twice should not raise."""
        conn1 = init_memory_db(":memory:")
        # Re-initializing on the same connection shouldn't fail.
        conn2 = init_memory_db(":memory:")
        conn1.close()
        conn2.close()


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    """Tests for schema versioning."""

    def test_version_is_1(self) -> None:
        assert SCHEMA_VERSION == 1

    def test_returns_version(self, memory_db) -> None:
        version = get_schema_version(memory_db)
        assert version == SCHEMA_VERSION

    def test_returns_zero_for_empty_db(self) -> None:
        conn = sqlite3.connect(":memory:")
        version = get_schema_version(conn)
        assert version == 0
        conn.close()


# ---------------------------------------------------------------------------
# In-memory database
# ---------------------------------------------------------------------------


class TestInMemoryDB:
    """Tests for in-memory database usage."""

    def test_works_with_memory(self) -> None:
        conn = init_memory_db(":memory:")
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "memory_entries" in tables
        assert "knowledge_base" in tables
        assert "tier_transitions" in tables
        assert "memory_budgets" in tables
        assert "schema_version" in tables
        conn.close()

    def test_insert_and_read_memory_entry(self, memory_db) -> None:
        """Can insert and read back a memory entry."""
        memory_db.execute(
            "INSERT INTO memory_entries "
            "(entry_id, profile_name, scope, tier, entry_type, content, "
            "created_at, updated_at, accessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("mem-test1234", "ceo", "strategic", "hot", "decision",
             "Test content", "2025-01-01T00:00:00+00:00",
             "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
        )
        memory_db.commit()
        cursor = memory_db.execute(
            "SELECT * FROM memory_entries WHERE entry_id = ?",
            ("mem-test1234",),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["profile_name"] == "ceo"
        assert row["scope"] == "strategic"

    def test_insert_and_read_knowledge_entry(self, memory_db) -> None:
        """Can insert and read back a knowledge entry."""
        memory_db.execute(
            "INSERT INTO knowledge_base "
            "(entry_id, profile_name, category, title, content, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("kb-test1234", "cto", "arch", "Title", "Content",
             "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
        )
        memory_db.commit()
        cursor = memory_db.execute(
            "SELECT * FROM knowledge_base WHERE entry_id = ?",
            ("kb-test1234",),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["profile_name"] == "cto"


# ---------------------------------------------------------------------------
# WAL mode
# ---------------------------------------------------------------------------


class TestWALMode:
    """Tests for WAL journal mode."""

    def test_wal_enabled_for_file_db(self, memory_db_path) -> None:
        conn = init_memory_db(memory_db_path)
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_file_backed_db_created(self, memory_db_path) -> None:
        conn = init_memory_db(memory_db_path)
        assert os.path.exists(memory_db_path)
        conn.close()

    def test_creates_parent_directory(self, temp_dir) -> None:
        db_path = os.path.join(temp_dir, "subdir", "nested", "memory.db")
        conn = init_memory_db(db_path)
        assert os.path.exists(os.path.dirname(db_path))
        conn.close()


# ---------------------------------------------------------------------------
# CHECK constraints
# ---------------------------------------------------------------------------


class TestConstraints:
    """Tests for CHECK constraints on enum-like columns."""

    def test_scope_check_valid(self, memory_db) -> None:
        """Valid scope values should be accepted."""
        for scope in ("strategic", "domain", "project", "task"):
            memory_db.execute(
                "INSERT INTO memory_entries "
                "(entry_id, profile_name, scope, tier, entry_type, content, "
                "created_at, updated_at, accessed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"mem-{scope}00", "ceo", scope, "hot", "decision",
                 "Content", "2025-01-01T00:00:00+00:00",
                 "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
            )

    def test_scope_check_invalid(self, memory_db) -> None:
        """Invalid scope value should violate CHECK constraint."""
        with pytest.raises(sqlite3.IntegrityError):
            memory_db.execute(
                "INSERT INTO memory_entries "
                "(entry_id, profile_name, scope, tier, entry_type, content, "
                "created_at, updated_at, accessed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("mem-invalid0", "ceo", "invalid_scope", "hot", "decision",
                 "Content", "2025-01-01T00:00:00+00:00",
                 "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
            )

    def test_tier_check_valid(self, memory_db) -> None:
        """Valid tier values should be accepted."""
        for i, tier in enumerate(("hot", "warm", "cool", "cold")):
            memory_db.execute(
                "INSERT INTO memory_entries "
                "(entry_id, profile_name, scope, tier, entry_type, content, "
                "created_at, updated_at, accessed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"mem-tier{i:04d}", "ceo", "strategic", tier, "decision",
                 "Content", "2025-01-01T00:00:00+00:00",
                 "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
            )

    def test_tier_check_invalid(self, memory_db) -> None:
        """Invalid tier value should violate CHECK constraint."""
        with pytest.raises(sqlite3.IntegrityError):
            memory_db.execute(
                "INSERT INTO memory_entries "
                "(entry_id, profile_name, scope, tier, entry_type, content, "
                "created_at, updated_at, accessed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("mem-invalid1", "ceo", "strategic", "boiling", "decision",
                 "Content", "2025-01-01T00:00:00+00:00",
                 "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
            )

    def test_entry_type_check_valid(self, memory_db) -> None:
        """Valid entry_type values should be accepted."""
        for i, et in enumerate(("preference", "decision", "learning",
                                 "context", "summary", "artifact")):
            memory_db.execute(
                "INSERT INTO memory_entries "
                "(entry_id, profile_name, scope, tier, entry_type, content, "
                "created_at, updated_at, accessed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"mem-type{i:04d}", "ceo", "strategic", "hot", et,
                 "Content", "2025-01-01T00:00:00+00:00",
                 "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
            )

    def test_entry_type_check_invalid(self, memory_db) -> None:
        """Invalid entry_type value should violate CHECK constraint."""
        with pytest.raises(sqlite3.IntegrityError):
            memory_db.execute(
                "INSERT INTO memory_entries "
                "(entry_id, profile_name, scope, tier, entry_type, content, "
                "created_at, updated_at, accessed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("mem-invalid2", "ceo", "strategic", "hot", "garbage",
                 "Content", "2025-01-01T00:00:00+00:00",
                 "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
            )

    def test_tier_transitions_from_tier_check(self, memory_db) -> None:
        """Invalid from_tier in tier_transitions should fail."""
        with pytest.raises(sqlite3.IntegrityError):
            memory_db.execute(
                "INSERT INTO tier_transitions "
                "(transition_id, entry_id, from_tier, to_tier, reason, transitioned_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("tt-invalid0", "mem-test1234", "invalid", "warm", "test",
                 "2025-01-01T00:00:00+00:00"),
            )

    def test_tier_transitions_to_tier_check(self, memory_db) -> None:
        """Invalid to_tier in tier_transitions should fail."""
        with pytest.raises(sqlite3.IntegrityError):
            memory_db.execute(
                "INSERT INTO tier_transitions "
                "(transition_id, entry_id, from_tier, to_tier, reason, transitioned_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("tt-invalid1", "mem-test1234", "hot", "invalid", "test",
                 "2025-01-01T00:00:00+00:00"),
            )

    def test_primary_key_uniqueness(self, memory_db) -> None:
        """Duplicate entry_id should fail."""
        memory_db.execute(
            "INSERT INTO memory_entries "
            "(entry_id, profile_name, scope, tier, entry_type, content, "
            "created_at, updated_at, accessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("mem-dupe0000", "ceo", "strategic", "hot", "decision",
             "Content", "2025-01-01T00:00:00+00:00",
             "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            memory_db.execute(
                "INSERT INTO memory_entries "
                "(entry_id, profile_name, scope, tier, entry_type, content, "
                "created_at, updated_at, accessed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("mem-dupe0000", "cto", "domain", "warm", "learning",
                 "Other", "2025-01-01T00:00:00+00:00",
                 "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
            )
