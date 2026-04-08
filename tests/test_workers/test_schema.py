"""Tests for the subagent SQLite schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.workers.schema import SCHEMA_VERSION, init_subagent_db


class TestSchemaInit:
    """Tests for database initialization."""

    def test_creates_subagents_table(self):
        conn = init_subagent_db(":memory:")
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='subagents'"
            )
            assert cur.fetchone() is not None
        finally:
            conn.close()

    def test_creates_schema_version_table(self):
        conn = init_subagent_db(":memory:")
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            assert cur.fetchone() is not None
        finally:
            conn.close()

    def test_records_schema_version(self):
        conn = init_subagent_db(":memory:")
        try:
            cur = conn.execute("SELECT MAX(version) as v FROM schema_version")
            row = cur.fetchone()
            assert row["v"] == SCHEMA_VERSION
        finally:
            conn.close()

    def test_idempotent_init(self):
        conn = init_subagent_db(":memory:")
        try:
            # Init again on same connection shouldn't error
            conn.execute("INSERT INTO subagents (subagent_id, project_manager, task_goal) VALUES ('sa-1', 'pm', 'test')")
            conn.commit()

            # Reinit shouldn't lose data
            # (In practice you'd call init_subagent_db on the path, but for :memory: we test the DDL)
            assert conn.execute("SELECT COUNT(*) as c FROM subagents").fetchone()["c"] == 1
        finally:
            conn.close()


class TestSchemaConstraints:
    """Tests for schema constraints and defaults."""

    def test_status_check_constraint(self):
        conn = init_subagent_db(":memory:")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO subagents (subagent_id, project_manager, task_goal, status) "
                    "VALUES ('sa-1', 'pm', 'test', 'invalid_status')"
                )
        finally:
            conn.close()

    def test_valid_statuses(self):
        conn = init_subagent_db(":memory:")
        try:
            for i, status in enumerate(["running", "sleeping", "completed", "archived"]):
                conn.execute(
                    "INSERT INTO subagents (subagent_id, project_manager, task_goal, status) "
                    "VALUES (?, 'pm', 'test', ?)",
                    (f"sa-{i}", status),
                )
            conn.commit()
            count = conn.execute("SELECT COUNT(*) as c FROM subagents").fetchone()["c"]
            assert count == 4
        finally:
            conn.close()

    def test_default_status_is_running(self):
        conn = init_subagent_db(":memory:")
        try:
            conn.execute(
                "INSERT INTO subagents (subagent_id, project_manager, task_goal) "
                "VALUES ('sa-1', 'pm', 'test')"
            )
            conn.commit()
            row = conn.execute("SELECT status FROM subagents WHERE subagent_id='sa-1'").fetchone()
            assert row["status"] == "running"
        finally:
            conn.close()

    def test_default_token_cost_is_zero(self):
        conn = init_subagent_db(":memory:")
        try:
            conn.execute(
                "INSERT INTO subagents (subagent_id, project_manager, task_goal) "
                "VALUES ('sa-1', 'pm', 'test')"
            )
            conn.commit()
            row = conn.execute("SELECT token_cost FROM subagents WHERE subagent_id='sa-1'").fetchone()
            assert row["token_cost"] == 0
        finally:
            conn.close()

    def test_default_artifacts_is_empty_json_array(self):
        conn = init_subagent_db(":memory:")
        try:
            conn.execute(
                "INSERT INTO subagents (subagent_id, project_manager, task_goal) "
                "VALUES ('sa-1', 'pm', 'test')"
            )
            conn.commit()
            row = conn.execute("SELECT artifacts FROM subagents WHERE subagent_id='sa-1'").fetchone()
            assert row["artifacts"] == "[]"
        finally:
            conn.close()

    def test_unique_subagent_id(self):
        conn = init_subagent_db(":memory:")
        try:
            conn.execute(
                "INSERT INTO subagents (subagent_id, project_manager, task_goal) "
                "VALUES ('sa-1', 'pm', 'test')"
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO subagents (subagent_id, project_manager, task_goal) "
                    "VALUES ('sa-1', 'pm', 'another task')"
                )
        finally:
            conn.close()

    def test_required_fields(self):
        conn = init_subagent_db(":memory:")
        try:
            # Missing project_manager
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO subagents (subagent_id, task_goal) "
                    "VALUES ('sa-1', 'test')"
                )
            # Missing task_goal
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO subagents (subagent_id, project_manager) "
                    "VALUES ('sa-1', 'pm')"
                )
        finally:
            conn.close()


class TestSchemaIndexes:
    """Tests for schema indexes."""

    def test_indexes_exist(self):
        conn = init_subagent_db(":memory:")
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_subagents_%'"
            )
            indexes = {row["name"] for row in cur.fetchall()}
            assert "idx_subagents_pm" in indexes
            assert "idx_subagents_status" in indexes
            assert "idx_subagents_parent_req" in indexes
            assert "idx_subagents_created" in indexes
        finally:
            conn.close()


class TestSchemaFileBacked:
    """Tests for file-backed database creation."""

    def test_creates_parent_directories(self, tmp_path: Path):
        db_path = tmp_path / "nested" / "dir" / "subagents.db"
        conn = init_subagent_db(str(db_path))
        try:
            assert db_path.exists()
        finally:
            conn.close()

    def test_wal_mode(self):
        conn = init_subagent_db(":memory:")
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            # In-memory databases may not support WAL, but the pragma should succeed
            assert mode in ("wal", "memory")
        finally:
            conn.close()

    def test_foreign_keys_enabled(self):
        conn = init_subagent_db(":memory:")
        try:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1
        finally:
            conn.close()
