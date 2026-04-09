#!/usr/bin/env python3
"""Migrate registry.db to support the specialist role.

SQLite cannot ALTER a CHECK constraint, so this script:
1. Creates a new profiles table with the updated constraint
2. Copies all existing data
3. Drops the old table
4. Renames the new table

Safe to run multiple times — skips if the constraint already includes 'specialist'.

Usage:
    python scripts/migrate_specialist_role.py [--db PATH]

Default DB: ~/.hermes/hierarchy/registry.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def needs_migration(conn: sqlite3.Connection) -> bool:
    """Check if the profiles table still has the old CHECK constraint."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='profiles'"
    ).fetchone()
    if row is None:
        print("No profiles table found — nothing to migrate.")
        return False
    schema_sql = row[0]
    if "'specialist'" in schema_sql:
        print("Schema already includes 'specialist' — no migration needed.")
        return False
    return True


def migrate(conn: sqlite3.Connection) -> None:
    """Recreate the profiles table with the updated CHECK constraint."""
    print("Migrating profiles table to add 'specialist' role...")

    conn.execute("PRAGMA foreign_keys = OFF;")

    conn.execute("""
        CREATE TABLE profiles_new (
            profile_name   TEXT PRIMARY KEY,
            display_name   TEXT NOT NULL,
            role           TEXT NOT NULL
                               CHECK (role IN ('ceo', 'department_head', 'project_manager', 'specialist')),
            parent_profile TEXT
                               REFERENCES profiles_new(profile_name)
                               ON DELETE RESTRICT,
            department     TEXT,
            status         TEXT NOT NULL DEFAULT 'active'
                               CHECK (status IN ('active', 'suspended', 'archived')),
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
            config_path    TEXT,
            description    TEXT
        );
    """)

    conn.execute("""
        INSERT INTO profiles_new
        SELECT * FROM profiles;
    """)

    # Count rows to verify
    old_count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    new_count = conn.execute("SELECT COUNT(*) FROM profiles_new").fetchone()[0]
    assert old_count == new_count, (
        f"Row count mismatch: old={old_count}, new={new_count}"
    )

    conn.execute("DROP TABLE profiles;")
    conn.execute("ALTER TABLE profiles_new RENAME TO profiles;")

    # Recreate indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_parent ON profiles(parent_profile);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_department ON profiles(department);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status);")

    conn.execute("PRAGMA foreign_keys = ON;")
    conn.commit()

    print(f"Migration complete. {new_count} profiles preserved.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate registry.db for specialist role")
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".hermes" / "hierarchy" / "registry.db"),
        help="Path to registry.db (default: ~/.hermes/hierarchy/registry.db)",
    )
    args = parser.parse_args()

    db_path = args.db
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        if needs_migration(conn):
            migrate(conn)
        return 0
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
