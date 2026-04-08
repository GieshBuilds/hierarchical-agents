"""Comprehensive tests for the TieredStorage class.

Tests cover tier assessment, bulk assessment, transition application,
summarization, archiving, statistics, and aging reports.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.memory.exceptions import InvalidTierTransition, MemoryEntryNotFound
from core.memory.memory_store import MemoryStore
from core.memory.models import (
    COLD_AGE_DAYS,
    COOL_AGE_DAYS,
    WARM_AGE_DAYS,
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
    content: str = "Test content",
    tier: MemoryTier = MemoryTier.hot,
    entry_type: MemoryEntryType = MemoryEntryType.decision,
    created_at: datetime | None = None,
) -> MemoryEntry:
    """Create a MemoryEntry with optional overrides."""
    kwargs: dict = {
        "entry_id": generate_memory_id(),
        "profile_name": "ceo",
        "scope": MemoryScope.strategic,
        "tier": tier,
        "entry_type": entry_type,
        "content": content,
    }
    if created_at is not None:
        kwargs["created_at"] = created_at
    return MemoryEntry(**kwargs)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aged(days: int) -> datetime:
    """Return a datetime that is *days* ago."""
    return _now() - timedelta(days=days)


def _store_aged_entry(
    store: MemoryStore,
    days_old: int,
    tier: MemoryTier = MemoryTier.hot,
    entry_type: MemoryEntryType = MemoryEntryType.decision,
    content: str = "Aged entry",
) -> MemoryEntry:
    """Store an entry with a created_at timestamp days_old days ago."""
    entry = _make_entry(
        content=content,
        tier=tier,
        entry_type=entry_type,
        created_at=_aged(days_old),
    )
    return store.store(entry)


# ==================================================================
# TestAssessTier
# ==================================================================


class TestAssessTier:
    """Tests for TieredStorage.assess_tier()."""

    def test_hot_stays_hot_recent(self):
        """A recently created entry stays hot when warm_age_days > 0."""
        # Default WARM_AGE_DAYS=0 means age 0 qualifies as warm (by design).
        # Use warm_age_days=1 so a brand-new entry remains hot.
        ts = TieredStorage(warm_age_days=1, cool_age_days=30, cold_age_days=90)
        entry = _make_entry(created_at=_now())
        result = ts.assess_tier(entry)
        assert result == MemoryTier.hot

    def test_warm_age(self, tiered_storage):
        """Entry at WARM_AGE_DAYS should assess as warm (or beyond)."""
        entry = _make_entry(created_at=_aged(WARM_AGE_DAYS + 1))
        result = tiered_storage.assess_tier(entry)
        # WARM_AGE_DAYS is 0, so even age 1 qualifies for warm+
        assert result in (MemoryTier.warm, MemoryTier.cool, MemoryTier.cold)

    def test_cool_age(self, tiered_storage):
        """Entry at COOL_AGE_DAYS should assess as cool or colder."""
        entry = _make_entry(created_at=_aged(COOL_AGE_DAYS + 1))
        result = tiered_storage.assess_tier(entry)
        assert result in (MemoryTier.cool, MemoryTier.cold)

    def test_cold_age(self, tiered_storage):
        """Entry at COLD_AGE_DAYS should assess as cold."""
        entry = _make_entry(created_at=_aged(COLD_AGE_DAYS + 1))
        result = tiered_storage.assess_tier(entry)
        assert result == MemoryTier.cold

    def test_no_backward_transition(self, tiered_storage):
        """Even a recently-created COLD entry should stay cold."""
        entry = _make_entry(tier=MemoryTier.cold, created_at=_now())
        result = tiered_storage.assess_tier(entry)
        assert result == MemoryTier.cold

    def test_no_backward_warm_to_hot(self, tiered_storage):
        """A warm entry should never be recommended to go back to hot."""
        entry = _make_entry(tier=MemoryTier.warm, created_at=_now())
        result = tiered_storage.assess_tier(entry)
        assert result != MemoryTier.hot

    def test_no_backward_cool_to_warm(self, tiered_storage):
        """A cool entry should never be recommended to go back to warm."""
        entry = _make_entry(tier=MemoryTier.cool, created_at=_now())
        result = tiered_storage.assess_tier(entry)
        assert result in (MemoryTier.cool, MemoryTier.cold)

    def test_very_old_entry(self, tiered_storage):
        """A 365-day-old hot entry should be assessed as cold."""
        entry = _make_entry(created_at=_aged(365))
        result = tiered_storage.assess_tier(entry)
        assert result == MemoryTier.cold

    def test_custom_thresholds(self):
        """TieredStorage with custom thresholds."""
        ts = TieredStorage(warm_age_days=5, cool_age_days=15, cold_age_days=30)
        # 3 days old — still hot with warm=5
        entry = _make_entry(created_at=_aged(3))
        assert ts.assess_tier(entry) == MemoryTier.hot

        # 6 days old — warm
        entry = _make_entry(created_at=_aged(6))
        result = ts.assess_tier(entry)
        assert result in (MemoryTier.warm, MemoryTier.cool, MemoryTier.cold)

    def test_exact_boundary_warm(self):
        """Entry exactly at warm threshold."""
        ts = TieredStorage(warm_age_days=7, cool_age_days=30, cold_age_days=90)
        entry = _make_entry(created_at=_aged(7))
        result = ts.assess_tier(entry)
        assert result in (MemoryTier.warm, MemoryTier.cool, MemoryTier.cold)

    def test_entry_at_same_tier(self, tiered_storage):
        """Entry whose assessed tier matches current returns current."""
        entry = _make_entry(tier=MemoryTier.cold, created_at=_aged(COLD_AGE_DAYS + 10))
        result = tiered_storage.assess_tier(entry)
        assert result == MemoryTier.cold


# ==================================================================
# TestRunTierAssessment
# ==================================================================


class TestRunTierAssessment:
    """Tests for TieredStorage.run_tier_assessment()."""

    def test_finds_entries_needing_transition(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        # Store an entry that's 3 days old (warm threshold = 1)
        _store_aged_entry(store, 3, tier=MemoryTier.hot)
        transitions = fast_tiered_storage.run_tier_assessment(store)
        assert len(transitions) >= 1
        assert transitions[0].from_tier == MemoryTier.hot
        assert transitions[0].to_tier == MemoryTier.warm
        store.close()

    def test_skips_current_tier_entries(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        # Store a freshly created hot entry (age 0, threshold 1)
        _store_aged_entry(store, 0, tier=MemoryTier.hot)
        transitions = fast_tiered_storage.run_tier_assessment(store)
        assert len(transitions) == 0
        store.close()

    def test_empty_store(self, tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        transitions = tiered_storage.run_tier_assessment(store)
        assert transitions == []
        store.close()

    def test_multiple_entries(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        # 5 entries, 3 days old (should transition hot->warm)
        for i in range(5):
            _store_aged_entry(store, 3, content=f"Entry {i}")
        transitions = fast_tiered_storage.run_tier_assessment(store)
        assert len(transitions) == 5
        store.close()

    def test_mixed_ages(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        # One fresh (stays hot), one 3-day-old (needs warm)
        _store_aged_entry(store, 0, content="Fresh")
        _store_aged_entry(store, 3, content="Aged")
        transitions = fast_tiered_storage.run_tier_assessment(store)
        assert len(transitions) == 1
        store.close()

    def test_single_step_only(self, fast_tiered_storage):
        """Even if entry is old enough for cold, only one step at a time."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        # 15 days old with warm=1, cool=5, cold=10 — should go hot->warm
        _store_aged_entry(store, 15, tier=MemoryTier.hot)
        transitions = fast_tiered_storage.run_tier_assessment(store)
        assert len(transitions) == 1
        assert transitions[0].to_tier == MemoryTier.warm
        store.close()

    def test_returns_tier_transition_objects(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        _store_aged_entry(store, 3)
        transitions = fast_tiered_storage.run_tier_assessment(store)
        for t in transitions:
            assert isinstance(t, TierTransition)
            assert t.transition_id.startswith("tt-")
        store.close()


# ==================================================================
# TestApplyTransitions
# ==================================================================


class TestApplyTransitions:
    """Tests for TieredStorage.apply_transitions()."""

    def test_successful_apply(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        _store_aged_entry(store, 3)
        transitions = fast_tiered_storage.run_tier_assessment(store)
        applied = fast_tiered_storage.apply_transitions(store, transitions)
        assert len(applied) == 1
        assert applied[0].to_tier == MemoryTier.warm
        store.close()

    def test_entry_tier_updated(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        stored = _store_aged_entry(store, 3)
        transitions = fast_tiered_storage.run_tier_assessment(store)
        fast_tiered_storage.apply_transitions(store, transitions)
        retrieved = store.get(stored.entry_id)
        assert retrieved.tier == MemoryTier.warm
        store.close()

    def test_failed_transitions_skipped(self, tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        stored = _store_aged_entry(store, 5)
        # Create an invalid transition (hot -> cool, skipping warm)
        fake_transition = TierTransition(
            transition_id=generate_transition_id(),
            entry_id=stored.entry_id,
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.cool,
            reason="Invalid skip",
        )
        applied = tiered_storage.apply_transitions(store, [fake_transition])
        assert len(applied) == 0
        store.close()

    def test_nonexistent_entry_skipped(self, tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        fake_transition = TierTransition(
            transition_id=generate_transition_id(),
            entry_id="mem-nonexistent",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="Missing entry",
        )
        applied = tiered_storage.apply_transitions(store, [fake_transition])
        assert len(applied) == 0
        store.close()

    def test_empty_transitions_list(self, tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        applied = tiered_storage.apply_transitions(store, [])
        assert applied == []
        store.close()

    def test_multiple_apply(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        for i in range(3):
            _store_aged_entry(store, 3, content=f"Entry {i}")
        transitions = fast_tiered_storage.run_tier_assessment(store)
        applied = fast_tiered_storage.apply_transitions(store, transitions)
        assert len(applied) == 3
        store.close()


# ==================================================================
# TestSummarizeForCool
# ==================================================================


class TestSummarizeForCool:
    """Tests for TieredStorage.summarize_for_cool()."""

    def test_decision_extraction(self, tiered_storage):
        entries = [
            _make_entry(
                content="Use PostgreSQL for primary database",
                entry_type=MemoryEntryType.decision,
            ),
            _make_entry(
                content="Adopt microservice architecture",
                entry_type=MemoryEntryType.decision,
            ),
        ]
        summary = tiered_storage.summarize_for_cool(entries)
        assert "Key decisions:" in summary
        assert "Use PostgreSQL" in summary
        assert "Adopt microservice" in summary

    def test_artifact_extraction(self, tiered_storage):
        entries = [
            _make_entry(
                content="API specification v2.0",
                entry_type=MemoryEntryType.artifact,
            ),
        ]
        summary = tiered_storage.summarize_for_cool(entries)
        assert "Artifacts:" in summary
        assert "API specification" in summary

    def test_mixed_content(self, tiered_storage):
        entries = [
            _make_entry(content="Decision A", entry_type=MemoryEntryType.decision),
            _make_entry(content="Artifact B", entry_type=MemoryEntryType.artifact),
            _make_entry(content="Context C", entry_type=MemoryEntryType.context),
            _make_entry(content="Learning D", entry_type=MemoryEntryType.learning),
        ]
        summary = tiered_storage.summarize_for_cool(entries)
        assert "Key decisions:" in summary
        assert "Artifacts:" in summary
        assert "Plus 2 other entries" in summary

    def test_empty_entries(self, tiered_storage):
        summary = tiered_storage.summarize_for_cool([])
        assert summary == "No entries to summarise."

    def test_only_other_types(self, tiered_storage):
        entries = [
            _make_entry(content="Context note", entry_type=MemoryEntryType.context),
            _make_entry(content="A learning", entry_type=MemoryEntryType.learning),
        ]
        summary = tiered_storage.summarize_for_cool(entries)
        assert "Plus 2 other entries" in summary

    def test_duplicate_decisions_deduplicated(self, tiered_storage):
        entries = [
            _make_entry(content="Same decision", entry_type=MemoryEntryType.decision),
            _make_entry(content="Same decision", entry_type=MemoryEntryType.decision),
        ]
        summary = tiered_storage.summarize_for_cool(entries)
        # Should only appear once
        assert summary.count("Same decision") == 1

    def test_multiline_content_first_line(self, tiered_storage):
        entries = [
            _make_entry(
                content="First line decision\nWith more detail on second line",
                entry_type=MemoryEntryType.decision,
            ),
        ]
        summary = tiered_storage.summarize_for_cool(entries)
        assert "First line decision" in summary
        assert "second line" not in summary


# ==================================================================
# TestArchiveToCold
# ==================================================================


class TestArchiveToCold:
    """Tests for TieredStorage.archive_to_cold()."""

    def test_one_paragraph_format(self, tiered_storage):
        entries = [
            _make_entry(content="Decision 1", entry_type=MemoryEntryType.decision),
            _make_entry(content="Context 1", entry_type=MemoryEntryType.context),
        ]
        archive = tiered_storage.archive_to_cold(entries)
        assert "2 memory entries" in archive
        assert "Types:" in archive
        assert "Key decisions:" in archive
        # Should be a single paragraph (no line breaks)
        assert "\n" not in archive

    def test_empty_entries(self, tiered_storage):
        archive = tiered_storage.archive_to_cold([])
        assert archive == "No entries to archive."

    def test_date_range(self, tiered_storage):
        old_date = _aged(60)
        new_date = _aged(10)
        entries = [
            _make_entry(content="Old entry", created_at=old_date),
            _make_entry(content="New entry", created_at=new_date),
        ]
        archive = tiered_storage.archive_to_cold(entries)
        assert old_date.strftime("%Y-%m-%d") in archive
        assert new_date.strftime("%Y-%m-%d") in archive

    def test_type_counts(self, tiered_storage):
        entries = [
            _make_entry(entry_type=MemoryEntryType.decision),
            _make_entry(entry_type=MemoryEntryType.decision),
            _make_entry(entry_type=MemoryEntryType.context),
        ]
        archive = tiered_storage.archive_to_cold(entries)
        assert "2 decision" in archive
        assert "1 context" in archive

    def test_key_decisions_limited_to_three(self, tiered_storage):
        entries = [
            _make_entry(content=f"Decision {i}", entry_type=MemoryEntryType.decision)
            for i in range(5)
        ]
        archive = tiered_storage.archive_to_cold(entries)
        # Up to 3 decisions
        assert "Decision 0" in archive
        assert "Decision 1" in archive
        assert "Decision 2" in archive

    def test_no_decisions(self, tiered_storage):
        entries = [
            _make_entry(entry_type=MemoryEntryType.context),
        ]
        archive = tiered_storage.archive_to_cold(entries)
        assert "none recorded" in archive


# ==================================================================
# TestGetTierStats
# ==================================================================


class TestGetTierStats:
    """Tests for TieredStorage.get_tier_stats()."""

    def test_correct_tier_breakdowns(self, tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        # Store entries and transition some
        for i in range(3):
            _store_aged_entry(store, 0, content=f"Hot {i}")
        entry_warm = _store_aged_entry(store, 0, content="To warm")
        store.transition_tier(entry_warm.entry_id, MemoryTier.warm, "Test")

        stats = tiered_storage.get_tier_stats(store)
        assert stats["hot"]["count"] == 3
        assert stats["warm"]["count"] == 1
        assert stats["total"]["count"] == 4
        store.close()

    def test_empty_store(self, tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        stats = tiered_storage.get_tier_stats(store)
        assert stats["total"]["count"] == 0
        assert stats["total"]["bytes"] == 0
        for tier in ("hot", "warm", "cool", "cold"):
            assert stats[tier]["count"] == 0
        store.close()

    def test_bytes_tracked(self, tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        _store_aged_entry(store, 0, content="Known content")
        stats = tiered_storage.get_tier_stats(store)
        assert stats["hot"]["bytes"] == len("Known content".encode("utf-8"))
        assert stats["total"]["bytes"] == len("Known content".encode("utf-8"))
        store.close()


# ==================================================================
# TestGetAgingReport
# ==================================================================


class TestGetAgingReport:
    """Tests for TieredStorage.get_aging_report()."""

    def test_entries_sorted_by_urgency(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        _store_aged_entry(store, 2, content="Older")
        _store_aged_entry(store, 0, content="Fresh")
        report = fast_tiered_storage.get_aging_report(store)
        assert len(report) >= 1
        # Most urgent (smallest days_until_transition) first
        if len(report) >= 2:
            assert report[0]["days_until_transition"] <= report[1]["days_until_transition"]
        store.close()

    def test_days_calculation(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        _store_aged_entry(store, 3, content="3 days old")
        report = fast_tiered_storage.get_aging_report(store)
        assert len(report) >= 1
        # warm_age_days=1, so days_until = 1 - 3 = -2
        found = [r for r in report if r["age_days"] >= 3]
        assert len(found) >= 1
        assert found[0]["days_until_transition"] < 0  # past due

    def test_empty_store(self, tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        report = tiered_storage.get_aging_report(store)
        assert report == []
        store.close()

    def test_report_structure(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        _store_aged_entry(store, 2)
        report = fast_tiered_storage.get_aging_report(store)
        assert len(report) >= 1
        entry = report[0]
        assert "entry_id" in entry
        assert "current_tier" in entry
        assert "recommended_tier" in entry
        assert "days_until_transition" in entry
        assert "age_days" in entry
        store.close()

    def test_cold_entries_excluded(self, fast_tiered_storage):
        """Cold entries have no next step and should not appear."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        entry = _store_aged_entry(store, 0)
        # Manually transition to cold
        store.transition_tier(entry.entry_id, MemoryTier.warm, "1")
        store.transition_tier(entry.entry_id, MemoryTier.cool, "2")
        store.transition_tier(entry.entry_id, MemoryTier.cold, "3")
        report = fast_tiered_storage.get_aging_report(store)
        cold_entries = [r for r in report if r["current_tier"] == "cold"]
        assert len(cold_entries) == 0
        store.close()

    def test_warm_entries_included(self, fast_tiered_storage):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        entry = _store_aged_entry(store, 0)
        store.transition_tier(entry.entry_id, MemoryTier.warm, "Move")
        report = fast_tiered_storage.get_aging_report(store)
        warm_entries = [r for r in report if r["current_tier"] == "warm"]
        assert len(warm_entries) >= 1
        store.close()


# ==================================================================
# TestProperties
# ==================================================================


class TestProperties:
    """Tests for TieredStorage properties."""

    def test_default_thresholds(self):
        ts = TieredStorage()
        assert ts.warm_age_days == WARM_AGE_DAYS
        assert ts.cool_age_days == COOL_AGE_DAYS
        assert ts.cold_age_days == COLD_AGE_DAYS

    def test_custom_thresholds(self):
        ts = TieredStorage(warm_age_days=5, cool_age_days=15, cold_age_days=30)
        assert ts.warm_age_days == 5
        assert ts.cool_age_days == 15
        assert ts.cold_age_days == 30


# ==================================================================
# TestIntegration
# ==================================================================


class TestIntegration:
    """End-to-end integration tests."""

    def test_full_lifecycle(self, fast_tiered_storage):
        """Test assess -> recommend -> apply -> verify."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        _store_aged_entry(store, 3, content="Lifecycle test")

        # Assess
        transitions = fast_tiered_storage.run_tier_assessment(store)
        assert len(transitions) == 1

        # Apply
        applied = fast_tiered_storage.apply_transitions(store, transitions)
        assert len(applied) == 1

        # Verify
        stats = fast_tiered_storage.get_tier_stats(store)
        assert stats["warm"]["count"] == 1
        assert stats["hot"]["count"] == 0
        store.close()

    def test_multi_step_lifecycle(self, fast_tiered_storage):
        """Test multiple assessment rounds moving entry through tiers."""
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        stored = _store_aged_entry(store, 15, content="Multi-step test")

        # Round 1: hot -> warm
        transitions = fast_tiered_storage.run_tier_assessment(store)
        fast_tiered_storage.apply_transitions(store, transitions)
        entry = store.get(stored.entry_id)
        assert entry.tier == MemoryTier.warm

        # Round 2: warm -> cool
        transitions = fast_tiered_storage.run_tier_assessment(store)
        fast_tiered_storage.apply_transitions(store, transitions)
        entry = store.get(stored.entry_id)
        assert entry.tier == MemoryTier.cool

        # Round 3: cool -> cold
        transitions = fast_tiered_storage.run_tier_assessment(store)
        fast_tiered_storage.apply_transitions(store, transitions)
        entry = store.get(stored.entry_id)
        assert entry.tier == MemoryTier.cold

        # Round 4: no more transitions
        transitions = fast_tiered_storage.run_tier_assessment(store)
        assert len(transitions) == 0

        store.close()
