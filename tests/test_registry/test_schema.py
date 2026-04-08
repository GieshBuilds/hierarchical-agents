"""Tests for SQLite schema creation and versioning."""

from __future__ import annotations

import sqlite3

import pytest

from core.registry.schema import (
    CREATE_INDEXES,
    CREATE_PROFILES_TABLE,
    CREATE_SCHEMA_VERSION_TABLE,
    SCHEMA_VERSION,
    init_db,
)


class TestSchemaCreation:
    """Verify that init_db creates all expected tables and indexes."""

    def test_init_db_returns_connection(self) -> None:
        """init_db should return an open sqlite3.Connection."""
        conn = init_db(":memory:")
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()

    def test_profiles_table_exists(self, in_memory_db: sqlite3.Connection) -> None:
        """The 'profiles' table must exist after init_db."""
        row = in_memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='profiles'"
        ).fetchone()
        assert row is not None

    def test_schema_version_table_exists(self, in_memory_db: sqlite3.Connection) -> None:
        """The 'schema_version' table must exist after init_db."""
        row = in_memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        assert row is not None

    def test_profiles_table_columns(self, in_memory_db: sqlite3.Connection) -> None:
        """The profiles table should contain all expected columns."""
        cursor = in_memory_db.execute("PRAGMA table_info(profiles)")
        columns = {row["name"] for row in cursor.fetchall()}
        expected = {
            "profile_name",
            "display_name",
            "role",
            "parent_profile",
            "department",
            "status",
            "created_at",
            "updated_at",
            "config_path",
            "description",
        }
        assert expected.issubset(columns)


class TestIndexes:
    """Verify the expected indexes are created."""

    def test_parent_index_exists(self, in_memory_db: sqlite3.Connection) -> None:
        row = in_memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_profiles_parent'"
        ).fetchone()
        assert row is not None

    def test_department_index_exists(self, in_memory_db: sqlite3.Connection) -> None:
        row = in_memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_profiles_department'"
        ).fetchone()
        assert row is not None

    def test_status_index_exists(self, in_memory_db: sqlite3.Connection) -> None:
        row = in_memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_profiles_status'"
        ).fetchone()
        assert row is not None


class TestSchemaVersion:
    """Verify schema version tracking."""

    def test_schema_version_recorded(self, in_memory_db: sqlite3.Connection) -> None:
        """A fresh database should have the current SCHEMA_VERSION recorded."""
        row = in_memory_db.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        assert row["v"] == SCHEMA_VERSION

    def test_schema_version_not_duplicated_on_reinit(self, tmp_path) -> None:
        """Calling init_db twice on the same file should NOT add a duplicate version row."""
        db_path = str(tmp_path / "test.db")
        conn1 = init_db(db_path)
        conn1.close()

        conn2 = init_db(db_path)
        try:
            count = conn2.execute(
                "SELECT COUNT(*) AS c FROM schema_version"
            ).fetchone()["c"]
            assert count == 1
        finally:
            conn2.close()

    def test_schema_version_value(self) -> None:
        """SCHEMA_VERSION constant should be a positive integer."""
        assert isinstance(SCHEMA_VERSION, int)
        assert SCHEMA_VERSION >= 1


class TestForeignKeys:
    """Verify that foreign-key enforcement is enabled."""

    def test_foreign_keys_enabled(self, in_memory_db: sqlite3.Connection) -> None:
        """Foreign keys should be ON after init_db."""
        fk = in_memory_db.execute("PRAGMA foreign_keys").fetchone()
        assert fk[0] == 1

    def test_self_referential_fk(self, in_memory_db: sqlite3.Connection) -> None:
        """parent_profile should reference profiles(profile_name)."""
        # Insert a valid CEO (no parent)
        in_memory_db.execute(
            "INSERT INTO profiles (profile_name, display_name, role) "
            "VALUES ('ceo', 'CEO', 'ceo')"
        )
        # Insert a dept head referencing the CEO — should succeed
        in_memory_db.execute(
            "INSERT INTO profiles (profile_name, display_name, role, parent_profile) "
            "VALUES ('cto', 'CTO', 'department_head', 'ceo')"
        )
        in_memory_db.commit()

        # Insert a profile referencing a non-existent parent — should fail
        with pytest.raises(sqlite3.IntegrityError):
            in_memory_db.execute(
                "INSERT INTO profiles (profile_name, display_name, role, parent_profile) "
                "VALUES ('bad', 'Bad', 'department_head', 'nonexistent')"
            )


class TestRoleConstraint:
    """Verify CHECK constraint on the role column."""

    def test_valid_roles_accepted(self, in_memory_db: sqlite3.Connection) -> None:
        """All three valid roles should be accepted by the CHECK constraint."""
        for role in ("ceo", "department_head", "project_manager"):
            name = f"test-{role}"
            in_memory_db.execute(
                "INSERT INTO profiles (profile_name, display_name, role) "
                f"VALUES ('{name}', '{name}', '{role}')"
            )
        in_memory_db.commit()

    def test_invalid_role_rejected(self, in_memory_db: sqlite3.Connection) -> None:
        """An invalid role value should be rejected by the CHECK constraint."""
        with pytest.raises(sqlite3.IntegrityError):
            in_memory_db.execute(
                "INSERT INTO profiles (profile_name, display_name, role) "
                "VALUES ('bad', 'Bad', 'intern')"
            )


class TestStatusConstraint:
    """Verify CHECK constraint on the status column."""

    def test_valid_statuses_accepted(self, in_memory_db: sqlite3.Connection) -> None:
        for i, status in enumerate(("active", "suspended", "archived")):
            name = f"test-status-{i}"
            in_memory_db.execute(
                "INSERT INTO profiles (profile_name, display_name, role, status) "
                f"VALUES ('{name}', '{name}', 'ceo', '{status}')"
            )
        in_memory_db.commit()

    def test_invalid_status_rejected(self, in_memory_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            in_memory_db.execute(
                "INSERT INTO profiles (profile_name, display_name, role, status) "
                "VALUES ('bad', 'Bad', 'ceo', 'deleted')"
            )
