"""Tests for IPC database schema and initialization."""
from __future__ import annotations

import os
import sqlite3
import pytest
from core.ipc.schema import (
    init_ipc_db,
    get_schema_version,
    SCHEMA_VERSION,
    CREATE_INDEXES,
)


class TestInitIpcDb:
    def test_creates_messages_table(self):
        conn = init_ipc_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_creates_archive_table(self):
        conn = init_ipc_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_archive'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_creates_schema_version_table(self):
        conn = init_ipc_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_creates_indexes(self):
        conn = init_ipc_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_messages_to_profile_status" in indexes
        assert "idx_messages_correlation_id" in indexes
        assert "idx_messages_priority_created" in indexes
        assert "idx_messages_expires_at" in indexes
        assert "idx_messages_from_profile" in indexes
        assert "idx_messages_status" in indexes
        assert "idx_archive_correlation_id" in indexes
        conn.close()

    def test_sets_wal_mode_for_file_db(self, bus_db_path):
        conn = init_ipc_db(bus_db_path)
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_row_factory_set(self):
        conn = init_ipc_db()
        assert conn.row_factory == sqlite3.Row
        conn.close()

    def test_creates_parent_directory(self, temp_dir):
        db_path = os.path.join(temp_dir, "subdir", "nested", "bus.db")
        conn = init_ipc_db(db_path)
        assert os.path.exists(os.path.dirname(db_path))
        conn.close()

    def test_idempotent(self):
        conn = init_ipc_db()
        # Call again - should not fail
        conn2 = init_ipc_db()
        conn.close()
        conn2.close()

    def test_file_backed_db(self, bus_db_path):
        conn = init_ipc_db(bus_db_path)
        assert os.path.exists(bus_db_path)
        conn.close()

    def test_messages_table_columns(self):
        conn = init_ipc_db()
        cursor = conn.execute("PRAGMA table_info(messages)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "message_id", "from_profile", "to_profile", "message_type",
            "payload", "correlation_id", "priority", "status",
            "created_at", "expires_at"
        }
        assert columns == expected
        conn.close()

    def test_archive_table_columns(self):
        conn = init_ipc_db()
        cursor = conn.execute("PRAGMA table_info(message_archive)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "message_id", "from_profile", "to_profile", "message_type",
            "payload", "correlation_id", "priority", "status",
            "created_at", "expires_at", "archived_at"
        }
        assert columns == expected
        conn.close()

    def test_message_type_check_constraint(self):
        conn = init_ipc_db()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO messages (message_id, from_profile, to_profile, "
                "message_type, created_at) VALUES (?, ?, ?, ?, ?)",
                ("msg-test", "ceo", "cto", "invalid_type", "2025-01-01T00:00:00+00:00"),
            )
        conn.close()

    def test_priority_check_constraint(self):
        conn = init_ipc_db()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO messages (message_id, from_profile, to_profile, "
                "message_type, priority, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("msg-test", "ceo", "cto", "task_request", "critical", "2025-01-01T00:00:00+00:00"),
            )
        conn.close()

    def test_status_check_constraint(self):
        conn = init_ipc_db()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO messages (message_id, from_profile, to_profile, "
                "message_type, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("msg-test", "ceo", "cto", "task_request", "unknown", "2025-01-01T00:00:00+00:00"),
            )
        conn.close()


class TestGetSchemaVersion:
    def test_returns_version(self):
        conn = init_ipc_db()
        version = get_schema_version(conn)
        assert version == SCHEMA_VERSION
        conn.close()

    def test_returns_zero_for_empty_db(self):
        conn = sqlite3.connect(":memory:")
        version = get_schema_version(conn)
        assert version == 0
        conn.close()
