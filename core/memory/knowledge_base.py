"""Knowledge Base — structured knowledge storage and retrieval.

Cross-profile knowledge sharing backed by SQLite. Provides categorised
knowledge entries with tag-based filtering, text search, and pattern-based
learning extraction.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from core.memory.exceptions import KnowledgeEntryNotFound
from core.memory.models import KnowledgeEntry, generate_knowledge_id
from core.memory.schema import init_memory_db


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_knowledge_entry(row: sqlite3.Row) -> KnowledgeEntry:
    """Convert a SQLite Row to a KnowledgeEntry dataclass.

    Parameters
    ----------
    row : sqlite3.Row
        A row from the ``knowledge_base`` table.

    Returns
    -------
    KnowledgeEntry
        The deserialized knowledge entry.
    """
    tags_raw = row["tags"]
    if isinstance(tags_raw, str):
        try:
            tags = json.loads(tags_raw)
        except (json.JSONDecodeError, TypeError):
            tags = []
    else:
        tags = list(tags_raw) if tags_raw else []

    return KnowledgeEntry(
        entry_id=row["entry_id"],
        profile_name=row["profile_name"],
        category=row["category"],
        title=row["title"],
        content=row["content"],
        source_profile=row["source_profile"] or "",
        source_context=row["source_context"] or "",
        tags=tags,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


# ---------------------------------------------------------------------------
# Pattern matchers for extract_learnings
# ---------------------------------------------------------------------------

_LEARNING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\bdecided\s+to\b"),
    re.compile(r"(?i)\blearned\s+that\b"),
    re.compile(r"(?i)\bkey\s+finding\s*:"),
    re.compile(r"(?i)\boutcome\s*:"),
    re.compile(r"(?i)\bimportant\s*:"),
    re.compile(r"(?i)\blesson\s*:"),
    re.compile(r"(?i)\btakeaway\s*:"),
    re.compile(r"(?i)\binsight\s*:"),
    re.compile(r"(?i)\bconclusion\s*:"),
    re.compile(r"(?i)\bnote\s*:"),
    re.compile(r"(?i)\bfound\s+that\b"),
    re.compile(r"(?i)\bresult\s*:"),
]

_SENTENCE_SPLITTER = re.compile(r"(?<=[.!?])\s+|\n+")


class KnowledgeBase:
    """Structured knowledge storage for cross-profile knowledge sharing.

    Backed by the ``knowledge_base`` table in the shared memory SQLite
    database.  Thread-safe via an internal lock.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file, or ``':memory:'`` for testing.
    profile_name : str
        The owning profile name for all operations.
    """

    def __init__(self, db_path: str, profile_name: str) -> None:
        self._db_path = db_path
        self._profile_name = profile_name
        self._conn = init_memory_db(db_path)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def profile_name(self) -> str:
        """The owning profile name."""
        return self._profile_name

    @property
    def db_path(self) -> str:
        """Path to the backing database."""
        return self._db_path

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @contextmanager
    def _cursor(
        self,
        *,
        commit: bool = False,
    ) -> Generator[sqlite3.Cursor, None, None]:
        """Yield a cursor under the knowledge base lock.

        Parameters
        ----------
        commit : bool
            If True, commit on successful exit.  Rollback on exception.
        """
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
                if commit:
                    self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def add_knowledge(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        """Store a knowledge entry.

        Automatically sets ``profile_name`` to the instance's profile and
        generates an ``entry_id`` if the entry does not already have one.

        Parameters
        ----------
        entry : KnowledgeEntry
            The knowledge entry to store.

        Returns
        -------
        KnowledgeEntry
            The stored entry (with generated id / profile set).
        """
        entry.profile_name = self._profile_name

        if not entry.entry_id:
            entry.entry_id = generate_knowledge_id()

        now = datetime.now(timezone.utc)
        entry.created_at = now
        entry.updated_at = now

        tags_json = json.dumps(entry.tags)

        with self._cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO knowledge_base (
                    entry_id, profile_name, category, title, content,
                    source_profile, source_context, tags,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    entry.profile_name,
                    entry.category,
                    entry.title,
                    entry.content,
                    entry.source_profile,
                    entry.source_context,
                    tags_json,
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                ),
            )

        return entry

    def get_knowledge(self, entry_id: str) -> KnowledgeEntry:
        """Retrieve a single knowledge entry by ID.

        Parameters
        ----------
        entry_id : str
            The knowledge entry ID.

        Returns
        -------
        KnowledgeEntry
            The knowledge entry.

        Raises
        ------
        KnowledgeEntryNotFound
            If no entry with the given ID exists.
        """
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM knowledge_base WHERE entry_id = ?",
                (entry_id,),
            )
            row = cursor.fetchone()

        if row is None:
            raise KnowledgeEntryNotFound(entry_id)

        return _row_to_knowledge_entry(row)

    def search_knowledge(
        self,
        query: str,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[KnowledgeEntry]:
        """Search knowledge entries by text, category, and tags.

        Performs a case-insensitive ``LIKE`` search across ``title`` and
        ``content`` columns.

        Parameters
        ----------
        query : str
            Search text (matched with ``%query%``).
        category : str | None
            Optional category filter.
        tags : list[str] | None
            Optional tag filter — entries must contain **all** listed tags.
        limit : int
            Maximum results to return.  Default: 20.

        Returns
        -------
        list[KnowledgeEntry]
            Matching entries ordered by ``updated_at`` descending.
        """
        conditions: list[str] = ["profile_name = ?"]
        params: list[str | int] = [self._profile_name]

        # Text search across title and content
        conditions.append("(title LIKE ? OR content LIKE ?)")
        like_pattern = f"%{query}%"
        params.extend([like_pattern, like_pattern])

        if category is not None:
            conditions.append("category = ?")
            params.append(category)

        if tags:
            for tag in tags:
                # Match tag within JSON array using LIKE on the serialised
                # tags column.  This handles the common case of simple tag
                # strings without special JSON characters.
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT * FROM knowledge_base
            WHERE {where_clause}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(limit)

        with self._cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        return [_row_to_knowledge_entry(row) for row in rows]

    def search_all_profiles(
        self,
        query: str,
        category: str | None = None,
        tags: list[str] | None = None,
        source_profile: str | None = None,
        limit: int = 20,
    ) -> list[KnowledgeEntry]:
        """Search knowledge entries across all profiles in the shared database.

        Unlike :meth:`search_knowledge`, this method does **not** filter by
        ``profile_name``, returning entries contributed by any agent.  Use
        this for cross-profile knowledge discovery.

        Parameters
        ----------
        query : str
            Search text (matched with ``%query%`` across title and content).
            Pass an empty string to match all entries.
        category : str | None
            Optional category filter.
        tags : list[str] | None
            Optional tag filter — entries must contain **all** listed tags.
        source_profile : str | None
            Optional filter by the profile that contributed the entry.
        limit : int
            Maximum results to return.  Default: 20.

        Returns
        -------
        list[KnowledgeEntry]
            Matching entries ordered by ``updated_at`` descending.
        """
        conditions: list[str] = []
        params: list[str | int] = []

        if query:
            conditions.append("(title LIKE ? OR content LIKE ?)")
            like_pattern = f"%{query}%"
            params.extend([like_pattern, like_pattern])

        if category is not None:
            conditions.append("category = ?")
            params.append(category)

        if source_profile is not None:
            conditions.append("source_profile = ?")
            params.append(source_profile)

        if tags:
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        sql = f"""
            SELECT * FROM knowledge_base
            WHERE {where_clause}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(limit)

        with self._cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        return [_row_to_knowledge_entry(row) for row in rows]

    def update_knowledge(self, entry_id: str, **fields: object) -> KnowledgeEntry:
        """Update fields on an existing knowledge entry.

        Parameters
        ----------
        entry_id : str
            The entry to update.
        **fields
            Allowed keys: ``title``, ``content``, ``category``, ``tags``,
            ``source_profile``, ``source_context``.

        Returns
        -------
        KnowledgeEntry
            The updated knowledge entry.

        Raises
        ------
        KnowledgeEntryNotFound
            If no entry with the given ID exists.
        ValueError
            If an unsupported field is supplied.
        """
        allowed = {
            "title",
            "content",
            "category",
            "tags",
            "source_profile",
            "source_context",
        }
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ValueError(f"Invalid field(s): {', '.join(sorted(invalid))}")

        # Ensure the entry exists
        self.get_knowledge(entry_id)

        if not fields:
            return self.get_knowledge(entry_id)

        set_clauses: list[str] = []
        params: list[object] = []

        for key, value in fields.items():
            if key == "tags":
                set_clauses.append("tags = ?")
                params.append(json.dumps(value))
            else:
                set_clauses.append(f"{key} = ?")
                params.append(value)

        # Always bump updated_at
        now = _now_iso()
        set_clauses.append("updated_at = ?")
        params.append(now)

        params.append(entry_id)
        set_sql = ", ".join(set_clauses)

        with self._cursor(commit=True) as cursor:
            cursor.execute(
                f"UPDATE knowledge_base SET {set_sql} WHERE entry_id = ?",
                params,
            )

        return self.get_knowledge(entry_id)

    def delete_knowledge(self, entry_id: str) -> None:
        """Delete a knowledge entry.

        Parameters
        ----------
        entry_id : str
            The entry to delete.

        Raises
        ------
        KnowledgeEntryNotFound
            If no entry with the given ID exists.
        """
        with self._cursor(commit=True) as cursor:
            cursor.execute(
                "DELETE FROM knowledge_base WHERE entry_id = ?",
                (entry_id,),
            )
            if cursor.rowcount == 0:
                raise KnowledgeEntryNotFound(entry_id)

    # ------------------------------------------------------------------
    # Category helpers
    # ------------------------------------------------------------------

    def list_categories(self) -> list[str]:
        """Return distinct categories for this profile.

        Returns
        -------
        list[str]
            Sorted list of unique category names.
        """
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT category FROM knowledge_base
                WHERE profile_name = ?
                ORDER BY category ASC
                """,
                (self._profile_name,),
            )
            rows = cursor.fetchall()

        return [row["category"] for row in rows]

    def list_by_category(
        self,
        category: str,
        limit: int = 50,
    ) -> list[KnowledgeEntry]:
        """List knowledge entries in a given category.

        Parameters
        ----------
        category : str
            The category to filter by.
        limit : int
            Maximum results.  Default: 50.

        Returns
        -------
        list[KnowledgeEntry]
            Matching entries ordered by ``updated_at`` descending.
        """
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM knowledge_base
                WHERE profile_name = ? AND category = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (self._profile_name, category, limit),
            )
            rows = cursor.fetchall()

        return [_row_to_knowledge_entry(row) for row in rows]

    # ------------------------------------------------------------------
    # Learning extraction
    # ------------------------------------------------------------------

    def extract_learnings(
        self,
        interaction_summary: str,
        source_context: str,
    ) -> list[KnowledgeEntry]:
        """Extract potential learnings from an interaction summary.

        Uses pattern-based heuristics (not LLM) to identify sentences
        that look like decisions, findings, or other noteworthy knowledge.

        The returned entries are **not** stored — the caller is responsible
        for persisting them via :meth:`add_knowledge`.

        Parameters
        ----------
        interaction_summary : str
            Free-text summary of an interaction or session.
        source_context : str
            Contextual label describing where the summary came from.

        Returns
        -------
        list[KnowledgeEntry]
            Extracted knowledge entries with ``category='auto-extracted'``
            and ``tags=['auto-extracted']``.  If no patterns match, a single
            entry wrapping the entire summary is returned.
        """
        if not interaction_summary or not interaction_summary.strip():
            return []

        # Split into sentences / paragraphs
        segments = _SENTENCE_SPLITTER.split(interaction_summary.strip())
        segments = [s.strip() for s in segments if s.strip()]

        extracted: list[KnowledgeEntry] = []

        for segment in segments:
            for pattern in _LEARNING_PATTERNS:
                if pattern.search(segment):
                    entry = KnowledgeEntry(
                        entry_id="",
                        profile_name=self._profile_name,
                        category="auto-extracted",
                        title=segment[:120],
                        content=segment,
                        source_profile=self._profile_name,
                        source_context=source_context,
                        tags=["auto-extracted"],
                    )
                    extracted.append(entry)
                    break  # one match per segment is enough

        # Fallback: wrap entire summary if nothing matched
        if not extracted:
            entry = KnowledgeEntry(
                entry_id="",
                profile_name=self._profile_name,
                category="auto-extracted",
                title=interaction_summary[:120],
                content=interaction_summary,
                source_profile=self._profile_name,
                source_context=source_context,
                tags=["auto-extracted"],
            )
            extracted.append(entry)

        return extracted

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return aggregate statistics for this profile's knowledge base.

        Returns
        -------
        dict
            ``{total_entries, by_category: {cat: count}, total_bytes}``
        """
        with self._cursor() as cursor:
            # Total entries
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM knowledge_base WHERE profile_name = ?",
                (self._profile_name,),
            )
            total_entries = cursor.fetchone()["cnt"]

            # By category
            cursor.execute(
                """
                SELECT category, COUNT(*) AS cnt FROM knowledge_base
                WHERE profile_name = ?
                GROUP BY category
                ORDER BY category ASC
                """,
                (self._profile_name,),
            )
            by_category = {row["category"]: row["cnt"] for row in cursor.fetchall()}

            # Total bytes (sum of content lengths)
            cursor.execute(
                """
                SELECT COALESCE(SUM(LENGTH(content)), 0) AS total_bytes
                FROM knowledge_base
                WHERE profile_name = ?
                """,
                (self._profile_name,),
            )
            total_bytes = cursor.fetchone()["total_bytes"]

        return {
            "total_entries": total_entries,
            "by_category": by_category,
            "total_bytes": total_bytes,
        }


__all__ = [
    "KnowledgeBase",
    "_row_to_knowledge_entry",
]
