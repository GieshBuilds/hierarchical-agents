"""SQLite schema definition and database initialisation for the Profile Registry."""

from __future__ import annotations

import sqlite3

# ---------------------------------------------------------------------------
# Schema version — bump when the schema changes
# ---------------------------------------------------------------------------
SCHEMA_VERSION: int = 3

# ---------------------------------------------------------------------------
# DDL statements
# ---------------------------------------------------------------------------

CREATE_SCHEMA_VERSION_TABLE: str = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version   INTEGER NOT NULL,
    applied_at TEXT   NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_PROFILES_TABLE: str = """\
CREATE TABLE IF NOT EXISTS profiles (
    profile_name   TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    role           TEXT NOT NULL
                       CHECK (role IN ('ceo', 'department_head', 'project_manager', 'specialist')),
    parent_profile TEXT
                       REFERENCES profiles(profile_name)
                       ON DELETE RESTRICT,
    department     TEXT,
    status         TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('onboarding', 'active', 'suspended', 'archived')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    config_path    TEXT,
    description    TEXT
);
"""

CREATE_ONBOARDING_BRIEFS_TABLE: str = """\
CREATE TABLE IF NOT EXISTS onboarding_briefs (
    profile_name      TEXT PRIMARY KEY
                          REFERENCES profiles(profile_name)
                          ON DELETE CASCADE,
    parent_pm         TEXT NOT NULL,
    role_definition   TEXT NOT NULL,
    scope             TEXT NOT NULL,
    success_criteria  TEXT NOT NULL,
    handoff_protocol  TEXT NOT NULL,
    discovery_answers TEXT NOT NULL DEFAULT '',
    dependencies      TEXT NOT NULL DEFAULT '',
    first_task        TEXT NOT NULL DEFAULT '',
    submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
    extra_json        TEXT NOT NULL DEFAULT '{}'
);
"""

CREATE_ONBOARDING_STATE_TABLE: str = """\
CREATE TABLE IF NOT EXISTS onboarding_state (
    profile_name            TEXT PRIMARY KEY
                                REFERENCES profiles(profile_name)
                                ON DELETE CASCADE,
    owner_profile           TEXT NOT NULL,
    discovery_completed_at  TEXT,
    brief_completed_at      TEXT,
    plan_required           INTEGER NOT NULL DEFAULT 1,
    plan_completed_at       TEXT,
    plan_summary            TEXT NOT NULL DEFAULT '',
    plan_path               TEXT NOT NULL DEFAULT '',
    activation_ready        INTEGER NOT NULL DEFAULT 0,
    activated_at            TEXT,
    notes_json              TEXT NOT NULL DEFAULT '{}'
);
"""

CREATE_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_profiles_parent     ON profiles(parent_profile);",
    "CREATE INDEX IF NOT EXISTS idx_profiles_department  ON profiles(department);",
    "CREATE INDEX IF NOT EXISTS idx_profiles_status      ON profiles(status);",
    "CREATE INDEX IF NOT EXISTS idx_onboarding_parent_pm ON onboarding_briefs(parent_pm);",
    "CREATE INDEX IF NOT EXISTS idx_onboarding_owner     ON onboarding_state(owner_profile);",
    "CREATE INDEX IF NOT EXISTS idx_onboarding_ready     ON onboarding_state(activation_ready);",
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialise (or open) a SQLite database with the profiles schema.

    * Enables WAL journal mode for better concurrency.
    * Enables foreign-key enforcement.
    * Creates the ``profiles`` and ``schema_version`` tables if they do not
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
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Pragmas — must be set before any DDL in the same connection.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Create tables and indexes.
    conn.execute(CREATE_SCHEMA_VERSION_TABLE)
    conn.execute(CREATE_PROFILES_TABLE)
    conn.execute(CREATE_ONBOARDING_BRIEFS_TABLE)
    conn.execute(CREATE_ONBOARDING_STATE_TABLE)
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
