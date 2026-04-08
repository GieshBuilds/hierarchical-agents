"""Database schema and initialization for the IPC message bus.

SQLite-backed storage for inter-profile communication messages.
Single shared bus.db (unlike per-PM subagent databases).

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


# Current schema version
SCHEMA_VERSION = 1

# SQL for creating the messages table
CREATE_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    from_profile TEXT NOT NULL,
    to_profile TEXT NOT NULL,
    message_type TEXT NOT NULL CHECK (
        message_type IN ('task_request', 'task_response', 'status_query',
                         'status_response', 'broadcast', 'escalation')
    ),
    payload TEXT NOT NULL DEFAULT '{}',
    correlation_id TEXT,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (
        priority IN ('low', 'normal', 'urgent')
    ),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'delivered', 'read', 'expired')
    ),
    created_at TEXT NOT NULL,
    expires_at TEXT
);
"""

# SQL for creating the message archive table (same schema)
CREATE_MESSAGE_ARCHIVE_TABLE = """
CREATE TABLE IF NOT EXISTS message_archive (
    message_id TEXT PRIMARY KEY,
    from_profile TEXT NOT NULL,
    to_profile TEXT NOT NULL,
    message_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    correlation_id TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    archived_at TEXT NOT NULL
);
"""

# Schema version tracking
CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

# Indexes for efficient queries
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_messages_to_profile_status ON messages(to_profile, status);",
    "CREATE INDEX IF NOT EXISTS idx_messages_correlation_id ON messages(correlation_id);",
    "CREATE INDEX IF NOT EXISTS idx_messages_priority_created ON messages(priority, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_messages_expires_at ON messages(expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_messages_from_profile ON messages(from_profile);",
    "CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);",
    "CREATE INDEX IF NOT EXISTS idx_archive_correlation_id ON message_archive(correlation_id);",
]


def init_ipc_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Initialize the IPC message bus database.

    Creates the messages table, archive table, indexes, and schema version
    tracking. Uses WAL mode for file-backed databases.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file, or ':memory:' for in-memory.

    Returns
    -------
    sqlite3.Connection
        Initialized database connection.
    """
    # Ensure parent directory exists for file-backed databases
    if db_path != ":memory:":
        parent = Path(db_path).parent
        parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Create tables
    conn.execute(CREATE_MESSAGES_TABLE)
    conn.execute(CREATE_MESSAGE_ARCHIVE_TABLE)
    conn.execute(CREATE_SCHEMA_VERSION_TABLE)

    # Create indexes
    for index_sql in CREATE_INDEXES:
        conn.execute(index_sql)

    # Record schema version
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, now),
    )
    conn.commit()

    return conn


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from the database.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database connection.

    Returns
    -------
    int
        Current schema version, or 0 if no version recorded.
    """
    try:
        cursor = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0
