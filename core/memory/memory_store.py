"""Per-profile scoped memory store with CRUD, tier management, and budgets.

Thread-safe SQLite-backed storage for hierarchical agent memory entries.
Each MemoryStore instance is scoped to a single profile and its associated
MemoryScope, enforcing scope isolation and budget constraints.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from core.memory.exceptions import (
    InvalidTierTransition,
    MemoryEntryNotFound,
    MemoryStoreError,
)
from core.memory.models import (
    MemoryBudget,
    MemoryEntry,
    MemoryEntryType,
    MemoryScope,
    MemoryTier,
    TierTransition,
    generate_memory_id,
    generate_transition_id,
    is_valid_tier_transition,
)
from core.memory.schema import init_memory_db

logger = logging.getLogger(__name__)


# --- Helper functions ---


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_memory_entry(row: sqlite3.Row) -> MemoryEntry:
    """Convert a SQLite Row to a MemoryEntry dataclass."""
    metadata_raw = row["metadata"]
    if isinstance(metadata_raw, str):
        metadata = json.loads(metadata_raw)
    else:
        metadata = metadata_raw if metadata_raw is not None else {}

    return MemoryEntry(
        entry_id=row["entry_id"],
        profile_name=row["profile_name"],
        scope=MemoryScope(row["scope"]),
        tier=MemoryTier(row["tier"]),
        entry_type=MemoryEntryType(row["entry_type"]),
        content=row["content"],
        metadata=metadata,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        accessed_at=datetime.fromisoformat(row["accessed_at"]),
        expires_at=(
            datetime.fromisoformat(row["expires_at"])
            if row["expires_at"]
            else None
        ),
        byte_size=row["byte_size"] or 0,
    )


def _row_to_tier_transition(row: sqlite3.Row) -> TierTransition:
    """Convert a SQLite Row to a TierTransition dataclass."""
    return TierTransition(
        transition_id=row["transition_id"],
        entry_id=row["entry_id"],
        from_tier=MemoryTier(row["from_tier"]),
        to_tier=MemoryTier(row["to_tier"]),
        reason=row["reason"],
        transitioned_at=datetime.fromisoformat(row["transitioned_at"]),
    )


def _row_to_memory_budget(row: sqlite3.Row) -> MemoryBudget:
    """Convert a SQLite Row to a MemoryBudget dataclass."""
    tier_quotas_raw = row["tier_quotas"]
    if isinstance(tier_quotas_raw, str):
        tier_quotas = json.loads(tier_quotas_raw)
    else:
        tier_quotas = tier_quotas_raw if tier_quotas_raw is not None else {
            "hot": 200, "warm": 300, "cool": 300, "cold": 200,
        }

    return MemoryBudget(
        profile_name=row["profile_name"],
        max_entries=row["max_entries"],
        max_bytes=row["max_bytes"],
        tier_quotas=tier_quotas,
    )


# --- MemoryStore class ---


class MemoryStore:
    """Per-profile scoped memory store.

    Provides CRUD operations on memory entries, tier management,
    and budget enforcement for a single profile.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file, or ``':memory:'`` for testing.
    profile_name : str
        Name of the profile this store is scoped to.
    profile_scope : MemoryScope
        The memory scope for this profile (strategic, domain, project, task).
    """

    def __init__(
        self,
        db_path: str,
        profile_name: str,
        profile_scope: MemoryScope,
    ) -> None:
        self._db_path = db_path
        self._profile_name = profile_name
        self._profile_scope = profile_scope
        self._conn = init_memory_db(db_path)
        self._lock = threading.Lock()

    # --- Properties ---

    @property
    def profile_name(self) -> str:
        """The profile name this store is scoped to."""
        return self._profile_name

    @property
    def profile_scope(self) -> MemoryScope:
        """The memory scope for this profile."""
        return self._profile_scope

    @property
    def db_path(self) -> str:
        """The database file path."""
        return self._db_path

    # --- Connection management ---

    @contextmanager
    def _cursor(
        self,
        *,
        commit: bool = False,
    ) -> Iterator[sqlite3.Cursor]:
        """Yield a cursor under the store lock.

        Parameters
        ----------
        commit : bool
            If True, commit on successful exit. Rollback on exception.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                if commit:
                    self._conn.commit()
            except Exception:
                if commit:
                    self._conn.rollback()
                raise

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # --- Core CRUD operations ---

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        """Store a new memory entry.

        Validates that the entry scope matches this store's profile scope,
        auto-sets the profile_name and byte_size, and generates an entry_id
        if one is not already set.

        Parameters
        ----------
        entry : MemoryEntry
            The memory entry to store.

        Returns
        -------
        MemoryEntry
            The stored entry with all fields populated.

        Raises
        ------
        MemoryStoreError
            If the entry scope does not match the profile scope.
        """
        # Validate scope
        if entry.scope != self._profile_scope:
            raise MemoryStoreError(
                f"Entry scope {entry.scope.value!r} does not match "
                f"profile scope {self._profile_scope.value!r}"
            )

        # Auto-populate fields
        entry.profile_name = self._profile_name
        if not entry.entry_id:
            entry.entry_id = generate_memory_id()
        entry.byte_size = len(entry.content.encode("utf-8"))

        now = _now_iso()

        with self._cursor(commit=True) as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO memory_entries (
                        entry_id, profile_name, scope, tier, entry_type,
                        content, metadata, created_at, updated_at,
                        accessed_at, expires_at, byte_size
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.entry_id,
                        entry.profile_name,
                        entry.scope.value,
                        entry.tier.value,
                        entry.entry_type.value,
                        entry.content,
                        json.dumps(entry.metadata),
                        entry.created_at.isoformat(),
                        entry.updated_at.isoformat(),
                        now,
                        (
                            entry.expires_at.isoformat()
                            if entry.expires_at
                            else None
                        ),
                        entry.byte_size,
                    ),
                )
            except sqlite3.Error as exc:
                raise MemoryStoreError(
                    f"Failed to store memory entry: {exc}"
                ) from exc

        # Check budget (warn only, do not block)
        self._warn_if_budget_exceeded()

        return entry

    def get(self, entry_id: str) -> MemoryEntry:
        """Retrieve a memory entry by ID and update its accessed_at timestamp.

        Parameters
        ----------
        entry_id : str
            The entry ID to look up.

        Returns
        -------
        MemoryEntry
            The memory entry.

        Raises
        ------
        MemoryEntryNotFound
            If no entry with the given ID exists for this profile.
        """
        now = _now_iso()

        with self._cursor(commit=True) as cur:
            cur.execute(
                """
                SELECT * FROM memory_entries
                WHERE entry_id = ? AND profile_name = ?
                """,
                (entry_id, self._profile_name),
            )
            row = cur.fetchone()

            if row is None:
                raise MemoryEntryNotFound(entry_id)

            # Update accessed_at
            cur.execute(
                """
                UPDATE memory_entries SET accessed_at = ?
                WHERE entry_id = ?
                """,
                (now, entry_id),
            )

        entry = _row_to_memory_entry(row)
        entry.accessed_at = datetime.fromisoformat(now)
        return entry

    def search(
        self,
        query: str,
        scope: MemoryScope | None = None,
        entry_type: MemoryEntryType | None = None,
        tier: MemoryTier | None = None,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """Search memory entries by content.

        Uses SQL ``LIKE`` for substring matching on the content field.

        Parameters
        ----------
        query : str
            Substring to search for in entry content.
        scope : MemoryScope | None
            Optional scope filter.
        entry_type : MemoryEntryType | None
            Optional entry type filter.
        tier : MemoryTier | None
            Optional tier filter.
        limit : int
            Maximum number of results. Default: 50.

        Returns
        -------
        list[MemoryEntry]
            Matching entries ordered by accessed_at descending.
        """
        conditions = ["profile_name = ?", "content LIKE ?"]
        params: list = [self._profile_name, f"%{query}%"]

        if scope is not None:
            conditions.append("scope = ?")
            params.append(scope.value)

        if entry_type is not None:
            conditions.append("entry_type = ?")
            params.append(entry_type.value)

        if tier is not None:
            conditions.append("tier = ?")
            params.append(tier.value)

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT * FROM memory_entries
            WHERE {where_clause}
            ORDER BY accessed_at DESC
            LIMIT ?
        """
        params.append(limit)

        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [_row_to_memory_entry(row) for row in rows]

    def update(self, entry_id: str, **fields: object) -> MemoryEntry:
        """Update fields on an existing memory entry.

        Only the following fields may be updated: ``content``, ``metadata``,
        ``entry_type``, ``expires_at``.  ``updated_at`` is set automatically.
        If ``content`` is changed, ``byte_size`` is recalculated.

        Parameters
        ----------
        entry_id : str
            The entry to update.
        **fields
            Keyword arguments for allowed fields.

        Returns
        -------
        MemoryEntry
            The updated entry.

        Raises
        ------
        MemoryEntryNotFound
            If the entry does not exist.
        MemoryStoreError
            If an invalid field name is supplied.
        """
        allowed = {"content", "metadata", "entry_type", "expires_at"}
        invalid = set(fields) - allowed
        if invalid:
            raise MemoryStoreError(
                f"Cannot update disallowed fields: {', '.join(sorted(invalid))}"
            )

        # Verify entry exists (also scoped to this profile)
        existing = self.get(entry_id)  # noqa: F841  — validates existence

        set_clauses: list[str] = []
        params: list = []
        now = _now_iso()

        if "content" in fields:
            content = str(fields["content"])
            byte_size = len(content.encode("utf-8"))
            set_clauses.append("content = ?")
            params.append(content)
            set_clauses.append("byte_size = ?")
            params.append(byte_size)

        if "metadata" in fields:
            meta = fields["metadata"]
            set_clauses.append("metadata = ?")
            params.append(json.dumps(meta))

        if "entry_type" in fields:
            et = fields["entry_type"]
            value = et.value if isinstance(et, MemoryEntryType) else str(et)
            set_clauses.append("entry_type = ?")
            params.append(value)

        if "expires_at" in fields:
            ea = fields["expires_at"]
            if ea is None:
                set_clauses.append("expires_at = ?")
                params.append(None)
            elif isinstance(ea, datetime):
                set_clauses.append("expires_at = ?")
                params.append(ea.isoformat())
            else:
                set_clauses.append("expires_at = ?")
                params.append(str(ea))

        # Always update updated_at
        set_clauses.append("updated_at = ?")
        params.append(now)

        params.append(entry_id)

        set_sql = ", ".join(set_clauses)

        with self._cursor(commit=True) as cur:
            cur.execute(
                f"UPDATE memory_entries SET {set_sql} WHERE entry_id = ?",
                params,
            )

        return self.get(entry_id)

    def delete(self, entry_id: str) -> None:
        """Delete a memory entry.

        Parameters
        ----------
        entry_id : str
            The entry to delete.

        Raises
        ------
        MemoryEntryNotFound
            If no entry with the given ID exists for this profile.
        """
        with self._cursor(commit=True) as cur:
            cur.execute(
                """
                DELETE FROM memory_entries
                WHERE entry_id = ? AND profile_name = ?
                """,
                (entry_id, self._profile_name),
            )
            if cur.rowcount == 0:
                raise MemoryEntryNotFound(entry_id)

    def list_entries(
        self,
        tier: MemoryTier | None = None,
        scope: MemoryScope | None = None,
        entry_type: MemoryEntryType | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """List memory entries with optional filters and pagination.

        Parameters
        ----------
        tier : MemoryTier | None
            Optional tier filter.
        scope : MemoryScope | None
            Optional scope filter.
        entry_type : MemoryEntryType | None
            Optional entry type filter.
        offset : int
            Number of entries to skip. Default: 0.
        limit : int
            Maximum entries to return. Default: 50.

        Returns
        -------
        list[MemoryEntry]
            Matching entries ordered by created_at descending.
        """
        conditions = ["profile_name = ?"]
        params: list = [self._profile_name]

        if tier is not None:
            conditions.append("tier = ?")
            params.append(tier.value)

        if scope is not None:
            conditions.append("scope = ?")
            params.append(scope.value)

        if entry_type is not None:
            conditions.append("entry_type = ?")
            params.append(entry_type.value)

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT * FROM memory_entries
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [_row_to_memory_entry(row) for row in rows]

    # --- Statistics ---

    def get_stats(self) -> dict:
        """Get memory store statistics for this profile.

        Returns
        -------
        dict
            Statistics including total_entries, total_bytes, breakdowns
            by tier/type/scope, and current budget (if set).
        """
        stats: dict = {
            "total_entries": 0,
            "total_bytes": 0,
            "by_tier": {},
            "by_type": {},
            "by_scope": {},
            "budget": None,
        }

        with self._cursor() as cur:
            # Total entries and bytes
            cur.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(byte_size), 0)
                FROM memory_entries WHERE profile_name = ?
                """,
                (self._profile_name,),
            )
            row = cur.fetchone()
            stats["total_entries"] = row[0]
            stats["total_bytes"] = row[1]

            # By tier
            cur.execute(
                """
                SELECT tier, COUNT(*)
                FROM memory_entries WHERE profile_name = ?
                GROUP BY tier
                """,
                (self._profile_name,),
            )
            stats["by_tier"] = {r[0]: r[1] for r in cur.fetchall()}

            # By type
            cur.execute(
                """
                SELECT entry_type, COUNT(*)
                FROM memory_entries WHERE profile_name = ?
                GROUP BY entry_type
                """,
                (self._profile_name,),
            )
            stats["by_type"] = {r[0]: r[1] for r in cur.fetchall()}

            # By scope
            cur.execute(
                """
                SELECT scope, COUNT(*)
                FROM memory_entries WHERE profile_name = ?
                GROUP BY scope
                """,
                (self._profile_name,),
            )
            stats["by_scope"] = {r[0]: r[1] for r in cur.fetchall()}

        # Attach budget info
        budget = self.get_budget()
        if budget is not None:
            stats["budget"] = budget.to_dict()

        return stats

    # --- Tier management ---

    def transition_tier(
        self,
        entry_id: str,
        new_tier: MemoryTier,
        reason: str,
    ) -> TierTransition:
        """Transition a memory entry to a new storage tier.

        Parameters
        ----------
        entry_id : str
            The entry to transition.
        new_tier : MemoryTier
            The target tier.
        reason : str
            Human-readable reason for the transition.

        Returns
        -------
        TierTransition
            Record of the completed transition.

        Raises
        ------
        MemoryEntryNotFound
            If the entry does not exist.
        InvalidTierTransition
            If the transition is not allowed.
        """
        entry = self.get(entry_id)

        if not is_valid_tier_transition(entry.tier, new_tier):
            raise InvalidTierTransition(entry.tier.value, new_tier.value)

        now = _now_iso()
        transition_id = generate_transition_id()

        with self._cursor(commit=True) as cur:
            # Update the entry tier
            cur.execute(
                """
                UPDATE memory_entries SET tier = ?, updated_at = ?
                WHERE entry_id = ?
                """,
                (new_tier.value, now, entry_id),
            )

            # Record the transition
            cur.execute(
                """
                INSERT INTO tier_transitions (
                    transition_id, entry_id, from_tier, to_tier,
                    reason, transitioned_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    transition_id,
                    entry_id,
                    entry.tier.value,
                    new_tier.value,
                    reason,
                    now,
                ),
            )

        return TierTransition(
            transition_id=transition_id,
            entry_id=entry_id,
            from_tier=entry.tier,
            to_tier=new_tier,
            reason=reason,
            transitioned_at=datetime.fromisoformat(now),
        )

    def bulk_transition(
        self,
        entry_ids: list[str],
        new_tier: MemoryTier,
        reason: str,
    ) -> list[TierTransition]:
        """Transition multiple entries to a new tier in a single operation.

        All transitions are performed under a single lock acquisition.
        Individual entries that fail validation are skipped with a warning.

        Parameters
        ----------
        entry_ids : list[str]
            List of entry IDs to transition.
        new_tier : MemoryTier
            The target tier.
        reason : str
            Human-readable reason for the transitions.

        Returns
        -------
        list[TierTransition]
            Records of all successful transitions.
        """
        transitions: list[TierTransition] = []
        now = _now_iso()

        with self._lock:
            cur = self._conn.cursor()
            try:
                for entry_id in entry_ids:
                    # Fetch the entry
                    cur.execute(
                        """
                        SELECT * FROM memory_entries
                        WHERE entry_id = ? AND profile_name = ?
                        """,
                        (entry_id, self._profile_name),
                    )
                    row = cur.fetchone()
                    if row is None:
                        logger.warning(
                            "Bulk transition: entry %s not found, skipping",
                            entry_id,
                        )
                        continue

                    entry = _row_to_memory_entry(row)

                    if not is_valid_tier_transition(entry.tier, new_tier):
                        logger.warning(
                            "Bulk transition: invalid transition %s -> %s "
                            "for entry %s, skipping",
                            entry.tier.value,
                            new_tier.value,
                            entry_id,
                        )
                        continue

                    transition_id = generate_transition_id()

                    # Update entry tier
                    cur.execute(
                        """
                        UPDATE memory_entries SET tier = ?, updated_at = ?
                        WHERE entry_id = ?
                        """,
                        (new_tier.value, now, entry_id),
                    )

                    # Record transition
                    cur.execute(
                        """
                        INSERT INTO tier_transitions (
                            transition_id, entry_id, from_tier, to_tier,
                            reason, transitioned_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            transition_id,
                            entry_id,
                            entry.tier.value,
                            new_tier.value,
                            reason,
                            now,
                        ),
                    )

                    transitions.append(TierTransition(
                        transition_id=transition_id,
                        entry_id=entry_id,
                        from_tier=entry.tier,
                        to_tier=new_tier,
                        reason=reason,
                        transitioned_at=datetime.fromisoformat(now),
                    ))

                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        return transitions

    # --- Budget management ---

    def get_budget(self) -> MemoryBudget | None:
        """Get the memory budget for this profile.

        Returns
        -------
        MemoryBudget | None
            The budget if set, otherwise None.
        """
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM memory_budgets WHERE profile_name = ?",
                (self._profile_name,),
            )
            row = cur.fetchone()

        if row is None:
            return None

        return _row_to_memory_budget(row)

    def set_budget(self, budget: MemoryBudget) -> None:
        """Set or replace the memory budget for this profile.

        Parameters
        ----------
        budget : MemoryBudget
            The budget to set. The profile_name on the budget is
            overridden to match this store's profile.
        """
        budget.profile_name = self._profile_name

        with self._cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO memory_budgets (
                    profile_name, max_entries, max_bytes, tier_quotas
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    budget.profile_name,
                    budget.max_entries,
                    budget.max_bytes,
                    json.dumps(budget.tier_quotas),
                ),
            )

    def check_budget(self) -> dict:
        """Check current usage against the budget.

        Returns
        -------
        dict
            Dictionary with ``exceeded`` (bool), ``usage``, ``limits``,
            and ``tier_usage`` information.
        """
        budget = self.get_budget()

        with self._cursor() as cur:
            # Current usage
            cur.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(byte_size), 0)
                FROM memory_entries WHERE profile_name = ?
                """,
                (self._profile_name,),
            )
            row = cur.fetchone()
            entry_count = row[0]
            total_bytes = row[1]

            # Tier usage
            cur.execute(
                """
                SELECT tier, COUNT(*)
                FROM memory_entries WHERE profile_name = ?
                GROUP BY tier
                """,
                (self._profile_name,),
            )
            tier_usage = {r[0]: r[1] for r in cur.fetchall()}

        # If no budget is set, report usage but never exceeded
        if budget is None:
            return {
                "exceeded": False,
                "usage": {"entries": entry_count, "bytes": total_bytes},
                "limits": {"max_entries": None, "max_bytes": None},
                "tier_usage": tier_usage,
            }

        exceeded = (
            entry_count > budget.max_entries
            or total_bytes > budget.max_bytes
        )

        return {
            "exceeded": exceeded,
            "usage": {"entries": entry_count, "bytes": total_bytes},
            "limits": {
                "max_entries": budget.max_entries,
                "max_bytes": budget.max_bytes,
            },
            "tier_usage": tier_usage,
        }

    # --- Internal helpers ---

    def _warn_if_budget_exceeded(self) -> None:
        """Log a warning if the current budget is exceeded.

        This is a soft check — it never raises or blocks operations.
        """
        try:
            status = self.check_budget()
            if status["exceeded"]:
                usage = status["usage"]
                limits = status["limits"]
                logger.warning(
                    "Memory budget exceeded for profile %r: "
                    "entries=%d/%s, bytes=%d/%s",
                    self._profile_name,
                    usage["entries"],
                    limits["max_entries"],
                    usage["bytes"],
                    limits["max_bytes"],
                )
        except Exception:
            # Budget check should never break store operations
            logger.debug(
                "Could not check budget for profile %r",
                self._profile_name,
                exc_info=True,
            )


# --- Public API ---

__all__ = [
    "MemoryStore",
    "_row_to_memory_entry",
]
