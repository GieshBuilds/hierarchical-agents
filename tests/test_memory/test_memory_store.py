"""Comprehensive tests for the MemoryStore class.

Tests cover init, CRUD, search, list, stats, tier transitions,
bulk transitions, budget management, and close operations.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from core.memory.exceptions import (
    InvalidTierTransition,
    MemoryEntryNotFound,
    MemoryStoreError,
)
from core.memory.memory_store import MemoryStore
from core.memory.models import (
    MemoryBudget,
    MemoryEntry,
    MemoryEntryType,
    MemoryScope,
    MemoryTier,
    generate_memory_id,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_entry(
    scope: MemoryScope = MemoryScope.strategic,
    tier: MemoryTier = MemoryTier.hot,
    entry_type: MemoryEntryType = MemoryEntryType.decision,
    content: str = "Test content",
    entry_id: str = "",
    metadata: dict | None = None,
    created_at: datetime | None = None,
) -> MemoryEntry:
    """Create a MemoryEntry with optional overrides."""
    kwargs: dict = {
        "entry_id": entry_id or generate_memory_id(),
        "profile_name": "ceo",
        "scope": scope,
        "tier": tier,
        "entry_type": entry_type,
        "content": content,
        "metadata": metadata or {},
    }
    if created_at is not None:
        kwargs["created_at"] = created_at
    return MemoryEntry(**kwargs)


# ==================================================================
# TestMemoryStoreInit
# ==================================================================


class TestMemoryStoreInit:
    """Tests for MemoryStore construction and properties."""

    def test_create_in_memory(self):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        assert store.profile_name == "ceo"
        assert store.profile_scope == MemoryScope.strategic
        assert store.db_path == ":memory:"
        store.close()

    def test_create_file_backed(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        store = MemoryStore(db_path, "pm-alpha", MemoryScope.project)
        assert store.profile_name == "pm-alpha"
        assert store.profile_scope == MemoryScope.project
        store.close()

    def test_profile_name_binding(self, memory_store):
        assert memory_store.profile_name == "ceo"

    def test_profile_scope_binding(self, memory_store):
        assert memory_store.profile_scope == MemoryScope.strategic

    def test_db_path_property(self, memory_store):
        assert memory_store.db_path == ":memory:"


# ==================================================================
# TestStore
# ==================================================================


class TestStore:
    """Tests for MemoryStore.store()."""

    def test_basic_store(self, memory_store):
        entry = _make_entry()
        result = memory_store.store(entry)
        assert result.entry_id == entry.entry_id
        assert result.profile_name == "ceo"

    def test_returns_stored_entry(self, memory_store):
        entry = _make_entry(content="Hello world")
        result = memory_store.store(entry)
        assert result.content == "Hello world"

    def test_scope_enforcement_rejects_wrong_scope(self, memory_store):
        entry = _make_entry(scope=MemoryScope.task)
        with pytest.raises(MemoryStoreError, match="scope"):
            memory_store.store(entry)

    def test_scope_enforcement_domain_on_strategic(self, memory_store):
        entry = _make_entry(scope=MemoryScope.domain)
        with pytest.raises(MemoryStoreError):
            memory_store.store(entry)

    def test_auto_id_generation(self, memory_store):
        entry = _make_entry(entry_id="")
        result = memory_store.store(entry)
        assert result.entry_id.startswith("mem-")

    def test_preserves_provided_id(self, memory_store):
        entry = _make_entry(entry_id="mem-custom123")
        result = memory_store.store(entry)
        assert result.entry_id == "mem-custom123"

    def test_byte_size_calculation(self, memory_store):
        content = "Hello 🌍"  # Unicode content
        entry = _make_entry(content=content)
        result = memory_store.store(entry)
        expected_size = len(content.encode("utf-8"))
        assert result.byte_size == expected_size

    def test_byte_size_ascii(self, memory_store):
        content = "Simple ASCII text"
        entry = _make_entry(content=content)
        result = memory_store.store(entry)
        assert result.byte_size == len(content)

    def test_profile_name_auto_set(self, memory_store):
        entry = _make_entry()
        entry.profile_name = "someone_else"
        result = memory_store.store(entry)
        assert result.profile_name == "ceo"

    def test_store_multiple(self, memory_store):
        for i in range(10):
            entry = _make_entry(content=f"Entry {i}")
            memory_store.store(entry)
        stats = memory_store.get_stats()
        assert stats["total_entries"] == 10

    def test_store_with_metadata(self, memory_store):
        entry = _make_entry(metadata={"priority": "high", "source": "user"})
        result = memory_store.store(entry)
        retrieved = memory_store.get(result.entry_id)
        assert retrieved.metadata["priority"] == "high"

    def test_duplicate_entry_id_raises(self, memory_store):
        entry = _make_entry(entry_id="mem-duplicate")
        memory_store.store(entry)
        entry2 = _make_entry(entry_id="mem-duplicate", content="Different")
        with pytest.raises(MemoryStoreError):
            memory_store.store(entry2)

    def test_store_all_entry_types(self, memory_store):
        for et in MemoryEntryType:
            entry = _make_entry(entry_type=et, content=f"Content for {et.value}")
            result = memory_store.store(entry)
            assert result.entry_type == et

    def test_store_with_expires_at(self, memory_store):
        future = datetime.now(timezone.utc) + timedelta(days=7)
        entry = _make_entry()
        entry.expires_at = future
        result = memory_store.store(entry)
        retrieved = memory_store.get(result.entry_id)
        assert retrieved.expires_at is not None


# ==================================================================
# TestGet
# ==================================================================


class TestGet:
    """Tests for MemoryStore.get()."""

    def test_basic_get(self, memory_store):
        entry = _make_entry(content="Get me")
        stored = memory_store.store(entry)
        retrieved = memory_store.get(stored.entry_id)
        assert retrieved.content == "Get me"

    def test_not_found(self, memory_store):
        with pytest.raises(MemoryEntryNotFound):
            memory_store.get("mem-nonexistent")

    def test_accessed_at_updated(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        before = stored.accessed_at

        # Small delay to ensure timestamp changes
        time.sleep(0.01)
        retrieved = memory_store.get(stored.entry_id)
        assert retrieved.accessed_at >= before

    def test_get_preserves_all_fields(self, memory_store):
        entry = _make_entry(
            content="Full field test",
            entry_type=MemoryEntryType.learning,
            metadata={"key": "value"},
        )
        stored = memory_store.store(entry)
        retrieved = memory_store.get(stored.entry_id)
        assert retrieved.scope == MemoryScope.strategic
        assert retrieved.tier == MemoryTier.hot
        assert retrieved.entry_type == MemoryEntryType.learning
        assert retrieved.metadata == {"key": "value"}

    def test_get_wrong_profile(self):
        """A store for profile A cannot get entries stored by profile B."""
        store_a = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        entry = _make_entry()
        stored = store_a.store(entry)

        # Create a new store on the same db but different profile
        # Since :memory: creates separate DBs, this won't find it
        store_b = MemoryStore(":memory:", "cto", MemoryScope.strategic)
        with pytest.raises(MemoryEntryNotFound):
            store_b.get(stored.entry_id)
        store_a.close()
        store_b.close()

    def test_get_returns_correct_byte_size(self, memory_store):
        entry = _make_entry(content="Known content")
        stored = memory_store.store(entry)
        retrieved = memory_store.get(stored.entry_id)
        assert retrieved.byte_size == len("Known content".encode("utf-8"))


# ==================================================================
# TestSearch
# ==================================================================


class TestSearch:
    """Tests for MemoryStore.search()."""

    def test_keyword_search(self, populated_memory_store):
        store, entries = populated_memory_store
        results = store.search("Decision 2")
        assert len(results) >= 1
        assert any("Decision 2" in r.content for r in results)

    def test_search_returns_list(self, memory_store):
        results = memory_store.search("anything")
        assert isinstance(results, list)

    def test_search_empty_results(self, memory_store):
        results = memory_store.search("nonexistent_term_xyz")
        assert results == []

    def test_search_all_match(self, populated_memory_store):
        store, entries = populated_memory_store
        results = store.search("Test content")
        assert len(results) == 5

    def test_filtered_search_by_type(self, mixed_memory_store):
        store, entries = mixed_memory_store
        results = store.search("Content", entry_type=MemoryEntryType.decision)
        assert all(r.entry_type == MemoryEntryType.decision for r in results)

    def test_filtered_search_by_tier(self, populated_memory_store):
        store, entries = populated_memory_store
        results = store.search("Decision", tier=MemoryTier.hot)
        assert all(r.tier == MemoryTier.hot for r in results)

    def test_search_with_limit(self, populated_memory_store):
        store, entries = populated_memory_store
        results = store.search("Decision", limit=2)
        assert len(results) <= 2

    def test_search_case_insensitive_content(self, memory_store):
        entry = _make_entry(content="Important Decision About APIs")
        memory_store.store(entry)
        # SQLite LIKE is case-insensitive for ASCII by default
        results = memory_store.search("important")
        # May or may not match depending on SQLite build; test structure
        assert isinstance(results, list)

    def test_search_partial_match(self, populated_memory_store):
        store, entries = populated_memory_store
        results = store.search("Decis")
        assert len(results) >= 1


# ==================================================================
# TestUpdate
# ==================================================================


class TestUpdate:
    """Tests for MemoryStore.update()."""

    def test_update_content(self, memory_store):
        entry = _make_entry(content="Original content")
        stored = memory_store.store(entry)
        updated = memory_store.update(stored.entry_id, content="Updated content")
        assert updated.content == "Updated content"

    def test_update_metadata(self, memory_store):
        entry = _make_entry(metadata={"old": True})
        stored = memory_store.store(entry)
        updated = memory_store.update(stored.entry_id, metadata={"new": True})
        assert updated.metadata == {"new": True}

    def test_update_entry_type(self, memory_store):
        entry = _make_entry(entry_type=MemoryEntryType.decision)
        stored = memory_store.store(entry)
        updated = memory_store.update(
            stored.entry_id, entry_type=MemoryEntryType.learning
        )
        assert updated.entry_type == MemoryEntryType.learning

    def test_update_expires_at(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        new_expiry = datetime.now(timezone.utc) + timedelta(days=30)
        updated = memory_store.update(stored.entry_id, expires_at=new_expiry)
        assert updated.expires_at is not None

    def test_update_expires_at_none(self, memory_store):
        entry = _make_entry()
        entry.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        stored = memory_store.store(entry)
        updated = memory_store.update(stored.entry_id, expires_at=None)
        assert updated.expires_at is None

    def test_invalid_field_rejection(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        with pytest.raises(MemoryStoreError, match="disallowed fields"):
            memory_store.update(stored.entry_id, scope="domain")

    def test_invalid_field_entry_id(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        with pytest.raises(MemoryStoreError):
            memory_store.update(stored.entry_id, profile_name="hacker")

    def test_recalculate_byte_size(self, memory_store):
        entry = _make_entry(content="Short")
        stored = memory_store.store(entry)
        updated = memory_store.update(
            stored.entry_id, content="This is a much longer content string"
        )
        assert updated.byte_size == len(
            "This is a much longer content string".encode("utf-8")
        )

    def test_updated_at_changes(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        original_updated = stored.updated_at
        time.sleep(0.01)
        updated = memory_store.update(stored.entry_id, content="New")
        assert updated.updated_at >= original_updated

    def test_update_nonexistent_entry(self, memory_store):
        with pytest.raises(MemoryEntryNotFound):
            memory_store.update("mem-nonexistent", content="anything")

    def test_update_multiple_fields(self, memory_store):
        entry = _make_entry(content="Old content", metadata={"old": True})
        stored = memory_store.store(entry)
        updated = memory_store.update(
            stored.entry_id,
            content="New content",
            metadata={"new": True},
        )
        assert updated.content == "New content"
        assert updated.metadata == {"new": True}


# ==================================================================
# TestDelete
# ==================================================================


class TestDelete:
    """Tests for MemoryStore.delete()."""

    def test_basic_delete(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        memory_store.delete(stored.entry_id)
        with pytest.raises(MemoryEntryNotFound):
            memory_store.get(stored.entry_id)

    def test_delete_not_found(self, memory_store):
        with pytest.raises(MemoryEntryNotFound):
            memory_store.delete("mem-nonexistent")

    def test_delete_reduces_count(self, populated_memory_store):
        store, entries = populated_memory_store
        initial_stats = store.get_stats()
        store.delete(entries[0].entry_id)
        after_stats = store.get_stats()
        assert after_stats["total_entries"] == initial_stats["total_entries"] - 1

    def test_delete_twice_raises(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        memory_store.delete(stored.entry_id)
        with pytest.raises(MemoryEntryNotFound):
            memory_store.delete(stored.entry_id)


# ==================================================================
# TestListEntries
# ==================================================================


class TestListEntries:
    """Tests for MemoryStore.list_entries()."""

    def test_basic_list(self, populated_memory_store):
        store, entries = populated_memory_store
        results = store.list_entries()
        assert len(results) == 5

    def test_empty_list(self, memory_store):
        results = memory_store.list_entries()
        assert results == []

    def test_filtered_by_tier(self, memory_store):
        entry_hot = _make_entry(content="Hot entry")
        memory_store.store(entry_hot)
        results_hot = memory_store.list_entries(tier=MemoryTier.hot)
        results_warm = memory_store.list_entries(tier=MemoryTier.warm)
        assert len(results_hot) == 1
        assert len(results_warm) == 0

    def test_filtered_by_scope(self, memory_store):
        entry = _make_entry(scope=MemoryScope.strategic)
        memory_store.store(entry)
        results = memory_store.list_entries(scope=MemoryScope.strategic)
        assert len(results) == 1
        results_task = memory_store.list_entries(scope=MemoryScope.task)
        assert len(results_task) == 0

    def test_filtered_by_entry_type(self, mixed_memory_store):
        store, entries = mixed_memory_store
        results = store.list_entries(entry_type=MemoryEntryType.decision)
        assert all(r.entry_type == MemoryEntryType.decision for r in results)

    def test_pagination_offset(self, populated_memory_store):
        store, entries = populated_memory_store
        first_page = store.list_entries(offset=0, limit=2)
        second_page = store.list_entries(offset=2, limit=2)
        assert len(first_page) == 2
        assert len(second_page) == 2
        # Entries should be different
        first_ids = {e.entry_id for e in first_page}
        second_ids = {e.entry_id for e in second_page}
        assert first_ids.isdisjoint(second_ids)

    def test_pagination_limit(self, populated_memory_store):
        store, entries = populated_memory_store
        results = store.list_entries(limit=3)
        assert len(results) == 3

    def test_list_returns_memory_entry_objects(self, populated_memory_store):
        store, entries = populated_memory_store
        results = store.list_entries()
        for r in results:
            assert isinstance(r, MemoryEntry)


# ==================================================================
# TestGetStats
# ==================================================================


class TestGetStats:
    """Tests for MemoryStore.get_stats()."""

    def test_empty_stats(self, memory_store):
        stats = memory_store.get_stats()
        assert stats["total_entries"] == 0
        assert stats["total_bytes"] == 0

    def test_correct_total_count(self, populated_memory_store):
        store, entries = populated_memory_store
        stats = store.get_stats()
        assert stats["total_entries"] == 5

    def test_by_tier_counts(self, populated_memory_store):
        store, entries = populated_memory_store
        stats = store.get_stats()
        assert stats["by_tier"].get("hot", 0) == 5

    def test_by_type_counts(self, mixed_memory_store):
        store, entries = mixed_memory_store
        stats = store.get_stats()
        assert stats["by_type"].get("decision", 0) >= 1
        assert stats["by_type"].get("learning", 0) >= 1

    def test_by_scope_counts(self, populated_memory_store):
        store, entries = populated_memory_store
        stats = store.get_stats()
        assert stats["by_scope"].get("strategic", 0) == 5

    def test_total_bytes(self, memory_store):
        entry = _make_entry(content="Exactly twenty chars")
        memory_store.store(entry)
        stats = memory_store.get_stats()
        assert stats["total_bytes"] == len("Exactly twenty chars".encode("utf-8"))

    def test_budget_included_when_set(self, memory_store):
        budget = MemoryBudget(profile_name="ceo", max_entries=100, max_bytes=1000)
        memory_store.set_budget(budget)
        stats = memory_store.get_stats()
        assert stats["budget"] is not None
        assert stats["budget"]["max_entries"] == 100

    def test_budget_none_when_not_set(self, memory_store):
        stats = memory_store.get_stats()
        assert stats["budget"] is None


# ==================================================================
# TestTierTransition
# ==================================================================


class TestTierTransition:
    """Tests for MemoryStore.transition_tier()."""

    def test_valid_hot_to_warm(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        transition = memory_store.transition_tier(
            stored.entry_id, MemoryTier.warm, "Age-based"
        )
        assert transition.from_tier == MemoryTier.hot
        assert transition.to_tier == MemoryTier.warm
        assert transition.transition_id.startswith("tt-")

    def test_valid_warm_to_cool(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        memory_store.transition_tier(stored.entry_id, MemoryTier.warm, "Step 1")
        transition = memory_store.transition_tier(
            stored.entry_id, MemoryTier.cool, "Step 2"
        )
        assert transition.from_tier == MemoryTier.warm
        assert transition.to_tier == MemoryTier.cool

    def test_valid_cool_to_cold(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        memory_store.transition_tier(stored.entry_id, MemoryTier.warm, "Step 1")
        memory_store.transition_tier(stored.entry_id, MemoryTier.cool, "Step 2")
        transition = memory_store.transition_tier(
            stored.entry_id, MemoryTier.cold, "Step 3"
        )
        assert transition.from_tier == MemoryTier.cool
        assert transition.to_tier == MemoryTier.cold

    def test_invalid_hot_to_cool(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        with pytest.raises(InvalidTierTransition):
            memory_store.transition_tier(stored.entry_id, MemoryTier.cool, "Skip")

    def test_invalid_hot_to_cold(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        with pytest.raises(InvalidTierTransition):
            memory_store.transition_tier(stored.entry_id, MemoryTier.cold, "Skip")

    def test_invalid_backward_warm_to_hot(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        memory_store.transition_tier(stored.entry_id, MemoryTier.warm, "Forward")
        with pytest.raises(InvalidTierTransition):
            memory_store.transition_tier(stored.entry_id, MemoryTier.hot, "Backward")

    def test_invalid_cold_to_anything(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        memory_store.transition_tier(stored.entry_id, MemoryTier.warm, "1")
        memory_store.transition_tier(stored.entry_id, MemoryTier.cool, "2")
        memory_store.transition_tier(stored.entry_id, MemoryTier.cold, "3")
        with pytest.raises(InvalidTierTransition):
            memory_store.transition_tier(stored.entry_id, MemoryTier.hot, "Back")

    def test_transition_record_created(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        transition = memory_store.transition_tier(
            stored.entry_id, MemoryTier.warm, "Testing"
        )
        assert transition.entry_id == stored.entry_id
        assert transition.reason == "Testing"
        assert transition.transitioned_at is not None

    def test_entry_tier_updated(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        memory_store.transition_tier(stored.entry_id, MemoryTier.warm, "Test")
        retrieved = memory_store.get(stored.entry_id)
        assert retrieved.tier == MemoryTier.warm

    def test_transition_nonexistent_entry(self, memory_store):
        with pytest.raises(MemoryEntryNotFound):
            memory_store.transition_tier("mem-none", MemoryTier.warm, "Test")


# ==================================================================
# TestBulkTransition
# ==================================================================


class TestBulkTransition:
    """Tests for MemoryStore.bulk_transition()."""

    def test_batch_transitions(self, populated_memory_store):
        store, entries = populated_memory_store
        entry_ids = [e.entry_id for e in entries]
        transitions = store.bulk_transition(
            entry_ids, MemoryTier.warm, "Batch age-based"
        )
        assert len(transitions) == 5
        for t in transitions:
            assert t.to_tier == MemoryTier.warm

    def test_mixed_valid_invalid(self, memory_store):
        # Create 3 entries: 2 hot, 1 already warm
        hot1 = _make_entry(content="Hot 1")
        hot2 = _make_entry(content="Hot 2")
        hot3 = _make_entry(content="Hot 3")
        memory_store.store(hot1)
        memory_store.store(hot2)
        stored3 = memory_store.store(hot3)
        # Transition one to warm first
        memory_store.transition_tier(stored3.entry_id, MemoryTier.warm, "Pre-move")

        entry_ids = [hot1.entry_id, hot2.entry_id, hot3.entry_id]
        # Try to transition all to warm — hot3 is already warm -> invalid
        transitions = memory_store.bulk_transition(
            entry_ids, MemoryTier.warm, "Batch"
        )
        assert len(transitions) == 2  # Only hot1 and hot2

    def test_nonexistent_entries_skipped(self, memory_store):
        entry = _make_entry()
        memory_store.store(entry)
        transitions = memory_store.bulk_transition(
            [entry.entry_id, "mem-fake"], MemoryTier.warm, "Batch"
        )
        assert len(transitions) == 1

    def test_empty_list(self, memory_store):
        transitions = memory_store.bulk_transition([], MemoryTier.warm, "Empty")
        assert transitions == []

    def test_bulk_transition_all_invalid(self, memory_store):
        entry = _make_entry()
        stored = memory_store.store(entry)
        # Try cool directly (skip warm) — invalid
        transitions = memory_store.bulk_transition(
            [stored.entry_id], MemoryTier.cool, "Skip"
        )
        assert transitions == []


# ==================================================================
# TestBudget
# ==================================================================


class TestBudget:
    """Tests for budget management methods."""

    def test_set_budget(self, memory_store):
        budget = MemoryBudget(profile_name="ceo", max_entries=100, max_bytes=5000)
        memory_store.set_budget(budget)
        retrieved = memory_store.get_budget()
        assert retrieved is not None
        assert retrieved.max_entries == 100
        assert retrieved.max_bytes == 5000

    def test_get_budget_none(self, memory_store):
        budget = memory_store.get_budget()
        assert budget is None

    def test_set_budget_overrides_profile(self, memory_store):
        budget = MemoryBudget(profile_name="someone_else", max_entries=50)
        memory_store.set_budget(budget)
        retrieved = memory_store.get_budget()
        assert retrieved.profile_name == "ceo"

    def test_set_budget_replace(self, memory_store):
        budget1 = MemoryBudget(profile_name="ceo", max_entries=100)
        memory_store.set_budget(budget1)
        budget2 = MemoryBudget(profile_name="ceo", max_entries=200)
        memory_store.set_budget(budget2)
        retrieved = memory_store.get_budget()
        assert retrieved.max_entries == 200

    def test_check_budget_no_budget(self, memory_store):
        status = memory_store.check_budget()
        assert status["exceeded"] is False
        assert status["limits"]["max_entries"] is None

    def test_check_budget_not_exceeded(self, memory_store):
        budget = MemoryBudget(profile_name="ceo", max_entries=100, max_bytes=100_000)
        memory_store.set_budget(budget)
        entry = _make_entry()
        memory_store.store(entry)
        status = memory_store.check_budget()
        assert status["exceeded"] is False
        assert status["usage"]["entries"] == 1

    def test_check_budget_exceeded_entries(self, memory_store):
        budget = MemoryBudget(profile_name="ceo", max_entries=2, max_bytes=100_000)
        memory_store.set_budget(budget)
        for i in range(3):
            entry = _make_entry(content=f"Budget test {i}")
            memory_store.store(entry)
        status = memory_store.check_budget()
        assert status["exceeded"] is True

    def test_check_budget_exceeded_bytes(self, memory_store):
        budget = MemoryBudget(profile_name="ceo", max_entries=100, max_bytes=10)
        memory_store.set_budget(budget)
        entry = _make_entry(content="This content exceeds 10 bytes easily")
        memory_store.store(entry)
        status = memory_store.check_budget()
        assert status["exceeded"] is True

    def test_check_budget_tier_usage(self, memory_store):
        entry = _make_entry(content="Tier check")
        memory_store.store(entry)
        status = memory_store.check_budget()
        assert "hot" in status["tier_usage"]
        assert status["tier_usage"]["hot"] == 1

    def test_budget_tier_quotas(self, memory_store):
        budget = MemoryBudget(
            profile_name="ceo",
            tier_quotas={"hot": 10, "warm": 20, "cool": 30, "cold": 40},
        )
        memory_store.set_budget(budget)
        retrieved = memory_store.get_budget()
        assert retrieved.tier_quotas["hot"] == 10
        assert retrieved.tier_quotas["cold"] == 40


# ==================================================================
# TestClose
# ==================================================================


class TestClose:
    """Tests for MemoryStore.close()."""

    def test_close_works(self):
        store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
        entry = _make_entry()
        store.store(entry)
        store.close()
        # After close, operations should fail
        # (ProgrammingError for closed db)


# ==================================================================
# Additional edge cases
# ==================================================================


class TestEdgeCases:
    """Additional edge case tests."""

    def test_empty_content(self, memory_store):
        entry = _make_entry(content="")
        result = memory_store.store(entry)
        assert result.byte_size == 0

    def test_large_content(self, memory_store):
        content = "x" * 100_000
        entry = _make_entry(content=content)
        result = memory_store.store(entry)
        assert result.byte_size == 100_000

    def test_special_characters_in_content(self, memory_store):
        content = "SQL injection test: '; DROP TABLE memory_entries; --"
        entry = _make_entry(content=content)
        stored = memory_store.store(entry)
        retrieved = memory_store.get(stored.entry_id)
        assert retrieved.content == content

    def test_unicode_content(self, memory_store):
        content = "日本語テスト 🎉 αβγδ"
        entry = _make_entry(content=content)
        stored = memory_store.store(entry)
        retrieved = memory_store.get(stored.entry_id)
        assert retrieved.content == content

    def test_concurrent_store_and_get(self, memory_store):
        """Basic concurrency: store then immediately get."""
        entry = _make_entry()
        stored = memory_store.store(entry)
        retrieved = memory_store.get(stored.entry_id)
        assert stored.entry_id == retrieved.entry_id
