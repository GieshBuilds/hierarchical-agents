"""Database schema and initialization for the Scoped Memory system.

SQLite-backed storage for hierarchical agent memory with tiered lifecycle
management. Each profile maintains scoped memory entries (strategic, domain,
project, task) with automatic tier transitions (hot → warm → cool → cold).

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Schema version — bump when the schema changes
# ---------------------------------------------------------------------------
SCHEMA_VERSION: int = 1

# ---------------------------------------------------------------------------
# DDL statements
# ---------------------------------------------------------------------------

CREATE_MEMORY_ENTRIES_TABLE: str = """\
CREATE TABLE IF NOT EXISTS memory_entries (
    entry_id     TEXT PRIMARY KEY,
    profile_name TEXT NOT NULL,
    scope        TEXT NOT NULL CHECK (
        scope IN ('strategic', 'domain', 'project', 'task')
    ),
    tier         TEXT NOT NULL DEFAULT 'hot' CHECK (
        tier IN ('hot', 'warm', 'cool', 'cold')
    ),
    entry_type   TEXT NOT NULL CHECK (
        entry_type IN ('preference', 'decision', 'learning',
                       'context', 'summary', 'artifact')
    ),
    content      TEXT NOT NULL,
    metadata     TEXT DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    accessed_at  TEXT NOT NULL,
    expires_at   TEXT,
    byte_size    INTEGER DEFAULT 0
);
"""

CREATE_KNOWLEDGE_BASE_TABLE: str = """\
CREATE TABLE IF NOT EXISTS knowledge_base (
    entry_id       TEXT PRIMARY KEY,
    profile_name   TEXT NOT NULL,
    category       TEXT NOT NULL,
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,
    source_profile TEXT DEFAULT '',
    source_context TEXT DEFAULT '',
    tags           TEXT DEFAULT '[]',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
"""

CREATE_TIER_TRANSITIONS_TABLE: str = """\
CREATE TABLE IF NOT EXISTS tier_transitions (
    transition_id   TEXT PRIMARY KEY,
    entry_id        TEXT NOT NULL,
    from_tier       TEXT NOT NULL CHECK (
        from_tier IN ('hot', 'warm', 'cool', 'cold')
    ),
    to_tier         TEXT NOT NULL CHECK (
        to_tier IN ('hot', 'warm', 'cool', 'cold')
    ),
    reason          TEXT NOT NULL,
    transitioned_at TEXT NOT NULL
);
"""

CREATE_MEMORY_BUDGETS_TABLE: str = """\
CREATE TABLE IF NOT EXISTS memory_budgets (
    profile_name TEXT PRIMARY KEY,
    max_entries  INTEGER DEFAULT 1000,
    max_bytes    INTEGER DEFAULT 10485760,
    tier_quotas  TEXT DEFAULT '{"hot": 200, "warm": 300, "cool": 300, "cold": 200}'
);
"""

CREATE_SCHEMA_VERSION_TABLE: str = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at TEXT    NOT NULL
);
"""

# Indexes for efficient queries
CREATE_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_memory_entries_profile  ON memory_entries(profile_name);",
    "CREATE INDEX IF NOT EXISTS idx_memory_entries_tier     ON memory_entries(tier);",
    "CREATE INDEX IF NOT EXISTS idx_memory_entries_scope    ON memory_entries(scope);",
    "CREATE INDEX IF NOT EXISTS idx_memory_entries_type     ON memory_entries(entry_type);",
    "CREATE INDEX IF NOT EXISTS idx_memory_entries_accessed ON memory_entries(accessed_at);",
    "CREATE INDEX IF NOT EXISTS idx_memory_entries_expires  ON memory_entries(expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_base_profile  ON knowledge_base(profile_name);",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_base_category ON knowledge_base(category);",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_base_source   ON knowledge_base(source_profile);",
    "CREATE INDEX IF NOT EXISTS idx_tier_transitions_entry  ON tier_transitions(entry_id);",
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_memory_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Initialize the Scoped Memory database.

    Creates the memory_entries, knowledge_base, tier_transitions,
    memory_budgets, and schema_version tables plus all indexes.
    Uses WAL mode for file-backed databases.

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

    # Pragmas — must be set before any DDL in the same connection.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Create tables
    conn.execute(CREATE_MEMORY_ENTRIES_TABLE)
    conn.execute(CREATE_KNOWLEDGE_BASE_TABLE)
    conn.execute(CREATE_TIER_TRANSITIONS_TABLE)
    conn.execute(CREATE_MEMORY_BUDGETS_TABLE)
    conn.execute(CREATE_SCHEMA_VERSION_TABLE)

    # Create indexes
    for index_sql in CREATE_INDEXES:
        conn.execute(index_sql)

    # Record schema version if this is a fresh database.
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if row["v"] is None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
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


__all__ = [
    "SCHEMA_VERSION",
    "CREATE_MEMORY_ENTRIES_TABLE",
    "CREATE_KNOWLEDGE_BASE_TABLE",
    "CREATE_TIER_TRANSITIONS_TABLE",
    "CREATE_MEMORY_BUDGETS_TABLE",
    "CREATE_SCHEMA_VERSION_TABLE",
    "CREATE_INDEXES",
    "init_memory_db",
    "get_schema_version",
]
