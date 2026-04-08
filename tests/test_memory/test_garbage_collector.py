"""Comprehensive tests for the GarbageCollector class.

Tests cover initialization, full GC cycle (run_gc), budget enforcement
(enforce_budget), stale-worker cleanup, GC report generation, and
internal purge helpers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.memory.exceptions import GarbageCollectionError
from core.memory.garbage_collector import GarbageCollector, _PURGEABLE_TIERS
from core.memory.memory_store import MemoryStore
from core.memory.models import (
    GCReport,
    MemoryBudget,
    MemoryEntry,
    MemoryEntryType,
    MemoryScope,
    MemoryTier,
    TierTransition,
    generate_memory_id,
    generate_transition_id,
)
from core.memory.tiered_storage import TieredStorage


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_entry(
    scope: MemoryScope = MemoryScope.strategic,
    tier: MemoryTier = MemoryTier.hot,
    entry_type: MemoryEntryType = MemoryEntryType.decision,
    content: str = "Test content",
    entry_id: str = "",
    created_at: datetime | None = None,
    accessed_at: datetime | None = None,
) -> MemoryEntry:
    """Create a MemoryEntry with optional overrides."""
    kwargs: dict = {
        "entry_id": entry_id or generate_memory_id(),
        "profile_name": "ceo",
        "scope": scope,
        "tier": tier,
        "entry_type": entry_type,
        "content": content,
    }
    if created_at is not None:
        kwargs["created_at"] = created_at
    if accessed_at is not None:
        kwargs["accessed_at"] = accessed_at
    return MemoryEntry(**kwargs)


def _make_store_with_entries(
    count: int = 5,
    tier: MemoryTier = MemoryTier.hot,
    content_prefix: str = "Entry",
) -> tuple[MemoryStore, list[MemoryEntry]]:
    """Create an in-memory MemoryStore with *count* entries in *tier*."""
    store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
    entries: list[MemoryEntry] = []
    for i in range(count):
        entry = _make_entry(
            content=f"{content_prefix} {i}: some test content for entry {i}",
            tier=tier,
        )
        stored = store.store(entry)
        entries.append(stored)
    return store, entries


def _make_cold_store(
    count: int = 5,
) -> tuple[MemoryStore, list[MemoryEntry]]:
    """Create a store with entries transitioned to COLD."""
    store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
    entries: list[MemoryEntry] = []
    for i in range(count):
        entry = _make_entry(
            content=f"Cold entry {i}: content for cold entry {i}",
        )
        stored = store.store(entry)
        # Transition through hot -> warm -> cool -> cold
        store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
        store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
        store.transition_tier(stored.entry_id, MemoryTier.cold, "age")
        # Re-fetch to get updated tier
        stored = store.get(stored.entry_id)
        entries.append(stored)
    return store, entries


def _make_cool_store(
    count: int = 5,
) -> tuple[MemoryStore, list[MemoryEntry]]:
    """Create a store with entries transitioned to COOL."""
    store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
    entries: list[MemoryEntry] = []
    for i in range(count):
        entry = _make_entry(
            content=f"Cool entry {i}: content for cool entry {i}",
        )
        stored = store.store(entry)
        store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
        store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
        stored = store.get(stored.entry_id)
        entries.append(stored)
    return store, entries


# ==================================================================
# TestGarbageCollectorInit
# ==================================================================


class TestGarbageCollectorInit:
    """Tests for GarbageCollector construction."""

    def test_creation_with_default_tiered_storage(self):
        gc = GarbageCollector()
        assert gc._tiered_storage is not None
        assert isinstance(gc._tiered_storage, TieredStorage)

    def test_creation_with_custom_tiered_storage(self):
        ts = TieredStorage(warm_age_days=1, cool_age_days=2, cold_age_days=3)
        gc = GarbageCollector(tiered_storage=ts)
        assert gc._tiered_storage is ts

    def test_creation_with_none_creates_default(self):
        gc = GarbageCollector(tiered_storage=None)
        assert isinstance(gc._tiered_storage, TieredStorage)

    def test_purgeable_tiers_order(self):
        """COLD should be purged before COOL."""
        assert _PURGEABLE_TIERS == [MemoryTier.cold, MemoryTier.cool]


# ==================================================================
# TestRunGC
# ==================================================================


class TestRunGC:
    """Tests for GarbageCollector.run_gc()."""

    def test_full_gc_cycle_returns_gc_report(self, memory_store):
        gc = GarbageCollector()
        report = gc.run_gc(memory_store)
        assert isinstance(report, GCReport)

    def test_full_gc_dry_run_no_mutations(self, memory_store):
        # Store some entries first
        entry = _make_entry()
        memory_store.store(entry)

        gc = GarbageCollector()
        report = gc.run_gc(memory_store, dry_run=True)
        assert report.dry_run is True
        # Entries should still be there
        entries = memory_store.list_entries()
        assert len(entries) == 1

    def test_gc_with_no_entries(self, memory_store):
        gc = GarbageCollector()
        report = gc.run_gc(memory_store)
        assert report.entries_transitioned == 0
        assert report.entries_purged == 0

    def test_gc_no_transitions_needed(self, memory_store):
        """Fresh HOT entries should not need transitions."""
        entry = _make_entry()
        memory_store.store(entry)

        gc = GarbageCollector()
        report = gc.run_gc(memory_store)
        # Fresh entries are HOT; no age-based transition expected
        assert isinstance(report.entries_transitioned, int)

    def test_gc_report_has_budget_status(self, memory_store):
        gc = GarbageCollector()
        report = gc.run_gc(memory_store)
        assert "exceeded" in report.budget_status

    def test_gc_raises_on_unexpected_error(self, memory_store):
        """Unexpected errors are wrapped in GarbageCollectionError."""
        ts = MagicMock(spec=TieredStorage)
        ts.run_tier_assessment.side_effect = RuntimeError("boom")
        gc = GarbageCollector(tiered_storage=ts)
        with pytest.raises(GarbageCollectionError, match="boom"):
            gc.run_gc(memory_store)

    def test_gc_with_mocked_transitions(self, memory_store):
        """Verify transitions are applied when not dry_run."""
        entry = _make_entry()
        stored = memory_store.store(entry)

        # Mock the tiered storage to suggest a transition
        ts = MagicMock(spec=TieredStorage)
        transition = TierTransition(
            transition_id=generate_transition_id(),
            entry_id=stored.entry_id,
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="test",
        )
        ts.run_tier_assessment.return_value = [transition]
        ts.apply_transitions.return_value = [transition]

        gc = GarbageCollector(tiered_storage=ts)
        report = gc.run_gc(memory_store, dry_run=False)

        assert report.entries_transitioned == 1
        assert report.dry_run is False
        ts.apply_transitions.assert_called_once()

    def test_gc_dry_run_does_not_apply_transitions(self, memory_store):
        """Verify transitions are NOT applied when dry_run=True."""
        ts = MagicMock(spec=TieredStorage)
        transition = TierTransition(
            transition_id=generate_transition_id(),
            entry_id="mem-fake1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="test",
        )
        ts.run_tier_assessment.return_value = [transition]

        gc = GarbageCollector(tiered_storage=ts)
        report = gc.run_gc(memory_store, dry_run=True)

        assert report.dry_run is True
        assert report.entries_transitioned == 1  # reported but not applied
        ts.apply_transitions.assert_not_called()

    def test_gc_report_transitions_list(self, memory_store):
        """Report includes the transition objects."""
        entry = _make_entry()
        stored = memory_store.store(entry)

        ts = MagicMock(spec=TieredStorage)
        transition = TierTransition(
            transition_id=generate_transition_id(),
            entry_id=stored.entry_id,
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="age-based",
        )
        ts.run_tier_assessment.return_value = [transition]
        ts.apply_transitions.return_value = [transition]

        gc = GarbageCollector(tiered_storage=ts)
        report = gc.run_gc(memory_store, dry_run=False)

        assert len(report.transitions) == 1
        assert report.transitions[0].entry_id == stored.entry_id


# ==================================================================
# TestEnforceBudget
# ==================================================================


class TestEnforceBudget:
    """Tests for GarbageCollector.enforce_budget()."""

    def test_budget_not_exceeded_returns_empty(self, memory_store):
        budget = MemoryBudget(profile_name="ceo", max_entries=100, max_bytes=100_000)
        memory_store.set_budget(budget)
        entry = _make_entry()
        memory_store.store(entry)

        gc = GarbageCollector()
        purged = gc.enforce_budget(memory_store)
        assert purged == []

    def test_no_budget_set_returns_empty(self, memory_store):
        entry = _make_entry()
        memory_store.store(entry)

        gc = GarbageCollector()
        purged = gc.enforce_budget(memory_store)
        assert purged == []

    def test_budget_exceeded_purges_cold_first(self):
        """When budget is exceeded, COLD entries are purged before COOL."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            # Create 3 entries, transition them to COLD
            cold_ids = []
            for i in range(3):
                entry = _make_entry(content=f"Cold entry {i}")
                stored = store.store(entry)
                store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cold, "age")
                cold_ids.append(stored.entry_id)

            # Create 2 entries at HOT (should NOT be purged)
            hot_ids = []
            for i in range(2):
                entry = _make_entry(content=f"Hot entry {i}")
                stored = store.store(entry)
                hot_ids.append(stored.entry_id)

            # Set tight budget: max 3 entries (we have 5)
            budget = MemoryBudget(
                profile_name="ceo", max_entries=3, max_bytes=10_000_000,
            )
            store.set_budget(budget)

            gc = GarbageCollector()
            purged = gc.enforce_budget(store)

            # Should purge 2 COLD entries (5 - 3 = 2 excess)
            assert len(purged) == 2
            # All purged entries should be from cold_ids
            for pid in purged:
                assert pid in cold_ids
            # HOT entries should still exist
            for hid in hot_ids:
                entry = store.get(hid)
                assert entry.tier == MemoryTier.hot
        finally:
            store.close()

    def test_budget_exceeded_purges_cool_after_cold(self):
        """When COLD entries are exhausted, COOL entries are purged next."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            # Create 1 COLD entry
            entry = _make_entry(content="The one cold entry")
            stored = store.store(entry)
            store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
            store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
            store.transition_tier(stored.entry_id, MemoryTier.cold, "age")

            # Create 3 COOL entries
            cool_ids = []
            for i in range(3):
                entry = _make_entry(content=f"Cool entry {i}")
                stored = store.store(entry)
                store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
                cool_ids.append(stored.entry_id)

            # Create 1 HOT entry
            entry = _make_entry(content="One hot entry")
            hot_stored = store.store(entry)

            # Total 5 entries; set budget to 2 => need to purge 3
            budget = MemoryBudget(
                profile_name="ceo", max_entries=2, max_bytes=10_000_000,
            )
            store.set_budget(budget)

            gc = GarbageCollector()
            purged = gc.enforce_budget(store)

            # Should purge 3: 1 COLD + 2 COOL
            assert len(purged) == 3
            # HOT entry must survive
            remaining = store.get(hot_stored.entry_id)
            assert remaining.tier == MemoryTier.hot
        finally:
            store.close()

    def test_never_purges_hot_entries(self):
        """HOT entries are never touched by budget enforcement."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            # Create 5 HOT entries
            hot_ids = []
            for i in range(5):
                entry = _make_entry(content=f"Hot entry {i}")
                stored = store.store(entry)
                hot_ids.append(stored.entry_id)

            # Set a budget of 2 entries (exceeded by 3)
            budget = MemoryBudget(
                profile_name="ceo", max_entries=2, max_bytes=10_000_000,
            )
            store.set_budget(budget)

            gc = GarbageCollector()
            purged = gc.enforce_budget(store)

            # No entries purged because all are HOT (not purgeable)
            assert purged == []
            # All 5 entries still exist
            remaining = store.list_entries(limit=100)
            assert len(remaining) == 5
        finally:
            store.close()

    def test_never_purges_warm_entries(self):
        """WARM entries are never touched by budget enforcement."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            # Create 5 WARM entries
            for i in range(5):
                entry = _make_entry(content=f"Warm entry {i}")
                stored = store.store(entry)
                store.transition_tier(stored.entry_id, MemoryTier.warm, "age")

            budget = MemoryBudget(
                profile_name="ceo", max_entries=2, max_bytes=10_000_000,
            )
            store.set_budget(budget)

            gc = GarbageCollector()
            purged = gc.enforce_budget(store)
            assert purged == []
        finally:
            store.close()

    def test_dry_run_does_not_delete(self):
        """Dry run identifies entries but does not delete them."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            # Create 3 COLD entries
            for i in range(3):
                entry = _make_entry(content=f"Cold entry {i}")
                stored = store.store(entry)
                store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cold, "age")

            budget = MemoryBudget(
                profile_name="ceo", max_entries=1, max_bytes=10_000_000,
            )
            store.set_budget(budget)

            gc = GarbageCollector()
            purged = gc.enforce_budget(store, dry_run=True)

            # Purge list should be non-empty (would purge 2)
            assert len(purged) == 2
            # But entries should still exist
            remaining = store.list_entries(limit=100)
            assert len(remaining) == 3
        finally:
            store.close()

    def test_budget_exceeded_by_bytes(self):
        """Budget enforcement works when byte budget is exceeded."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            # Create 2 COLD entries with large content
            for i in range(2):
                content = "x" * 500
                entry = _make_entry(content=content)
                stored = store.store(entry)
                store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cold, "age")

            # Set byte budget smaller than total
            budget = MemoryBudget(
                profile_name="ceo", max_entries=10_000, max_bytes=100,
            )
            store.set_budget(budget)

            gc = GarbageCollector()
            purged = gc.enforce_budget(store)
            # At least some entries should be purged
            assert len(purged) >= 1
        finally:
            store.close()


# ==================================================================
# TestCleanupCompletedWorkers
# ==================================================================


class TestCleanupCompletedWorkers:
    """Tests for GarbageCollector.cleanup_completed_workers()."""

    def test_no_registry(self):
        """An object without list_subagents returns empty list."""
        gc = GarbageCollector()
        results = gc.cleanup_completed_workers(object())
        assert results == []

    def test_duck_typed_registry_with_completed_workers(self):
        """Registry with completed workers older than threshold."""
        gc = GarbageCollector()

        # Create a simple mock registry
        old_time = datetime.now(timezone.utc) - timedelta(days=60)

        class FakeAgent:
            def __init__(self, sid, status, completed_at):
                self.subagent_id = sid
                self.status = status
                self.completed_at = completed_at

        class FakeRegistry:
            def list_subagents(self):
                return [
                    FakeAgent("w-1", "completed", old_time),
                    FakeAgent("w-2", "active", None),
                    FakeAgent("w-3", "completed", old_time - timedelta(days=10)),
                ]

        results = gc.cleanup_completed_workers(FakeRegistry(), days_threshold=30)
        assert len(results) == 2
        ids = {r["subagent_id"] for r in results}
        assert "w-1" in ids
        assert "w-3" in ids
        assert all(r["action"] == "archived" for r in results)

    def test_completed_but_too_recent(self):
        """Completed workers under the threshold are not archived."""
        gc = GarbageCollector()

        recent_time = datetime.now(timezone.utc) - timedelta(days=5)

        class FakeAgent:
            def __init__(self):
                self.subagent_id = "w-recent"
                self.status = "completed"
                self.completed_at = recent_time

        class FakeRegistry:
            def list_subagents(self):
                return [FakeAgent()]

        results = gc.cleanup_completed_workers(FakeRegistry(), days_threshold=30)
        assert results == []

    def test_custom_threshold(self):
        """Custom days_threshold is respected."""
        gc = GarbageCollector()

        agent_time = datetime.now(timezone.utc) - timedelta(days=10)

        class FakeAgent:
            def __init__(self):
                self.subagent_id = "w-custom"
                self.status = "completed"
                self.completed_at = agent_time

        class FakeRegistry:
            def list_subagents(self):
                return [FakeAgent()]

        results = gc.cleanup_completed_workers(FakeRegistry(), days_threshold=5)
        assert len(results) == 1
        assert results[0]["age_days"] >= 10

    def test_registry_with_no_completed(self):
        """Registry with only active agents returns empty list."""
        gc = GarbageCollector()

        class FakeAgent:
            def __init__(self):
                self.subagent_id = "w-active"
                self.status = "active"
                self.completed_at = None

        class FakeRegistry:
            def list_subagents(self):
                return [FakeAgent()]

        results = gc.cleanup_completed_workers(FakeRegistry())
        assert results == []

    def test_registry_raises_exception(self):
        """If list_subagents raises, return empty list gracefully."""
        gc = GarbageCollector()

        class BrokenRegistry:
            def list_subagents(self):
                raise RuntimeError("db unavailable")

        results = gc.cleanup_completed_workers(BrokenRegistry())
        assert results == []

    def test_naive_datetime_handled(self):
        """Naive datetimes (no tzinfo) are treated as UTC."""
        gc = GarbageCollector()

        # Naive datetime (no timezone)
        naive_time = datetime.utcnow() - timedelta(days=60)

        class FakeAgent:
            def __init__(self):
                self.subagent_id = "w-naive"
                self.status = "completed"
                self.completed_at = naive_time

        class FakeRegistry:
            def list_subagents(self):
                return [FakeAgent()]

        results = gc.cleanup_completed_workers(FakeRegistry(), days_threshold=30)
        assert len(results) == 1


# ==================================================================
# TestGetGCReport
# ==================================================================


class TestGetGCReport:
    """Tests for GarbageCollector.get_gc_report()."""

    def test_analysis_only_no_side_effects(self, memory_store):
        entry = _make_entry()
        memory_store.store(entry)

        gc = GarbageCollector()
        report = gc.get_gc_report(memory_store)

        assert report.dry_run is True
        assert report.entries_purged == 0
        assert report.bytes_freed == 0
        # Entry should still exist
        entries = memory_store.list_entries()
        assert len(entries) == 1

    def test_report_includes_budget_status(self, memory_store):
        gc = GarbageCollector()
        report = gc.get_gc_report(memory_store)
        assert "exceeded" in report.budget_status

    def test_recommendations_generated_for_transitions(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)

        ts = MagicMock(spec=TieredStorage)
        transition = TierTransition(
            transition_id=generate_transition_id(),
            entry_id=stored.entry_id,
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="age-based",
        )
        ts.run_tier_assessment.return_value = [transition]

        gc = GarbageCollector(tiered_storage=ts)
        report = gc.get_gc_report(memory_store)

        assert any("transition" in r.lower() for r in report.recommendations)

    def test_recommendations_for_budget_exceeded(self):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            for i in range(5):
                entry = _make_entry(content=f"Entry {i}")
                store.store(entry)
            budget = MemoryBudget(
                profile_name="ceo", max_entries=2, max_bytes=10_000_000,
            )
            store.set_budget(budget)

            gc = GarbageCollector()
            report = gc.get_gc_report(store)
            # Should have a budget recommendation
            assert any("budget" in r.lower() or "exceeded" in r.lower()
                        for r in report.recommendations)
        finally:
            store.close()

    def test_report_serializable(self, memory_store):
        gc = GarbageCollector()
        report = gc.get_gc_report(memory_store)
        d = report.to_dict()
        assert isinstance(d, dict)
        assert "entries_transitioned" in d
        assert "recommendations" in d

    def test_raises_gc_error_on_unexpected_failure(self, memory_store):
        ts = MagicMock(spec=TieredStorage)
        ts.run_tier_assessment.side_effect = ValueError("unexpected")
        gc = GarbageCollector(tiered_storage=ts)
        with pytest.raises(GarbageCollectionError, match="unexpected"):
            gc.get_gc_report(memory_store)

    def test_cold_entries_reported(self):
        """Cold entries appear in recommendations."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            # Create entries and transition to COLD
            for i in range(3):
                entry = _make_entry(content=f"Archive me {i}")
                stored = store.store(entry)
                store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cold, "age")

            gc = GarbageCollector()
            report = gc.get_gc_report(store)
            assert any("COLD" in r for r in report.recommendations)
        finally:
            store.close()


# ==================================================================
# TestPurgeHelpers
# ==================================================================


class TestPurgeHelpers:
    """Tests for _purge_entries_by_tier and _calculate_purge_needed."""

    def test_purge_entries_by_tier(self):
        """Directly test _purge_entries_by_tier removes oldest entries."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            # Create COLD entries
            for i in range(5):
                entry = _make_entry(content=f"Purge test {i}")
                stored = store.store(entry)
                store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cold, "age")

            gc = GarbageCollector()
            deleted = gc._purge_entries_by_tier(store, MemoryTier.cold, 3)

            assert len(deleted) == 3
            # 2 entries should remain
            remaining = store.list_entries(tier=MemoryTier.cold, limit=100)
            assert len(remaining) == 2
        finally:
            store.close()

    def test_purge_entries_more_than_available(self):
        """Requesting more purges than entries available."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            for i in range(2):
                entry = _make_entry(content=f"Purge {i}")
                stored = store.store(entry)
                store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cold, "age")

            gc = GarbageCollector()
            deleted = gc._purge_entries_by_tier(store, MemoryTier.cold, 10)
            assert len(deleted) == 2
        finally:
            store.close()

    def test_purge_empty_tier(self, memory_store):
        """Purging from an empty tier returns empty list."""
        gc = GarbageCollector()
        deleted = gc._purge_entries_by_tier(memory_store, MemoryTier.cold, 5)
        assert deleted == []

    def test_calculate_purge_needed_entries_exceeded(self):
        gc = GarbageCollector()
        entries_to_purge, bytes_to_free = gc._calculate_purge_needed(
            usage={"entries": 150, "bytes": 500},
            limits={"max_entries": 100, "max_bytes": 1000},
        )
        assert entries_to_purge == 50
        assert bytes_to_free == 0  # bytes not exceeded

    def test_calculate_purge_needed_bytes_exceeded(self):
        gc = GarbageCollector()
        entries_to_purge, bytes_to_free = gc._calculate_purge_needed(
            usage={"entries": 50, "bytes": 1500},
            limits={"max_entries": 100, "max_bytes": 1000},
        )
        assert entries_to_purge == 0
        assert bytes_to_free == 500

    def test_calculate_purge_needed_both_exceeded(self):
        gc = GarbageCollector()
        entries_to_purge, bytes_to_free = gc._calculate_purge_needed(
            usage={"entries": 200, "bytes": 2000},
            limits={"max_entries": 100, "max_bytes": 1000},
        )
        assert entries_to_purge == 100
        assert bytes_to_free == 1000

    def test_calculate_purge_needed_nothing_exceeded(self):
        gc = GarbageCollector()
        entries_to_purge, bytes_to_free = gc._calculate_purge_needed(
            usage={"entries": 50, "bytes": 500},
            limits={"max_entries": 100, "max_bytes": 1000},
        )
        assert entries_to_purge == 0
        assert bytes_to_free == 0

    def test_calculate_purge_needed_none_limits(self):
        gc = GarbageCollector()
        entries_to_purge, bytes_to_free = gc._calculate_purge_needed(
            usage={"entries": 50, "bytes": 500},
            limits={"max_entries": None, "max_bytes": None},
        )
        assert entries_to_purge == 0
        assert bytes_to_free == 0

    def test_get_purgeable_entries(self):
        """_get_purgeable_entries returns (entry_id, byte_size) tuples."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        try:
            for i in range(3):
                entry = _make_entry(content=f"Candidate {i}")
                stored = store.store(entry)
                store.transition_tier(stored.entry_id, MemoryTier.warm, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cool, "age")
                store.transition_tier(stored.entry_id, MemoryTier.cold, "age")

            gc = GarbageCollector()
            candidates = gc._get_purgeable_entries(store, MemoryTier.cold, 5)
            assert len(candidates) == 3
            for entry_id, byte_size in candidates:
                assert isinstance(entry_id, str)
                assert isinstance(byte_size, int)
                assert byte_size > 0
        finally:
            store.close()


# ==================================================================
# TestBuildRecommendations
# ==================================================================


class TestBuildRecommendations:
    """Tests for GarbageCollector._build_recommendations()."""

    def test_no_recommendations_when_healthy(self):
        gc = GarbageCollector()
        recs = gc._build_recommendations(
            transitions=[],
            budget_status={"exceeded": False, "usage": {}, "limits": {}},
        )
        assert recs == []

    def test_transition_recommendation(self):
        gc = GarbageCollector()
        transition = TierTransition(
            transition_id=generate_transition_id(),
            entry_id="mem-test1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="age-based",
        )
        recs = gc._build_recommendations(
            transitions=[transition],
            budget_status={"exceeded": False},
        )
        assert len(recs) == 1
        assert "transition" in recs[0].lower()

    def test_budget_recommendation(self):
        gc = GarbageCollector()
        recs = gc._build_recommendations(
            transitions=[],
            budget_status={
                "exceeded": True,
                "usage": {"entries": 200, "bytes": 2000},
                "limits": {"max_entries": 100, "max_bytes": 1000},
            },
        )
        assert len(recs) >= 1
        assert any("budget" in r.lower() or "exceeded" in r.lower() for r in recs)
