"""Subagent Registry — tracks worker subagents spawned by Project Managers.

Each project manager profile gets its own isolated SQLite database.
The SubagentRegistry manages connections to these per-PM databases and
provides CRUD operations with status transition validation.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generator, List

logger = logging.getLogger(__name__)

from core.workers.exceptions import (
    InvalidProjectManager,
    InvalidSubagentStatus,
    SubagentNotFound,
)
from core.workers.models import (
    Subagent,
    SubagentStatus,
    generate_subagent_id,
    is_valid_transition,
)
from core.workers.schema import init_subagent_db


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_subagent(row: sqlite3.Row) -> Subagent:
    """Convert a SQLite Row to a Subagent dataclass."""
    return Subagent(
        subagent_id=row["subagent_id"],
        project_manager=row["project_manager"],
        task_goal=row["task_goal"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        conversation_path=row["conversation_path"],
        result_summary=row["result_summary"],
        artifacts=Subagent.artifacts_from_json(row["artifacts"]),
        token_cost=row["token_cost"],
        parent_request_id=row["parent_request_id"],
    )


class SubagentRegistry:
    """Registry for managing subagent workers across project managers.

    Parameters
    ----------
    base_path:
        Root directory for subagent databases.  Each PM gets a database at
        ``<base_path>/<pm_profile>/subagents.db``.
        Pass ``":memory:"`` for a single in-memory database (useful for tests).
    profile_registry:
        Optional :class:`~core.registry.ProfileRegistry` instance.  When provided,
        ``register()`` validates that the project manager exists and has the
        correct role.  When ``None``, this validation is skipped.
    """

    def __init__(
        self,
        base_path: str = ":memory:",
        profile_registry: object | None = None,
    ) -> None:
        self._base_path = base_path
        self._profile_registry = profile_registry
        self._lock = threading.Lock()

        # Callbacks fired when a worker is marked complete.
        # Each callable receives (subagent_id: str, result_summary: str).
        self._completion_callbacks: List[Callable[[str, str], None]] = []

        # For :memory: mode, we keep a single shared connection.
        # For file-backed mode, we lazily open per-PM connections.
        self._connections: dict[str, sqlite3.Connection] = {}

        if base_path == ":memory:":
            self._connections["_memory"] = init_subagent_db(":memory:")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_connection(self, project_manager: str) -> sqlite3.Connection:
        """Get or create a database connection for the given PM."""
        if self._base_path == ":memory:":
            return self._connections["_memory"]

        if project_manager not in self._connections:
            db_dir = Path(self._base_path) / project_manager
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "subagents.db")
            self._connections[project_manager] = init_subagent_db(db_path)

        return self._connections[project_manager]

    @contextmanager
    def _cursor(
        self,
        project_manager: str,
        *,
        commit: bool = False,
    ) -> Generator[sqlite3.Cursor, None, None]:
        """Yield a cursor under the registry lock.

        Parameters
        ----------
        project_manager:
            The PM whose database to use.
        commit:
            If ``True``, commit the transaction on successful exit.
            On exception the transaction is rolled back.
        """
        conn = self._get_connection(project_manager)
        with self._lock:
            cursor = conn.cursor()
            try:
                yield cursor
                if commit:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Completion callbacks
    # ------------------------------------------------------------------

    def register_completion_callback(
        self,
        fn: Callable[[str, str], None],
    ) -> None:
        """Register a callable to be invoked when any worker completes.

        The callback is called with ``(subagent_id, result_summary)``
        immediately after the subagent's status is persisted as
        ``completed``.  Callbacks are invoked in registration order.
        A failing callback is logged and skipped so that it does not
        prevent other callbacks from running.

        Parameters
        ----------
        fn:
            A callable with signature ``fn(subagent_id: str,
            result_summary: str) -> None``.
        """
        self._completion_callbacks.append(fn)

    def _fire_completion_callbacks(
        self,
        subagent_id: str,
        result_summary: str,
    ) -> None:
        """Invoke all registered completion callbacks in order.

        Each callback is wrapped in its own try/except so that a single
        failing callback cannot block the others.

        Parameters
        ----------
        subagent_id:
            ID of the worker that just completed.
        result_summary:
            Result text recorded on the worker.
        """
        for cb in list(self._completion_callbacks):
            try:
                cb(subagent_id, result_summary)
            except Exception:
                logger.exception(
                    "Completion callback %r raised an exception for subagent_id=%s",
                    cb,
                    subagent_id,
                )

    # ------------------------------------------------------------------
    # PM validation
    # ------------------------------------------------------------------

    def _validate_project_manager(self, pm_name: str) -> None:
        """Validate that the PM exists and has the correct role.

        Only performed when a profile_registry was supplied.
        """
        if self._profile_registry is None:
            return

        # Use duck-typing to avoid importing ProfileRegistry at module level.
        try:
            profile = self._profile_registry.get_profile(pm_name)  # type: ignore[union-attr]
        except Exception:
            raise InvalidProjectManager(pm_name, "profile not found in registry")

        if profile.role != "project_manager":
            raise InvalidProjectManager(
                pm_name,
                f"expected role 'project_manager', got '{profile.role}'",
            )

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def register(
        self,
        project_manager: str,
        task_goal: str,
        *,
        parent_request_id: str | None = None,
        conversation_path: str | None = None,
    ) -> Subagent:
        """Register a new subagent worker.

        Creates a new entry with status ``running``.

        Parameters
        ----------
        project_manager:
            Profile name of the PM that owns this worker.
        task_goal:
            Description of what the worker should accomplish.
        parent_request_id:
            Optional ID linking this subagent to an upstream request.
        conversation_path:
            Optional path to the worker's session directory.

        Returns
        -------
        Subagent
            The newly created subagent record.
        """
        self._validate_project_manager(project_manager)

        subagent_id = generate_subagent_id()
        now = _now_iso()

        with self._cursor(project_manager, commit=True) as cur:
            cur.execute(
                """
                INSERT INTO subagents
                    (subagent_id, project_manager, task_goal, status,
                     created_at, updated_at, conversation_path,
                     parent_request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subagent_id,
                    project_manager,
                    task_goal,
                    SubagentStatus.RUNNING.value,
                    now,
                    now,
                    conversation_path,
                    parent_request_id,
                ),
            )

        return self.get(subagent_id, project_manager=project_manager)

    def get(
        self,
        subagent_id: str,
        *,
        project_manager: str | None = None,
    ) -> Subagent:
        """Fetch a subagent by ID.

        Parameters
        ----------
        subagent_id:
            The unique subagent identifier.
        project_manager:
            If provided, search only this PM's database.  If ``None`` and using
            file-backed storage, searches all open PM connections.

        Returns
        -------
        Subagent
            The matching subagent record.

        Raises
        ------
        SubagentNotFound
            If no subagent with the given ID exists.
        """
        if self._base_path == ":memory:" or project_manager is not None:
            pm = project_manager or "_memory"
            return self._get_from_pm(subagent_id, pm)

        # File-backed: search all open connections
        for pm in list(self._connections.keys()):
            try:
                return self._get_from_pm(subagent_id, pm)
            except SubagentNotFound:
                continue

        raise SubagentNotFound(subagent_id)

    def _get_from_pm(self, subagent_id: str, project_manager: str) -> Subagent:
        """Fetch a subagent from a specific PM's database."""
        with self._cursor(project_manager) as cur:
            cur.execute(
                "SELECT * FROM subagents WHERE subagent_id = ?",
                (subagent_id,),
            )
            row = cur.fetchone()

        if row is None:
            raise SubagentNotFound(subagent_id)

        return _row_to_subagent(row)

    def update_status(
        self,
        subagent_id: str,
        target_status: str | SubagentStatus,
        *,
        project_manager: str | None = None,
    ) -> Subagent:
        """Transition a subagent to a new status.

        Validates the transition against the state machine.

        Parameters
        ----------
        subagent_id:
            The subagent to update.
        target_status:
            The desired new status.
        project_manager:
            PM that owns this subagent (required for file-backed DBs).

        Returns
        -------
        Subagent
            The updated subagent record.

        Raises
        ------
        InvalidSubagentStatus
            If the transition is not allowed.
        """
        if isinstance(target_status, str):
            target_status = SubagentStatus(target_status)

        current = self.get(subagent_id, project_manager=project_manager)
        current_status = SubagentStatus(current.status)

        if not is_valid_transition(current_status, target_status):
            raise InvalidSubagentStatus(
                subagent_id, current_status.value, target_status.value
            )

        pm = project_manager or current.project_manager
        now = _now_iso()

        with self._cursor(pm, commit=True) as cur:
            cur.execute(
                """
                UPDATE subagents
                SET status = ?, updated_at = ?
                WHERE subagent_id = ?
                """,
                (target_status.value, now, subagent_id),
            )

        return self.get(subagent_id, project_manager=pm)

    def complete(
        self,
        subagent_id: str,
        result_summary: str,
        *,
        artifacts: list[str] | None = None,
        token_cost: int | None = None,
        project_manager: str | None = None,
    ) -> Subagent:
        """Mark a subagent as completed with results.

        Transitions status from ``running`` to ``completed`` and records
        the result summary, artifacts, and token cost.

        Parameters
        ----------
        subagent_id:
            The subagent to complete.
        result_summary:
            Human-readable summary of what the worker accomplished.
        artifacts:
            List of file paths created/modified by the worker.
        token_cost:
            Total tokens consumed during the worker's execution.
        project_manager:
            PM that owns this subagent.
        """
        current = self.get(subagent_id, project_manager=project_manager)
        current_status = SubagentStatus(current.status)

        if not is_valid_transition(current_status, SubagentStatus.COMPLETED):
            raise InvalidSubagentStatus(
                subagent_id, current_status.value, SubagentStatus.COMPLETED.value
            )

        pm = project_manager or current.project_manager
        now = _now_iso()
        artifacts_json = Subagent(
            subagent_id="", project_manager="", task_goal="",
            artifacts=artifacts or [],
        ).artifacts_as_json()

        with self._cursor(pm, commit=True) as cur:
            updates = [
                "status = ?",
                "updated_at = ?",
                "result_summary = ?",
                "artifacts = ?",
            ]
            params: list[str | int] = [
                SubagentStatus.COMPLETED.value,
                now,
                result_summary,
                artifacts_json,
            ]

            if token_cost is not None:
                updates.append("token_cost = ?")
                params.append(token_cost)

            params.append(subagent_id)
            cur.execute(
                f"UPDATE subagents SET {', '.join(updates)} WHERE subagent_id = ?",
                params,
            )

        updated = self.get(subagent_id, project_manager=pm)
        self._fire_completion_callbacks(subagent_id, result_summary)
        return updated

    def sleep(
        self,
        subagent_id: str,
        *,
        project_manager: str | None = None,
    ) -> Subagent:
        """Transition a running subagent to sleeping (paused).

        Parameters
        ----------
        subagent_id:
            The subagent to pause.
        project_manager:
            PM that owns this subagent.
        """
        return self.update_status(
            subagent_id,
            SubagentStatus.SLEEPING,
            project_manager=project_manager,
        )

    def archive(
        self,
        subagent_id: str,
        *,
        project_manager: str | None = None,
    ) -> Subagent:
        """Transition a completed subagent to archived.

        Parameters
        ----------
        subagent_id:
            The subagent to archive.
        project_manager:
            PM that owns this subagent.
        """
        return self.update_status(
            subagent_id,
            SubagentStatus.ARCHIVED,
            project_manager=project_manager,
        )

    def list(
        self,
        *,
        project_manager: str | None = None,
        status: str | SubagentStatus | None = None,
        parent_request_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Subagent]:
        """List subagents with optional filters.

        Parameters
        ----------
        project_manager:
            Filter by PM.  For in-memory mode, all subagents are in one DB.
            For file-backed mode, if ``None``, searches all open connections.
        status:
            Filter by status.
        parent_request_id:
            Filter by parent request ID.
        limit:
            Maximum number of results.
        offset:
            Number of results to skip (for pagination).

        Returns
        -------
        list[Subagent]
            Matching subagents, ordered by created_at descending.
        """
        if isinstance(status, SubagentStatus):
            status = status.value

        if self._base_path == ":memory:" or project_manager is not None:
            pm_key = "_memory" if self._base_path == ":memory:" else project_manager
            return self._list_from_pm(
                pm_key,
                status=status,
                parent_request_id=parent_request_id,
                project_manager_filter=project_manager if self._base_path == ":memory:" else None,
                limit=limit, offset=offset,
            )

        # File-backed: aggregate across all open PMs
        all_results: list[Subagent] = []
        for pm in list(self._connections.keys()):
            all_results.extend(
                self._list_from_pm(
                    pm, status=status, parent_request_id=parent_request_id,
                    limit=limit + offset, offset=0,
                )
            )
        # Sort by created_at desc and apply pagination
        all_results.sort(key=lambda s: s.created_at, reverse=True)
        return all_results[offset : offset + limit]

    def _list_from_pm(
        self,
        project_manager: str,
        *,
        status: str | None = None,
        parent_request_id: str | None = None,
        project_manager_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Subagent]:
        """List subagents from a specific PM's database."""
        conditions: list[str] = []
        params: list[str | int] = []

        if project_manager_filter is not None:
            conditions.append("project_manager = ?")
            params.append(project_manager_filter)

        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        if parent_request_id is not None:
            conditions.append("parent_request_id = ?")
            params.append(parent_request_id)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])

        with self._cursor(project_manager) as cur:
            cur.execute(
                f"""
                SELECT * FROM subagents
                {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params,
            )
            rows = cur.fetchall()

        return [_row_to_subagent(row) for row in rows]

    def delete(
        self,
        subagent_id: str,
        *,
        project_manager: str | None = None,
    ) -> None:
        """Hard-delete a subagent record.

        Use sparingly — prefer :meth:`archive` for normal cleanup.

        Parameters
        ----------
        subagent_id:
            The subagent to delete.
        project_manager:
            PM that owns this subagent.
        """
        current = self.get(subagent_id, project_manager=project_manager)
        pm = project_manager or current.project_manager

        with self._cursor(pm, commit=True) as cur:
            cur.execute(
                "DELETE FROM subagents WHERE subagent_id = ?",
                (subagent_id,),
            )

    def get_stats(
        self,
        *,
        project_manager: str | None = None,
    ) -> dict:
        """Get aggregate statistics for subagents.

        Parameters
        ----------
        project_manager:
            If provided, stats for this PM only.  Otherwise, aggregate
            across all open connections.

        Returns
        -------
        dict
            Keys: ``total``, ``by_status`` (dict), ``total_token_cost``.
        """
        if self._base_path == ":memory:" or project_manager is not None:
            pm = project_manager or "_memory"
            return self._stats_from_pm(pm)

        # Aggregate across all PMs
        totals: dict = {"total": 0, "by_status": {}, "total_token_cost": 0}
        for pm in list(self._connections.keys()):
            pm_stats = self._stats_from_pm(pm)
            totals["total"] += pm_stats["total"]
            totals["total_token_cost"] += pm_stats["total_token_cost"]
            for status_key, count in pm_stats["by_status"].items():
                totals["by_status"][status_key] = (
                    totals["by_status"].get(status_key, 0) + count
                )
        return totals

    def _stats_from_pm(self, project_manager: str) -> dict:
        """Get stats from a specific PM's database."""
        with self._cursor(project_manager) as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM subagents")
            total = cur.fetchone()["cnt"]

            cur.execute(
                "SELECT status, COUNT(*) as cnt FROM subagents GROUP BY status"
            )
            by_status = {row["status"]: row["cnt"] for row in cur.fetchall()}

            cur.execute("SELECT COALESCE(SUM(token_cost), 0) as total FROM subagents")
            total_tokens = cur.fetchone()["total"]

        return {
            "total": total,
            "by_status": by_status,
            "total_token_cost": total_tokens,
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close all database connections."""
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()
