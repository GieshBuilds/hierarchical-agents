"""Comprehensive tests for the KnowledgeBase class.

Tests cover init, add, get, search, update, delete, category
operations, learning extraction, and statistics.
"""
from __future__ import annotations

import time

import pytest

from core.memory.exceptions import KnowledgeEntryNotFound
from core.memory.knowledge_base import KnowledgeBase
from core.memory.models import (
    KnowledgeEntry,
    generate_knowledge_id,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_kb_entry(
    category: str = "general",
    title: str = "Test Entry",
    content: str = "Test content for knowledge base",
    tags: list[str] | None = None,
    entry_id: str = "",
    source_profile: str = "",
    source_context: str = "",
) -> KnowledgeEntry:
    """Create a KnowledgeEntry with optional overrides."""
    return KnowledgeEntry(
        entry_id=entry_id or generate_knowledge_id(),
        profile_name="ceo",
        category=category,
        title=title,
        content=content,
        source_profile=source_profile,
        source_context=source_context,
        tags=tags or [],
    )


# ==================================================================
# TestKnowledgeBaseInit
# ==================================================================


class TestKnowledgeBaseInit:
    """Tests for KnowledgeBase construction."""

    def test_creation(self):
        kb = KnowledgeBase(":memory:", "ceo")
        assert kb.profile_name == "ceo"
        assert kb.db_path == ":memory:"
        kb.close()

    def test_creation_with_different_profile(self):
        kb = KnowledgeBase(":memory:", "cto")
        assert kb.profile_name == "cto"
        kb.close()

    def test_file_backed(self, tmp_path):
        db_path = str(tmp_path / "kb.db")
        kb = KnowledgeBase(db_path, "pm-alpha")
        assert kb.profile_name == "pm-alpha"
        kb.close()


# ==================================================================
# TestAddKnowledge
# ==================================================================


class TestAddKnowledge:
    """Tests for KnowledgeBase.add_knowledge()."""

    def test_basic_add(self, knowledge_base):
        entry = _make_kb_entry()
        result = knowledge_base.add_knowledge(entry)
        assert result.entry_id != ""
        assert result.category == "general"

    def test_auto_id_generation(self, knowledge_base):
        entry = _make_kb_entry(entry_id="")
        result = knowledge_base.add_knowledge(entry)
        assert result.entry_id.startswith("kb-")

    def test_preserves_provided_id(self, knowledge_base):
        entry = _make_kb_entry(entry_id="kb-custom123")
        result = knowledge_base.add_knowledge(entry)
        assert result.entry_id == "kb-custom123"

    def test_profile_binding(self, knowledge_base):
        entry = _make_kb_entry()
        entry.profile_name = "someone_else"
        result = knowledge_base.add_knowledge(entry)
        assert result.profile_name == "ceo"

    def test_timestamps_set(self, knowledge_base):
        entry = _make_kb_entry()
        result = knowledge_base.add_knowledge(entry)
        assert result.created_at is not None
        assert result.updated_at is not None

    def test_add_with_tags(self, knowledge_base):
        entry = _make_kb_entry(tags=["python", "testing"])
        result = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(result.entry_id)
        assert "python" in retrieved.tags
        assert "testing" in retrieved.tags

    def test_add_with_source_fields(self, knowledge_base):
        entry = _make_kb_entry(
            source_profile="cto",
            source_context="architecture review",
        )
        result = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(result.entry_id)
        assert retrieved.source_profile == "cto"
        assert retrieved.source_context == "architecture review"

    def test_add_multiple(self, knowledge_base):
        for i in range(10):
            entry = _make_kb_entry(title=f"Entry {i}", content=f"Content {i}")
            knowledge_base.add_knowledge(entry)
        stats = knowledge_base.get_stats()
        assert stats["total_entries"] == 10

    def test_add_empty_tags(self, knowledge_base):
        entry = _make_kb_entry(tags=[])
        result = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(result.entry_id)
        assert retrieved.tags == []


# ==================================================================
# TestGetKnowledge
# ==================================================================


class TestGetKnowledge:
    """Tests for KnowledgeBase.get_knowledge()."""

    def test_basic_get(self, knowledge_base):
        entry = _make_kb_entry(title="Get Me", content="Found content")
        stored = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(stored.entry_id)
        assert retrieved.title == "Get Me"
        assert retrieved.content == "Found content"

    def test_not_found(self, knowledge_base):
        with pytest.raises(KnowledgeEntryNotFound):
            knowledge_base.get_knowledge("kb-nonexistent")

    def test_preserves_all_fields(self, knowledge_base):
        entry = _make_kb_entry(
            category="arch",
            title="Full Test",
            content="Complete content",
            tags=["a", "b"],
            source_profile="cto",
            source_context="review",
        )
        stored = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(stored.entry_id)
        assert retrieved.category == "arch"
        assert retrieved.tags == ["a", "b"]
        assert retrieved.source_profile == "cto"

    def test_get_returns_knowledge_entry(self, knowledge_base):
        entry = _make_kb_entry()
        stored = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(stored.entry_id)
        assert isinstance(retrieved, KnowledgeEntry)


# ==================================================================
# TestSearchKnowledge
# ==================================================================


class TestSearchKnowledge:
    """Tests for KnowledgeBase.search_knowledge()."""

    def test_keyword_search_title(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.search_knowledge("Microservice")
        assert len(results) >= 1
        assert any("Microservice" in r.title for r in results)

    def test_keyword_search_content(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.search_knowledge("event-driven")
        assert len(results) >= 1

    def test_empty_results(self, knowledge_base):
        results = knowledge_base.search_knowledge("nonexistent_xyz")
        assert results == []

    def test_category_filter(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.search_knowledge("", category="architecture")
        assert all(r.category == "architecture" for r in results)
        assert len(results) >= 1

    def test_tag_filter(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.search_knowledge("", tags=["auth"])
        assert len(results) >= 1
        for r in results:
            assert "auth" in r.tags

    def test_combined_category_and_tag(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.search_knowledge("", category="process", tags=["review"])
        assert len(results) >= 1
        for r in results:
            assert r.category == "process"
            assert "review" in r.tags

    def test_search_limit(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.search_knowledge("", limit=2)
        assert len(results) <= 2

    def test_search_returns_list(self, knowledge_base):
        results = knowledge_base.search_knowledge("test")
        assert isinstance(results, list)

    def test_search_multiple_tags(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.search_knowledge("", tags=["deploy", "process"])
        assert len(results) >= 1

    def test_broad_search(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        # Empty query matches broadly (title LIKE '%%' OR content LIKE '%%')
        results = kb.search_knowledge("")
        assert len(results) >= 3


# ==================================================================
# TestUpdateKnowledge
# ==================================================================


class TestUpdateKnowledge:
    """Tests for KnowledgeBase.update_knowledge()."""

    def test_update_title(self, knowledge_base):
        entry = _make_kb_entry(title="Original Title")
        stored = knowledge_base.add_knowledge(entry)
        updated = knowledge_base.update_knowledge(stored.entry_id, title="New Title")
        assert updated.title == "New Title"

    def test_update_content(self, knowledge_base):
        entry = _make_kb_entry(content="Original content")
        stored = knowledge_base.add_knowledge(entry)
        updated = knowledge_base.update_knowledge(
            stored.entry_id, content="New content"
        )
        assert updated.content == "New content"

    def test_update_category(self, knowledge_base):
        entry = _make_kb_entry(category="old")
        stored = knowledge_base.add_knowledge(entry)
        updated = knowledge_base.update_knowledge(stored.entry_id, category="new")
        assert updated.category == "new"

    def test_update_tags(self, knowledge_base):
        entry = _make_kb_entry(tags=["old"])
        stored = knowledge_base.add_knowledge(entry)
        updated = knowledge_base.update_knowledge(
            stored.entry_id, tags=["new1", "new2"]
        )
        assert updated.tags == ["new1", "new2"]

    def test_update_source_fields(self, knowledge_base):
        entry = _make_kb_entry()
        stored = knowledge_base.add_knowledge(entry)
        updated = knowledge_base.update_knowledge(
            stored.entry_id,
            source_profile="pm-1",
            source_context="sprint review",
        )
        assert updated.source_profile == "pm-1"
        assert updated.source_context == "sprint review"

    def test_update_not_found(self, knowledge_base):
        with pytest.raises(KnowledgeEntryNotFound):
            knowledge_base.update_knowledge("kb-nonexistent", title="New")

    def test_update_invalid_field(self, knowledge_base):
        entry = _make_kb_entry()
        stored = knowledge_base.add_knowledge(entry)
        with pytest.raises(ValueError, match="Invalid field"):
            knowledge_base.update_knowledge(stored.entry_id, profile_name="hacker")

    def test_update_bumps_updated_at(self, knowledge_base):
        entry = _make_kb_entry()
        stored = knowledge_base.add_knowledge(entry)
        original = stored.updated_at
        time.sleep(0.01)
        updated = knowledge_base.update_knowledge(stored.entry_id, title="Changed")
        assert updated.updated_at >= original

    def test_update_no_fields(self, knowledge_base):
        entry = _make_kb_entry(title="Unchanged")
        stored = knowledge_base.add_knowledge(entry)
        result = knowledge_base.update_knowledge(stored.entry_id)
        assert result.title == "Unchanged"

    def test_update_multiple_fields(self, knowledge_base):
        entry = _make_kb_entry(title="Old", content="Old content")
        stored = knowledge_base.add_knowledge(entry)
        updated = knowledge_base.update_knowledge(
            stored.entry_id, title="New", content="New content"
        )
        assert updated.title == "New"
        assert updated.content == "New content"


# ==================================================================
# TestDeleteKnowledge
# ==================================================================


class TestDeleteKnowledge:
    """Tests for KnowledgeBase.delete_knowledge()."""

    def test_basic_delete(self, knowledge_base):
        entry = _make_kb_entry()
        stored = knowledge_base.add_knowledge(entry)
        knowledge_base.delete_knowledge(stored.entry_id)
        with pytest.raises(KnowledgeEntryNotFound):
            knowledge_base.get_knowledge(stored.entry_id)

    def test_delete_not_found(self, knowledge_base):
        with pytest.raises(KnowledgeEntryNotFound):
            knowledge_base.delete_knowledge("kb-nonexistent")

    def test_delete_reduces_count(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        initial = kb.get_stats()["total_entries"]
        kb.delete_knowledge(entries[0].entry_id)
        after = kb.get_stats()["total_entries"]
        assert after == initial - 1

    def test_delete_twice_raises(self, knowledge_base):
        entry = _make_kb_entry()
        stored = knowledge_base.add_knowledge(entry)
        knowledge_base.delete_knowledge(stored.entry_id)
        with pytest.raises(KnowledgeEntryNotFound):
            knowledge_base.delete_knowledge(stored.entry_id)


# ==================================================================
# TestListCategories
# ==================================================================


class TestListCategories:
    """Tests for KnowledgeBase.list_categories()."""

    def test_distinct_categories(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        categories = kb.list_categories()
        assert "architecture" in categories
        assert "process" in categories
        assert "domain" in categories

    def test_sorted_categories(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        categories = kb.list_categories()
        assert categories == sorted(categories)

    def test_empty_categories(self, knowledge_base):
        categories = knowledge_base.list_categories()
        assert categories == []

    def test_single_category(self, knowledge_base):
        entry = _make_kb_entry(category="only-one")
        knowledge_base.add_knowledge(entry)
        categories = knowledge_base.list_categories()
        assert categories == ["only-one"]

    def test_no_duplicates(self, knowledge_base):
        for _ in range(3):
            entry = _make_kb_entry(category="same")
            knowledge_base.add_knowledge(entry)
        categories = knowledge_base.list_categories()
        assert categories.count("same") == 1


# ==================================================================
# TestListByCategory
# ==================================================================


class TestListByCategory:
    """Tests for KnowledgeBase.list_by_category()."""

    def test_filtered_list(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.list_by_category("architecture")
        assert len(results) >= 1
        assert all(r.category == "architecture" for r in results)

    def test_empty_category(self, knowledge_base):
        results = knowledge_base.list_by_category("nonexistent")
        assert results == []

    def test_limit(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.list_by_category("architecture", limit=1)
        assert len(results) <= 1

    def test_returns_knowledge_entries(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        results = kb.list_by_category("process")
        for r in results:
            assert isinstance(r, KnowledgeEntry)


# ==================================================================
# TestExtractLearnings
# ==================================================================


class TestExtractLearnings:
    """Tests for KnowledgeBase.extract_learnings()."""

    def test_pattern_extraction_decided(self, knowledge_base):
        summary = "We decided to use PostgreSQL for the database."
        results = knowledge_base.extract_learnings(summary, "sprint-1")
        assert len(results) >= 1
        assert any("decided" in r.content.lower() for r in results)

    def test_pattern_extraction_learned(self, knowledge_base):
        summary = "The team learned that caching improves performance."
        results = knowledge_base.extract_learnings(summary, "sprint-2")
        assert len(results) >= 1

    def test_pattern_extraction_key_finding(self, knowledge_base):
        summary = "Key finding: Users prefer dark mode."
        results = knowledge_base.extract_learnings(summary, "research")
        assert len(results) >= 1

    def test_pattern_extraction_outcome(self, knowledge_base):
        summary = "Outcome: Deployment succeeded with zero downtime."
        results = knowledge_base.extract_learnings(summary, "deploy")
        assert len(results) >= 1

    def test_fallback_to_full_summary(self, knowledge_base):
        summary = "This is just a plain summary without any trigger words."
        results = knowledge_base.extract_learnings(summary, "general")
        assert len(results) == 1
        assert results[0].content == summary

    def test_empty_summary(self, knowledge_base):
        results = knowledge_base.extract_learnings("", "context")
        assert results == []

    def test_whitespace_only_summary(self, knowledge_base):
        results = knowledge_base.extract_learnings("   \n  ", "context")
        assert results == []

    def test_extracted_entries_not_stored(self, knowledge_base):
        summary = "We decided to refactor the payment module."
        results = knowledge_base.extract_learnings(summary, "sprint-3")
        stats = knowledge_base.get_stats()
        assert stats["total_entries"] == 0  # Not persisted

    def test_auto_extracted_category(self, knowledge_base):
        summary = "We decided to adopt TypeScript."
        results = knowledge_base.extract_learnings(summary, "review")
        for r in results:
            assert r.category == "auto-extracted"
            assert "auto-extracted" in r.tags

    def test_multiple_patterns(self, knowledge_base):
        summary = (
            "We decided to use Kubernetes. "
            "Key finding: Horizontal scaling is essential. "
            "Lesson: Always set resource limits."
        )
        results = knowledge_base.extract_learnings(summary, "arch-review")
        assert len(results) >= 2  # At least 2 patterns matched

    def test_source_context_preserved(self, knowledge_base):
        summary = "We decided to migrate to AWS."
        results = knowledge_base.extract_learnings(summary, "cloud-review")
        for r in results:
            assert r.source_context == "cloud-review"

    def test_profile_name_set(self, knowledge_base):
        summary = "Found that rate limiting helps."
        results = knowledge_base.extract_learnings(summary, "perf")
        for r in results:
            assert r.profile_name == "ceo"


# ==================================================================
# TestGetStats
# ==================================================================


class TestGetStats:
    """Tests for KnowledgeBase.get_stats()."""

    def test_empty_stats(self, knowledge_base):
        stats = knowledge_base.get_stats()
        assert stats["total_entries"] == 0
        assert stats["by_category"] == {}
        assert stats["total_bytes"] == 0

    def test_correct_counts(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        stats = kb.get_stats()
        assert stats["total_entries"] == 5

    def test_by_category_breakdown(self, populated_knowledge_base):
        kb, entries = populated_knowledge_base
        stats = kb.get_stats()
        assert stats["by_category"]["architecture"] == 2
        assert stats["by_category"]["process"] == 2
        assert stats["by_category"]["domain"] == 1

    def test_total_bytes_calculated(self, knowledge_base):
        entry = _make_kb_entry(content="Known content length")
        knowledge_base.add_knowledge(entry)
        stats = knowledge_base.get_stats()
        assert stats["total_bytes"] == len("Known content length")

    def test_stats_after_delete(self, knowledge_base):
        entry = _make_kb_entry()
        stored = knowledge_base.add_knowledge(entry)
        knowledge_base.delete_knowledge(stored.entry_id)
        stats = knowledge_base.get_stats()
        assert stats["total_entries"] == 0


# ==================================================================
# TestClose
# ==================================================================


class TestClose:
    """Tests for KnowledgeBase.close()."""

    def test_close_works(self):
        kb = KnowledgeBase(":memory:", "ceo")
        entry = _make_kb_entry()
        kb.add_knowledge(entry)
        kb.close()


# ==================================================================
# TestEdgeCases
# ==================================================================


class TestEdgeCases:
    """Additional edge case tests."""

    def test_special_characters_in_content(self, knowledge_base):
        content = "SQL test: '; DROP TABLE knowledge_base; --"
        entry = _make_kb_entry(content=content)
        stored = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(stored.entry_id)
        assert retrieved.content == content

    def test_unicode_content(self, knowledge_base):
        entry = _make_kb_entry(content="日本語テスト 🎉")
        stored = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(stored.entry_id)
        assert retrieved.content == "日本語テスト 🎉"

    def test_empty_content(self, knowledge_base):
        entry = _make_kb_entry(content="")
        stored = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(stored.entry_id)
        assert retrieved.content == ""

    def test_large_tags_list(self, knowledge_base):
        tags = [f"tag-{i}" for i in range(50)]
        entry = _make_kb_entry(tags=tags)
        stored = knowledge_base.add_knowledge(entry)
        retrieved = knowledge_base.get_knowledge(stored.entry_id)
        assert len(retrieved.tags) == 50
