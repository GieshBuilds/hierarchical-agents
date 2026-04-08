"""Tests for Protocol conformance in the memory subsystem.

Verifies that concrete implementations conform to the runtime-checkable
Protocol types defined in core.memory.interface.
"""
from __future__ import annotations

import pytest

from core.memory.context_manager import ContextManager
from core.memory.interface import (
    ContextProvider,
    KnowledgeProvider,
    MemoryLifecycleManager,
    MemoryProvider,
)
from core.memory.knowledge_base import KnowledgeBase
from core.memory.memory_store import MemoryStore
from core.memory.models import (
    ContextBrief,
    KnowledgeEntry,
    MemoryEntry,
    MemoryEntryType,
    MemoryScope,
    MemoryTier,
    StatusSummary,
    generate_knowledge_id,
    generate_memory_id,
)
from core.memory.tiered_storage import TieredStorage


# ==================================================================
# TestMemoryProvider
# ==================================================================


class TestMemoryProvider:
    """Tests that MemoryStore conforms to MemoryProvider protocol."""

    def test_isinstance_check(self, memory_store):
        assert isinstance(memory_store, MemoryProvider)

    def test_has_store_method(self, memory_store):
        assert callable(getattr(memory_store, "store", None))

    def test_has_get_method(self, memory_store):
        assert callable(getattr(memory_store, "get", None))

    def test_has_search_method(self, memory_store):
        assert callable(getattr(memory_store, "search", None))

    def test_has_delete_method(self, memory_store):
        assert callable(getattr(memory_store, "delete", None))

    def test_has_list_entries_method(self, memory_store):
        assert callable(getattr(memory_store, "list_entries", None))

    def test_has_get_stats_method(self, memory_store):
        assert callable(getattr(memory_store, "get_stats", None))

    def test_store_returns_memory_entry(self, memory_store):
        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Protocol test",
        )
        result = memory_store.store(entry)
        assert isinstance(result, MemoryEntry)

    def test_get_returns_memory_entry(self, memory_store):
        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Protocol test",
        )
        stored = memory_store.store(entry)
        result = memory_store.get(stored.entry_id)
        assert isinstance(result, MemoryEntry)

    def test_search_returns_list(self, memory_store):
        result = memory_store.search("test")
        assert isinstance(result, list)

    def test_get_stats_returns_dict(self, memory_store):
        result = memory_store.get_stats()
        assert isinstance(result, dict)


# ==================================================================
# TestKnowledgeProvider
# ==================================================================


class TestKnowledgeProvider:
    """Tests that KnowledgeBase conforms to KnowledgeProvider protocol."""

    def test_isinstance_check(self, knowledge_base):
        assert isinstance(knowledge_base, KnowledgeProvider)

    def test_has_add_knowledge_method(self, knowledge_base):
        assert callable(getattr(knowledge_base, "add_knowledge", None))

    def test_has_get_knowledge_method(self, knowledge_base):
        assert callable(getattr(knowledge_base, "get_knowledge", None))

    def test_has_search_knowledge_method(self, knowledge_base):
        assert callable(getattr(knowledge_base, "search_knowledge", None))

    def test_has_delete_knowledge_method(self, knowledge_base):
        assert callable(getattr(knowledge_base, "delete_knowledge", None))

    def test_has_list_categories_method(self, knowledge_base):
        assert callable(getattr(knowledge_base, "list_categories", None))

    def test_has_get_stats_method(self, knowledge_base):
        assert callable(getattr(knowledge_base, "get_stats", None))

    def test_add_returns_knowledge_entry(self, knowledge_base):
        entry = KnowledgeEntry(
            entry_id=generate_knowledge_id(),
            profile_name="ceo",
            category="test",
            title="Protocol Test",
            content="Testing knowledge provider",
        )
        result = knowledge_base.add_knowledge(entry)
        assert isinstance(result, KnowledgeEntry)

    def test_search_returns_list(self, knowledge_base):
        result = knowledge_base.search_knowledge("test")
        assert isinstance(result, list)

    def test_list_categories_returns_list(self, knowledge_base):
        result = knowledge_base.list_categories()
        assert isinstance(result, list)


# ==================================================================
# TestContextProvider
# ==================================================================


class TestContextProvider:
    """Tests that ContextManager conforms to ContextProvider protocol."""

    def test_isinstance_check(self, context_manager):
        assert isinstance(context_manager, ContextProvider)

    def test_has_build_activation_context(self, context_manager):
        assert callable(getattr(context_manager, "build_activation_context", None))

    def test_has_build_task_brief(self, context_manager):
        assert callable(getattr(context_manager, "build_task_brief", None))

    def test_has_build_upward_summary(self, context_manager):
        assert callable(getattr(context_manager, "build_upward_summary", None))

    def test_has_inject_context(self, context_manager):
        assert callable(getattr(context_manager, "inject_context", None))

    def test_build_activation_returns_context_brief(self, context_manager):
        result = context_manager.build_activation_context("ceo")
        assert isinstance(result, ContextBrief)

    def test_build_task_brief_returns_context_brief(self, context_manager):
        result = context_manager.build_task_brief("pm-alpha", "Do something")
        assert isinstance(result, ContextBrief)

    def test_build_upward_summary_returns_status_summary(self, context_manager):
        result = context_manager.build_upward_summary("pm-alpha")
        assert isinstance(result, StatusSummary)

    def test_inject_context_returns_string(self, context_manager):
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={"identity": "Test"},
        )
        result = context_manager.inject_context(brief)
        assert isinstance(result, str)


# ==================================================================
# TestMemoryLifecycleManager
# ==================================================================


class TestMemoryLifecycleManager:
    """Tests that TieredStorage conforms to MemoryLifecycleManager protocol."""

    def test_isinstance_check(self, tiered_storage):
        assert isinstance(tiered_storage, MemoryLifecycleManager)

    def test_has_assess_tier(self, tiered_storage):
        assert callable(getattr(tiered_storage, "assess_tier", None))

    def test_has_run_tier_assessment(self, tiered_storage):
        assert callable(getattr(tiered_storage, "run_tier_assessment", None))

    def test_has_apply_transitions(self, tiered_storage):
        assert callable(getattr(tiered_storage, "apply_transitions", None))

    def test_has_get_tier_stats(self, tiered_storage):
        assert callable(getattr(tiered_storage, "get_tier_stats", None))

    def test_assess_tier_returns_memory_tier(self, tiered_storage):
        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Tier test",
        )
        result = tiered_storage.assess_tier(entry)
        assert isinstance(result, MemoryTier)

    def test_run_tier_assessment_returns_list(self, tiered_storage, memory_store):
        result = tiered_storage.run_tier_assessment(memory_store)
        assert isinstance(result, list)

    def test_apply_transitions_returns_list(self, tiered_storage, memory_store):
        result = tiered_storage.apply_transitions(memory_store, [])
        assert isinstance(result, list)

    def test_get_tier_stats_returns_dict(self, tiered_storage, memory_store):
        result = tiered_storage.get_tier_stats(memory_store)
        assert isinstance(result, dict)


# ==================================================================
# TestProtocolNonConformance
# ==================================================================


class TestProtocolNonConformance:
    """Tests that arbitrary objects do NOT satisfy protocols."""

    def test_plain_object_not_memory_provider(self):
        assert not isinstance(object(), MemoryProvider)

    def test_plain_object_not_knowledge_provider(self):
        assert not isinstance(object(), KnowledgeProvider)

    def test_plain_object_not_context_provider(self):
        assert not isinstance(object(), ContextProvider)

    def test_plain_object_not_lifecycle_manager(self):
        assert not isinstance(object(), MemoryLifecycleManager)

    def test_dict_not_memory_provider(self):
        assert not isinstance({}, MemoryProvider)

    def test_string_not_knowledge_provider(self):
        assert not isinstance("hello", KnowledgeProvider)
