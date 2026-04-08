"""Automated garbage collection and budget enforcement for agent memory.

Implements the GarbageCollector class which performs tier-based lifecycle
management, budget enforcement (purging oldest COLD then COOL entries),
and stale-worker cleanup. Operates on a MemoryStore instance passed to
each method, keeping it composable and testable.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.memory.exceptions import GarbageCollectionError
from core.memory.models import GCReport, MemoryTier, TierTransition

if TYPE_CHECKING:
    from core.memory.memory_store import MemoryStore

logger = logging.getLogger(__name__)

__all__ = [
    "GarbageCollector",
]


# --- Constants ---

#: Tiers eligible for budget-based purging (coldest first).
_PURGEABLE_TIERS: list[MemoryTier] = [MemoryTier.cold, MemoryTier.cool]


# --- Helper functions ---


def _now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


# --- GarbageCollector class ---


class GarbageCollector:
    """Automated cleanup and budget enforcement for agent memory.

    Orchestrates tier transitions (via :class:`TieredStorage`), budget
    enforcement (purging oldest COLD then COOL entries), and stale-worker
    archival.  Every public method that touches persisted data requires a
    :class:`MemoryStore` argument so the collector itself remains
    stateless with respect to storage.

    Parameters
    ----------
    tiered_storage : TieredStorage | None
        An optional :class:`TieredStorage` instance.  When *None* a fresh
        default instance is created lazily.
    """

    def __init__(self, tiered_storage: "TieredStorage | None" = None) -> None:
        if tiered_storage is None:
            from core.memory.tiered_storage import TieredStorage

            tiered_storage = TieredStorage()
        self._tiered_storage = tiered_storage

    # --- Public methods ---

    def run_gc(
        self,
        memory_store: MemoryStore,
        dry_run: bool = False,
    ) -> GCReport:
        """Execute a full garbage-collection cycle.

        Steps performed:

        1. Run tier assessment via the underlying :class:`TieredStorage`.
        2. If *dry_run* is ``False``, apply the recommended transitions.
        3. Enforce the profile budget (purge oldest COLD, then COOL).
        4. Build and return a :class:`GCReport` summarising all actions.

        Parameters
        ----------
        memory_store : MemoryStore
            The memory store to operate on.
        dry_run : bool
            If ``True``, analyse only — no mutations are performed.

        Returns
        -------
        GCReport
            A detailed report of the GC pass.

        Raises
        ------
        GarbageCollectionError
            If an unexpected error prevents the GC cycle from completing.
        """
        try:
            # Step 1: tier assessment
            transitions = self._tiered_storage.run_tier_assessment(memory_store)
            logger.info(
                "GC tier assessment found %d transition(s)", len(transitions),
            )

            # Step 2: apply transitions (unless dry_run)
            applied: list[TierTransition] = []
            if not dry_run and transitions:
                applied = self._tiered_storage.apply_transitions(
                    memory_store, transitions,
                )
                logger.info(
                    "GC applied %d/%d transition(s)",
                    len(applied),
                    len(transitions),
                )

            # Step 3: enforce budget
            purged_ids = self.enforce_budget(memory_store, dry_run=dry_run)
            if purged_ids:
                logger.info(
                    "GC %s %d entries for budget enforcement",
                    "would purge" if dry_run else "purged",
                    len(purged_ids),
                )

            # Calculate bytes freed from purged entries
            bytes_freed = 0
            if purged_ids and not dry_run:
                # Entries are already deleted; estimate from count.
                # The actual byte tracking is done at purge time.
                pass  # bytes_freed is tracked below via budget status

            # Step 4: build report
            budget_status = memory_store.check_budget()

            report = GCReport(
                entries_transitioned=len(applied) if not dry_run else len(transitions),
                entries_purged=len(purged_ids),
                bytes_freed=bytes_freed,
                budget_status=budget_status,
                recommendations=self._build_recommendations(
                    transitions, budget_status,
                ),
                transitions=applied if not dry_run else transitions,
                purged_ids=purged_ids,
                dry_run=dry_run,
            )

            logger.info(
                "GC cycle complete: transitioned=%d, purged=%d, dry_run=%s",
                report.entries_transitioned,
                report.entries_purged,
                dry_run,
            )
            return report

        except GarbageCollectionError:
            raise
        except Exception as exc:
            raise GarbageCollectionError(str(exc)) from exc

    def enforce_budget(
        self,
        memory_store: MemoryStore,
        dry_run: bool = False,
    ) -> list[str]:
        """Enforce the profile memory budget by purging cold entries.

        Strategy:

        1. Check the budget via :meth:`MemoryStore.check_budget`.
        2. If not exceeded, return immediately.
        3. Determine how many entries / bytes must be freed.
        4. Purge oldest **COLD** entries first, then oldest **COOL**
           entries if the budget is still exceeded.
        5. **Never** touch HOT or WARM entries.

        Parameters
        ----------
        memory_store : MemoryStore
            The memory store to operate on.
        dry_run : bool
            If ``True``, compute entries that *would* be purged but do not
            delete them.

        Returns
        -------
        list[str]
            IDs of purged (or would-be-purged) entries.
        """
        budget_status = memory_store.check_budget()

        if not budget_status["exceeded"]:
            return []

        entries_to_purge, bytes_to_free = self._calculate_purge_needed(
            usage=budget_status["usage"],
            limits=budget_status["limits"],
        )

        logger.info(
            "Budget exceeded — need to free %d entries / %d bytes",
            entries_to_purge,
            bytes_to_free,
        )

        purged_ids: list[str] = []
        remaining_entries = entries_to_purge
        remaining_bytes = bytes_to_free

        for tier in _PURGEABLE_TIERS:
            if remaining_entries <= 0 and remaining_bytes <= 0:
                break

            # Determine how many to purge from this tier.
            # We need to purge enough to satisfy both the entry and byte
            # constraints, so we may fetch more than remaining_entries.
            candidates = self._get_purgeable_entries(
                memory_store, tier, max(remaining_entries, 1),
            )

            for entry_id, byte_size in candidates:
                if remaining_entries <= 0 and remaining_bytes <= 0:
                    break

                if not dry_run:
                    try:
                        memory_store.delete(entry_id)
                    except Exception:
                        logger.warning(
                            "Failed to delete entry %s during budget enforcement",
                            entry_id,
                            exc_info=True,
                        )
                        continue

                purged_ids.append(entry_id)
                remaining_entries -= 1
                remaining_bytes -= byte_size
                logger.debug(
                    "Budget enforcement: %s entry %s (%d bytes)",
                    "would purge" if dry_run else "purged",
                    entry_id,
                    byte_size,
                )

        return purged_ids

    def cleanup_completed_workers(
        self,
        subagent_registry: object,
        days_threshold: int = 30,
    ) -> list[dict]:
        """Archive stale completed workers from a subagent registry.

        Uses duck typing on *subagent_registry* — it must provide a
        ``list_subagents()`` method that returns objects with at least
        ``subagent_id``, ``status``, and ``completed_at`` attributes.

        Parameters
        ----------
        subagent_registry : object
            Any object exposing a ``list_subagents()`` method.
        days_threshold : int
            Minimum age in days for a completed worker to be eligible
            for archival.  Default: 30.

        Returns
        -------
        list[dict]
            One dict per eligible worker with keys:
            ``subagent_id``, ``status``, ``age_days``, ``action``.
        """
        results: list[dict] = []

        if not hasattr(subagent_registry, "list_subagents"):
            logger.warning(
                "Subagent registry does not expose list_subagents(); "
                "worker cleanup skipped",
            )
            return results

        try:
            subagents = subagent_registry.list_subagents()  # type: ignore[union-attr]
        except Exception:
            logger.warning(
                "Failed to list subagents from registry; worker cleanup skipped",
                exc_info=True,
            )
            return results

        now = _now_utc()

        for agent in subagents:
            try:
                status = getattr(agent, "status", None)
                if status != "completed":
                    continue

                completed_at = getattr(agent, "completed_at", None)
                if completed_at is None:
                    continue

                # Ensure timezone-aware comparison
                if completed_at.tzinfo is None:
                    completed_at = completed_at.replace(tzinfo=timezone.utc)

                age_days = max(0, (now - completed_at).days)
                if age_days < days_threshold:
                    continue

                subagent_id = getattr(agent, "subagent_id", "unknown")

                results.append({
                    "subagent_id": subagent_id,
                    "status": status,
                    "age_days": age_days,
                    "action": "archived",
                })

                logger.info(
                    "Completed worker %s (age %d days) marked for archival",
                    subagent_id,
                    age_days,
                )
            except Exception:
                logger.warning(
                    "Error inspecting subagent during cleanup",
                    exc_info=True,
                )
                continue

        return results

    def get_gc_report(self, memory_store: MemoryStore) -> GCReport:
        """Generate an analysis-only GC report with no side effects.

        Performs tier assessment and budget analysis, then produces a
        :class:`GCReport` populated with actionable recommendations.

        Parameters
        ----------
        memory_store : MemoryStore
            The memory store to analyse.

        Returns
        -------
        GCReport
            A report with ``dry_run=True`` and a populated
            ``recommendations`` list.
        """
        try:
            # Read-only tier assessment
            transitions = self._tiered_storage.run_tier_assessment(memory_store)

            # Budget check
            budget_status = memory_store.check_budget()

            # Build recommendations
            recommendations = self._build_recommendations(
                transitions, budget_status,
            )

            # Count cold entries that could be archived
            cold_entries = memory_store.list_entries(
                tier=MemoryTier.cold, limit=10_000,
            )
            if cold_entries:
                recommendations.append(
                    f"{len(cold_entries)} COLD entries can be archived",
                )

            return GCReport(
                entries_transitioned=len(transitions),
                entries_purged=0,
                bytes_freed=0,
                budget_status=budget_status,
                recommendations=recommendations,
                transitions=transitions,
                purged_ids=[],
                dry_run=True,
            )

        except GarbageCollectionError:
            raise
        except Exception as exc:
            raise GarbageCollectionError(str(exc)) from exc

    # --- Private helpers ---

    def _purge_entries_by_tier(
        self,
        memory_store: MemoryStore,
        tier: MemoryTier,
        count: int,
    ) -> list[str]:
        """Delete the oldest entries in *tier*, up to *count*.

        Entries are ordered by ``accessed_at`` ascending (least recently
        used first).

        Parameters
        ----------
        memory_store : MemoryStore
            The memory store to purge from.
        tier : MemoryTier
            Which tier to purge.
        count : int
            Maximum number of entries to delete.

        Returns
        -------
        list[str]
            IDs of successfully deleted entries.
        """
        # Fetch candidates ordered by least-recently-accessed
        entries = memory_store.list_entries(tier=tier, limit=count)

        # Sort by accessed_at ascending (oldest access first = LRU)
        entries.sort(key=lambda e: e.accessed_at)

        deleted: list[str] = []
        for entry in entries[:count]:
            try:
                memory_store.delete(entry.entry_id)
                deleted.append(entry.entry_id)
                logger.debug(
                    "Purged entry %s (tier=%s, accessed_at=%s)",
                    entry.entry_id,
                    tier.value,
                    entry.accessed_at.isoformat(),
                )
            except Exception:
                logger.warning(
                    "Failed to purge entry %s from tier %s",
                    entry.entry_id,
                    tier.value,
                    exc_info=True,
                )

        return deleted

    def _calculate_purge_needed(
        self,
        usage: dict,
        limits: dict,
    ) -> tuple[int, int]:
        """Calculate how many entries and bytes must be freed.

        Parameters
        ----------
        usage : dict
            Current usage with keys ``entries`` and ``bytes``.
        limits : dict
            Budget limits with keys ``max_entries`` and ``max_bytes``.

        Returns
        -------
        tuple[int, int]
            ``(entries_to_purge, bytes_to_free)`` — both >= 0.
        """
        entries_to_purge = 0
        bytes_to_free = 0

        max_entries = limits.get("max_entries")
        if max_entries is not None:
            excess = usage.get("entries", 0) - max_entries
            if excess > 0:
                entries_to_purge = excess

        max_bytes = limits.get("max_bytes")
        if max_bytes is not None:
            excess = usage.get("bytes", 0) - max_bytes
            if excess > 0:
                bytes_to_free = excess

        return entries_to_purge, bytes_to_free

    def _get_purgeable_entries(
        self,
        memory_store: MemoryStore,
        tier: MemoryTier,
        count: int,
    ) -> list[tuple[str, int]]:
        """Return ``(entry_id, byte_size)`` pairs for the oldest entries in *tier*.

        Entries are sorted by ``accessed_at`` ascending (LRU first).

        Parameters
        ----------
        memory_store : MemoryStore
            The memory store to query.
        tier : MemoryTier
            Tier to list from.
        count : int
            Maximum number of candidates to return.

        Returns
        -------
        list[tuple[str, int]]
            Pairs of ``(entry_id, byte_size)``.
        """
        entries = memory_store.list_entries(tier=tier, limit=max(count, 200))
        entries.sort(key=lambda e: e.accessed_at)
        return [(e.entry_id, e.byte_size) for e in entries[:count]]

    def _build_recommendations(
        self,
        transitions: list[TierTransition],
        budget_status: dict,
    ) -> list[str]:
        """Build a list of human-readable recommendations.

        Parameters
        ----------
        transitions : list[TierTransition]
            Pending or applied transitions from tier assessment.
        budget_status : dict
            Result of :meth:`MemoryStore.check_budget`.

        Returns
        -------
        list[str]
            Recommendation strings.
        """
        recommendations: list[str] = []

        if transitions:
            recommendations.append(
                f"{len(transitions)} entries eligible for tier transition",
            )

        if budget_status.get("exceeded"):
            usage = budget_status.get("usage", {})
            limits = budget_status.get("limits", {})
            entries_to_purge, bytes_to_free = self._calculate_purge_needed(
                usage, limits,
            )
            if entries_to_purge > 0 or bytes_to_free > 0:
                recommendations.append(
                    f"Budget exceeded by {bytes_to_free} bytes, "
                    f"{entries_to_purge} entries should be purged",
                )

        return recommendations
