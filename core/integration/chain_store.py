"""Persistent SQLite storage for delegation chains.

Stores DelegationChain and DelegationHop state so chains survive
process restarts. Uses the same patterns as the workers schema module.

Stdlib only -- no external dependencies.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from .delegation import (
    ChainStatus,
    DelegationChain,
    DelegationHop,
    HopStatus,
)
from .exceptions import ChainNotFound

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1

_CREATE_SCHEMA_VERSION = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_CHAINS = """\
CREATE TABLE IF NOT EXISTS chains (
    chain_id         TEXT PRIMARY KEY,
    task_description TEXT NOT NULL,
    originator       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','active','completed','failed','expired')),
    workers          TEXT NOT NULL DEFAULT '[]',
    worker_results   TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    completed_at     TEXT
);
"""

_CREATE_HOPS = """\
CREATE TABLE IF NOT EXISTS hops (
    rowid_pk       INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id       TEXT NOT NULL REFERENCES chains(chain_id) ON DELETE CASCADE,
    hop_index      INTEGER NOT NULL,
    from_profile   TEXT NOT NULL,
    to_profile     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','delegated','working','completed','failed')),
    message_id     TEXT,
    delegated_at   TEXT,
    completed_at   TEXT,
    UNIQUE (chain_id, hop_index)
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_chains_status ON chains(status);",
    "CREATE INDEX IF NOT EXISTS idx_chains_originator ON chains(originator);",
    "CREATE INDEX IF NOT EXISTS idx_hops_chain ON hops(chain_id);",
]


def _init_chain_db(db_path: str) -> sqlite3.Connection:
    """Initialise or open a chain store database."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.execute(_CREATE_SCHEMA_VERSION)
    conn.execute(_CREATE_CHAINS)
    conn.execute(_CREATE_HOPS)
    for idx in _CREATE_INDEXES:
        conn.execute(idx)

    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if row["v"] is None:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?);",
            (SCHEMA_VERSION,),
        )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_chain(chain_row: sqlite3.Row, hop_rows: list[sqlite3.Row]) -> DelegationChain:
    """Reconstruct a DelegationChain from DB rows."""
    hops = []
    for hr in hop_rows:
        hops.append(DelegationHop(
            from_profile=hr["from_profile"],
            to_profile=hr["to_profile"],
            status=HopStatus(hr["status"]),
            message_id=hr["message_id"],
            delegated_at=(
                datetime.fromisoformat(hr["delegated_at"])
                if hr["delegated_at"] else None
            ),
            completed_at=(
                datetime.fromisoformat(hr["completed_at"])
                if hr["completed_at"] else None
            ),
        ))

    return DelegationChain(
        chain_id=chain_row["chain_id"],
        task_description=chain_row["task_description"],
        originator=chain_row["originator"],
        status=ChainStatus(chain_row["status"]),
        hops=hops,
        workers=json.loads(chain_row["workers"]),
        worker_results=json.loads(chain_row["worker_results"]),
        created_at=datetime.fromisoformat(chain_row["created_at"]),
        completed_at=(
            datetime.fromisoformat(chain_row["completed_at"])
            if chain_row["completed_at"] else None
        ),
    )


# ---------------------------------------------------------------------------
# ChainStore
# ---------------------------------------------------------------------------

class ChainStore:
    """Persistent SQLite store for delegation chains.

    Thread-safe. Uses the same cursor/lock pattern as SubagentRegistry.

    Parameters
    ----------
    db_path : str
        Path to the SQLite file, or ``":memory:"`` for tests.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = _init_chain_db(db_path)
        self._lock = threading.Lock()

    @contextmanager
    def _cursor(self, *, commit: bool = False) -> Generator[sqlite3.Cursor, None, None]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                if commit:
                    self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Save / update
    # ------------------------------------------------------------------

    def save(self, chain: DelegationChain) -> None:
        """Insert or replace a full chain (including hops) in the store."""
        with self._cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO chains
                    (chain_id, task_description, originator, status,
                     workers, worker_results, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chain.chain_id,
                    chain.task_description,
                    chain.originator,
                    chain.status.value,
                    json.dumps(chain.workers),
                    json.dumps(chain.worker_results),
                    chain.created_at.isoformat(),
                    chain.completed_at.isoformat() if chain.completed_at else None,
                ),
            )
            # Replace hops: delete old, insert current
            cur.execute("DELETE FROM hops WHERE chain_id = ?", (chain.chain_id,))
            for idx, hop in enumerate(chain.hops):
                cur.execute(
                    """
                    INSERT INTO hops
                        (chain_id, hop_index, from_profile, to_profile,
                         status, message_id, delegated_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chain.chain_id,
                        idx,
                        hop.from_profile,
                        hop.to_profile,
                        hop.status.value,
                        hop.message_id,
                        hop.delegated_at.isoformat() if hop.delegated_at else None,
                        hop.completed_at.isoformat() if hop.completed_at else None,
                    ),
                )

    def get(self, chain_id: str) -> DelegationChain:
        """Retrieve a chain by ID.

        Raises
        ------
        ChainNotFound
            If no chain with that ID exists.
        """
        with self._cursor() as cur:
            cur.execute("SELECT * FROM chains WHERE chain_id = ?", (chain_id,))
            chain_row = cur.fetchone()
            if chain_row is None:
                raise ChainNotFound(chain_id)

            cur.execute(
                "SELECT * FROM hops WHERE chain_id = ? ORDER BY hop_index",
                (chain_id,),
            )
            hop_rows = cur.fetchall()

        return _row_to_chain(chain_row, hop_rows)

    def list(
        self,
        *,
        status: Optional[ChainStatus] = None,
        originator: Optional[str] = None,
        limit: int = 100,
    ) -> list[DelegationChain]:
        """List chains with optional filters."""
        conditions: list[str] = []
        params: list[str | int] = []

        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if originator is not None:
            conditions.append("originator = ?")
            params.append(originator)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        with self._cursor() as cur:
            cur.execute(
                f"SELECT * FROM chains {where} ORDER BY created_at DESC LIMIT ?",
                params,
            )
            chain_rows = cur.fetchall()

            chains = []
            for cr in chain_rows:
                cur.execute(
                    "SELECT * FROM hops WHERE chain_id = ? ORDER BY hop_index",
                    (cr["chain_id"],),
                )
                hop_rows = cur.fetchall()
                chains.append(_row_to_chain(cr, hop_rows))

        return chains

    def delete(self, chain_id: str) -> None:
        """Delete a chain and its hops."""
        with self._cursor(commit=True) as cur:
            cur.execute("DELETE FROM hops WHERE chain_id = ?", (chain_id,))
            cur.execute("DELETE FROM chains WHERE chain_id = ?", (chain_id,))

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
