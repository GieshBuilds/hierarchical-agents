"""SQLite schema definition and database initialisation for the Subagent Registry."""

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

CREATE_SCHEMA_VERSION_TABLE: str = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_SUBAGENTS_TABLE: str = """\
CREATE TABLE IF NOT EXISTS subagents (
    subagent_id        TEXT PRIMARY KEY,
    project_manager    TEXT NOT NULL,
    task_goal          TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'running'
                           CHECK (status IN ('running', 'sleeping', 'completed', 'archived')),
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    conversation_path  TEXT,
    result_summary     TEXT,
    artifacts          TEXT DEFAULT '[]',
    token_cost         INTEGER NOT NULL DEFAULT 0,
    parent_request_id  TEXT
);
"""

CREATE_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_subagents_pm     ON subagents(project_manager);",
    "CREATE INDEX IF NOT EXISTS idx_subagents_status  ON subagents(status);",
    "CREATE INDEX IF NOT EXISTS idx_subagents_parent_req ON subagents(parent_request_id);",
    "CREATE INDEX IF NOT EXISTS idx_subagents_created ON subagents(created_at);",
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_subagent_db(db_path: str) -> sqlite3.Connection:
    """Initialise (or open) a SQLite database with the subagents schema.

    * Enables WAL journal mode for better concurrency.
    * Enables foreign-key enforcement.
    * Creates the ``subagents`` and ``schema_version`` tables if they do not
      already exist.
    * Records the current schema version when bootstrapping a new database.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite file, or ``":memory:"`` for an
        in-memory database.

    Returns
    -------
    sqlite3.Connection
        An open connection ready for use.
    """
    # Ensure parent directory exists for file-backed DBs.
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Pragmas — must be set before any DDL in the same connection.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Create tables and indexes.
    conn.execute(CREATE_SCHEMA_VERSION_TABLE)
    conn.execute(CREATE_SUBAGENTS_TABLE)
    for idx_sql in CREATE_INDEXES:
        conn.execute(idx_sql)

    # Record schema version if this is a fresh database.
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if row["v"] is None:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?);",
            (SCHEMA_VERSION,),
        )

    conn.commit()
    return conn
