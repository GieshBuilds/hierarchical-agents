"""Tiered storage lifecycle management for hierarchical agent memory.

Implements the HOT -> WARM -> COOL -> COLD tier lifecycle. The TieredStorage
class operates *on* a MemoryStore instance (passed to each method) rather than
owning a database connection, keeping it composable and testable.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.memory.exceptions import InvalidTierTransition
from core.memory.models import (
    COLD_AGE_DAYS,
    COOL_AGE_DAYS,
    MemoryEntry,
    MemoryEntryType,
    MemoryTier,
    TierTransition,
    VALID_TIER_TRANSITIONS,
    WARM_AGE_DAYS,
    generate_transition_id,
    is_valid_tier_transition,
)

if TYPE_CHECKING:
    from core.memory.memory_store import MemoryStore

logger = logging.getLogger(__name__)


# --- Constants ---

#: Tier ordering for comparison (lower index = hotter).
_TIER_ORDER: dict[MemoryTier, int] = {
    MemoryTier.hot: 0,
    MemoryTier.warm: 1,
    MemoryTier.cool: 2,
    MemoryTier.cold: 3,
}

#: Map from tier to its *next* tier in the lifecycle (single-step forward).
_NEXT_TIER: dict[MemoryTier, MemoryTier | None] = {
    MemoryTier.hot: MemoryTier.warm,
    MemoryTier.warm: MemoryTier.cool,
    MemoryTier.cool: MemoryTier.cold,
    MemoryTier.cold: None,  # terminal
}


# --- Helper functions ---


def _now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def _age_days(entry: MemoryEntry) -> int:
    """Return the age of *entry* in whole days since creation."""
    delta = _now_utc() - entry.created_at
    return max(0, delta.days)


def _tier_ge(a: MemoryTier, b: MemoryTier) -> bool:
    """Return True if tier *a* is the same as or colder than tier *b*."""
    return _TIER_ORDER[a] >= _TIER_ORDER[b]


def _threshold_for_tier(
    tier: MemoryTier,
    warm_age_days: int,
    cool_age_days: int,
    cold_age_days: int,
) -> int | None:
    """Return the age-in-days threshold at which entries enter *tier*.

    Returns ``None`` for ``hot`` (the initial tier, no age threshold).
    """
    return {
        MemoryTier.hot: None,
        MemoryTier.warm: warm_age_days,
        MemoryTier.cool: cool_age_days,
        MemoryTier.cold: cold_age_days,
    }[tier]


# --- TieredStorage class ---


class TieredStorage:
    """Manage the HOT -> WARM -> COOL -> COLD memory lifecycle.

    The class is stateless with respect to storage — every method that
    touches persisted data requires a :class:`MemoryStore` argument.
    Configuration (age thresholds) is set at construction time.

    Parameters
    ----------
    warm_age_days : int
        Minimum age in days before an entry is eligible for WARM tier.
    cool_age_days : int
        Minimum age in days before an entry is eligible for COOL tier.
    cold_age_days : int
        Minimum age in days before an entry is eligible for COLD tier.
    """

    def __init__(
        self,
        warm_age_days: int = WARM_AGE_DAYS,
        cool_age_days: int = COOL_AGE_DAYS,
        cold_age_days: int = COLD_AGE_DAYS,
    ) -> None:
        self._warm_age_days = warm_age_days
        self._cool_age_days = cool_age_days
        self._cold_age_days = cold_age_days

    # --- Properties ---

    @property
    def warm_age_days(self) -> int:
        """Age threshold (days) for the WARM tier."""
        return self._warm_age_days

    @property
    def cool_age_days(self) -> int:
        """Age threshold (days) for the COOL tier."""
        return self._cool_age_days

    @property
    def cold_age_days(self) -> int:
        """Age threshold (days) for the COLD tier."""
        return self._cold_age_days

    # --- Tier assessment ---

    def assess_tier(self, entry: MemoryEntry) -> MemoryTier:
        """Determine the appropriate tier for *entry* based on its age.

        The recommended tier is always >= (colder or same as) the entry's
        current tier — backward transitions are never recommended.

        Parameters
        ----------
        entry : MemoryEntry
            The memory entry to assess.

        Returns
        -------
        MemoryTier
            The recommended storage tier.
        """
        age = _age_days(entry)

        # Determine the ideal tier purely from age thresholds.
        if age >= self._cold_age_days:
            ideal = MemoryTier.cold
        elif age >= self._cool_age_days:
            ideal = MemoryTier.cool
        elif age >= self._warm_age_days:
            ideal = MemoryTier.warm
        else:
            ideal = MemoryTier.hot

        # Never recommend a backward (hotter) transition.
        if _tier_ge(ideal, entry.tier):
            return ideal
        return entry.tier

    def _next_valid_step(self, entry: MemoryEntry) -> MemoryTier | None:
        """Return the immediate next tier if the entry should advance.

        Because ``VALID_TIER_TRANSITIONS`` only allows single-step moves
        (hot->warm, warm->cool, cool->cold), this method returns the
        *next single step* toward the assessed target tier, or ``None``
        if the entry is already at or beyond its recommended tier.
        """
        target = self.assess_tier(entry)
        if target == entry.tier:
            return None
        # The next valid step from the current tier.
        return _NEXT_TIER.get(entry.tier)

    # --- Bulk assessment ---

    def run_tier_assessment(
        self,
        memory_store: MemoryStore,
    ) -> list[TierTransition]:
        """Scan all entries and produce a list of recommended transitions.

        For each entry whose assessed tier differs from its current tier,
        a :class:`TierTransition` record is created. Only transitions that
        are valid single-step moves (per ``VALID_TIER_TRANSITIONS``) are
        included.

        .. note::

            This method is *read-only* — it does **not** apply transitions.
            Pass the result to :meth:`apply_transitions` to persist them.

        Parameters
        ----------
        memory_store : MemoryStore
            The memory store to scan.

        Returns
        -------
        list[TierTransition]
            Recommended transitions, sorted by entry age descending
            (oldest entries first).
        """
        transitions: list[TierTransition] = []

        # Paginate through all entries.
        offset = 0
        batch_size = 200
        while True:
            entries = memory_store.list_entries(offset=offset, limit=batch_size)
            if not entries:
                break

            for entry in entries:
                next_step = self._next_valid_step(entry)
                if next_step is None:
                    continue

                if not is_valid_tier_transition(entry.tier, next_step):
                    # Shouldn't happen, but guard anyway.
                    continue

                age = _age_days(entry)
                reason = (
                    f"Age-based tier lifecycle: entry is {age} days old, "
                    f"transitioning {entry.tier.value} -> {next_step.value}"
                )

                transitions.append(
                    TierTransition(
                        transition_id=generate_transition_id(),
                        entry_id=entry.entry_id,
                        from_tier=entry.tier,
                        to_tier=next_step,
                        reason=reason,
                    )
                )

            offset += batch_size

        # Sort by entry age descending — oldest (most urgent) first.
        # We don't have direct access to age here, but transitions for
        # colder tiers are naturally more urgent.  Re-sort by tier order
        # (cold first) then by entry_id for determinism.
        transitions.sort(
            key=lambda t: (-_TIER_ORDER[t.to_tier], t.entry_id),
        )

        logger.info(
            "Tier assessment complete: %d transitions recommended",
            len(transitions),
        )
        return transitions

    # --- Applying transitions ---

    def apply_transitions(
        self,
        memory_store: MemoryStore,
        transitions: list[TierTransition],
    ) -> list[TierTransition]:
        """Apply a list of tier transitions to *memory_store*.

        Each transition is applied individually via
        :meth:`MemoryStore.transition_tier`.  Failures are logged as
        warnings and skipped — the returned list contains only the
        transitions that were successfully applied.

        Parameters
        ----------
        memory_store : MemoryStore
            The memory store to modify.
        transitions : list[TierTransition]
            Transitions to apply (typically from :meth:`run_tier_assessment`).

        Returns
        -------
        list[TierTransition]
            Successfully applied transitions.
        """
        applied: list[TierTransition] = []

        for transition in transitions:
            try:
                result = memory_store.transition_tier(
                    entry_id=transition.entry_id,
                    new_tier=transition.to_tier,
                    reason=transition.reason,
                )
                applied.append(result)
                logger.info(
                    "Tier transition applied: %s %s -> %s (%s)",
                    transition.entry_id,
                    transition.from_tier.value,
                    transition.to_tier.value,
                    result.transition_id,
                )
            except InvalidTierTransition:
                logger.warning(
                    "Skipping invalid tier transition for %s: %s -> %s",
                    transition.entry_id,
                    transition.from_tier.value,
                    transition.to_tier.value,
                )
            except Exception:
                logger.warning(
                    "Failed to apply tier transition for %s: %s -> %s",
                    transition.entry_id,
                    transition.from_tier.value,
                    transition.to_tier.value,
                    exc_info=True,
                )

        logger.info(
            "Tier transitions applied: %d/%d succeeded",
            len(applied),
            len(transitions),
        )
        return applied

    # --- Summarisation helpers ---

    def summarize_for_cool(self, entries: list[MemoryEntry]) -> str:
        """Produce a deterministic summary suitable for COOL-tier storage.

        Extracts key decisions and artifacts, then counts remaining entries
        by type.  No LLM is used — this is pure pattern-based compression.

        Parameters
        ----------
        entries : list[MemoryEntry]
            Entries to summarise (typically all entries transitioning to cool).

        Returns
        -------
        str
            A human-readable summary string.
        """
        if not entries:
            return "No entries to summarise."

        decisions: list[str] = []
        artifacts: list[str] = []
        other_count = 0
        other_types: Counter[str] = Counter()

        for entry in entries:
            if entry.entry_type == MemoryEntryType.decision:
                # Take the first line as the decision headline.
                headline = entry.content.strip().split("\n", 1)[0]
                if headline and headline not in decisions:
                    decisions.append(headline)
            elif entry.entry_type == MemoryEntryType.artifact:
                headline = entry.content.strip().split("\n", 1)[0]
                if headline and headline not in artifacts:
                    artifacts.append(headline)
            else:
                other_count += 1
                other_types[entry.entry_type.value] += 1

        parts: list[str] = []

        if decisions:
            parts.append("Key decisions:")
            for idx, decision in enumerate(decisions, 1):
                parts.append(f"  {idx}. {decision}")

        if artifacts:
            parts.append("Artifacts:")
            for idx, artifact in enumerate(artifacts, 1):
                parts.append(f"  {idx}. {artifact}")

        if other_count > 0:
            type_summary = ", ".join(
                f"{v} {k}" for k, v in other_types.most_common()
            )
            parts.append(
                f"Plus {other_count} other entries ({type_summary})."
            )

        return "\n".join(parts) if parts else "No notable entries."

    def archive_to_cold(self, entries: list[MemoryEntry]) -> str:
        """Compress entries into a single-paragraph COLD-tier archive summary.

        Parameters
        ----------
        entries : list[MemoryEntry]
            Entries to archive (typically all entries transitioning to cold).

        Returns
        -------
        str
            A one-paragraph archive summary.
        """
        if not entries:
            return "No entries to archive."

        # Date range.
        dates = [e.created_at for e in entries]
        earliest = min(dates).strftime("%Y-%m-%d")
        latest = max(dates).strftime("%Y-%m-%d")

        # Type counts.
        type_counts: Counter[str] = Counter()
        for entry in entries:
            type_counts[entry.entry_type.value] += 1
        type_str = ", ".join(
            f"{count} {etype}" for etype, count in type_counts.most_common()
        )

        # Key decisions (up to 3).
        decisions: list[str] = []
        for entry in entries:
            if entry.entry_type == MemoryEntryType.decision:
                headline = entry.content.strip().split("\n", 1)[0]
                if headline and headline not in decisions:
                    decisions.append(headline)
                if len(decisions) >= 3:
                    break

        decisions_str = "; ".join(decisions) if decisions else "none recorded"

        return (
            f"{len(entries)} memory entries from {earliest} to {latest}. "
            f"Types: {type_str}. "
            f"Key decisions: {decisions_str}."
        )

    # --- Statistics & reporting ---

    def get_tier_stats(self, memory_store: MemoryStore) -> dict:
        """Get tier-focused memory statistics.

        Restructures the output of :meth:`MemoryStore.get_stats` into a
        per-tier breakdown with count and byte totals.

        Parameters
        ----------
        memory_store : MemoryStore
            The memory store to query.

        Returns
        -------
        dict
            ``{hot: {count, bytes}, warm: …, cool: …, cold: …,
            total: {count, bytes}}``.
        """
        result: dict = {}
        total_count = 0
        total_bytes = 0

        for tier in MemoryTier:
            entries = memory_store.list_entries(tier=tier, limit=10_000)
            count = len(entries)
            byte_total = sum(e.byte_size for e in entries)
            result[tier.value] = {"count": count, "bytes": byte_total}
            total_count += count
            total_bytes += byte_total

        result["total"] = {"count": total_count, "bytes": total_bytes}
        return result

    def get_aging_report(self, memory_store: MemoryStore) -> list[dict]:
        """Identify entries approaching or past their tier transition threshold.

        For each entry in hot, warm, or cool tier, calculates how many days
        remain until the next tier threshold is reached.  Entries already
        past their threshold have a negative ``days_until_transition``.

        Parameters
        ----------
        memory_store : MemoryStore
            The memory store to query.

        Returns
        -------
        list[dict]
            Sorted by ``days_until_transition`` ascending (most urgent
            first).  Each dict contains: ``entry_id``, ``current_tier``,
            ``recommended_tier``, ``days_until_transition``, ``age_days``.
        """
        report: list[dict] = []

        # Only scan tiers that have a possible next step.
        for tier in (MemoryTier.hot, MemoryTier.warm, MemoryTier.cool):
            next_tier = _NEXT_TIER[tier]
            if next_tier is None:
                continue

            threshold = _threshold_for_tier(
                next_tier,
                self._warm_age_days,
                self._cool_age_days,
                self._cold_age_days,
            )
            if threshold is None:
                continue

            # Paginate through all entries in this tier.
            offset = 0
            batch_size = 200
            while True:
                entries = memory_store.list_entries(
                    tier=tier, offset=offset, limit=batch_size,
                )
                if not entries:
                    break

                for entry in entries:
                    age = _age_days(entry)
                    days_until = threshold - age
                    recommended = self.assess_tier(entry)

                    report.append({
                        "entry_id": entry.entry_id,
                        "current_tier": tier.value,
                        "recommended_tier": recommended.value,
                        "days_until_transition": days_until,
                        "age_days": age,
                    })

                offset += batch_size

        # Sort: most urgent (smallest days_until_transition) first.
        report.sort(key=lambda r: (r["days_until_transition"], r["entry_id"]))
        return report


# --- Public API ---

__all__ = [
    "TieredStorage",
]
