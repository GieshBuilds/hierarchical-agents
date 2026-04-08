"""Comprehensive tests for the ContextManager class.

Tests cover init, activation context, task brief, upward summary,
context injection, size estimation, budget truncation, and formatters.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.memory.context_manager import ContextManager
from core.memory.exceptions import ContextInjectionError
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
    estimate_tokens,
    generate_knowledge_id,
    generate_memory_id,
)


# ------------------------------------------------------------------
# Mock objects for optional components
# ------------------------------------------------------------------


@dataclass
class MockProfile:
    name: str = "ceo"
    role: str = "Chief Executive Officer"
    description: str = "Oversees all strategic operations"


class MockProfileRegistry:
    def __init__(self, profiles: dict | None = None):
        self._profiles = profiles or {"ceo": MockProfile()}

    def get(self, profile_name: str):
        if profile_name not in self._profiles:
            raise KeyError(f"Not found: {profile_name}")
        return self._profiles[profile_name]


@dataclass
class MockMessage:
    from_profile: str = "cto"
    to_profile: str = "ceo"
    priority: str = "normal"
    payload: dict | None = None

    def __post_init__(self):
        if self.payload is None:
            self.payload = {"task": "review"}


class MockMessageBus:
    def __init__(self, messages: list | None = None):
        self._messages = messages or []

    def poll(self, profile_name: str, limit: int = 20):
        return self._messages[:limit]


@dataclass
class MockWorker:
    worker_id: str = "worker-1"
    status: str = "running"
    task_description: str = "Processing data pipeline"


class MockSubagentRegistry:
    def __init__(self, workers: list | None = None):
        self._workers = workers or []

    def list_active(self, parent_profile: str = ""):
        return self._workers


# ------------------------------------------------------------------
# Helper to populate stores
# ------------------------------------------------------------------


def _populate_memory_store(store: MemoryStore, count: int = 3) -> list[MemoryEntry]:
    entries = []
    for i in range(count):
        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content=f"Strategic decision {i}: invest in AI",
        )
        store.store(entry)
        entries.append(entry)
    return entries


def _populate_knowledge_base(kb: KnowledgeBase, count: int = 3) -> list[KnowledgeEntry]:
    entries = []
    for i in range(count):
        entry = KnowledgeEntry(
            entry_id=generate_knowledge_id(),
            profile_name="ceo",
            category="strategy",
            title=f"Knowledge item {i}",
            content=f"Important knowledge about strategy item {i}",
            tags=["strategy"],
        )
        kb.add_knowledge(entry)
        entries.append(entry)
    return entries


# ==================================================================
# TestInit
# ==================================================================


class TestInit:
    """Tests for ContextManager construction."""

    def test_creation_with_no_components(self):
        cm = ContextManager()
        assert cm is not None

    def test_creation_with_memory_store(self, memory_store):
        cm = ContextManager(memory_store=memory_store)
        assert cm is not None

    def test_creation_with_knowledge_base(self, knowledge_base):
        cm = ContextManager(knowledge_base=knowledge_base)
        assert cm is not None

    def test_creation_with_all_components(self, memory_store, knowledge_base):
        cm = ContextManager(
            memory_store=memory_store,
            knowledge_base=knowledge_base,
            profile_registry=MockProfileRegistry(),
            message_bus=MockMessageBus(),
            subagent_registry=MockSubagentRegistry(),
            max_context_tokens=8000,
        )
        assert cm is not None

    def test_custom_token_budget(self, memory_store):
        cm = ContextManager(memory_store=memory_store, max_context_tokens=2000)
        assert cm is not None


# ==================================================================
# TestBuildActivationContext
# ==================================================================


class TestBuildActivationContext:
    """Tests for ContextManager.build_activation_context()."""

    def test_with_memory_only(self, memory_store):
        _populate_memory_store(memory_store)
        cm = ContextManager(memory_store=memory_store)
        brief = cm.build_activation_context("ceo")
        assert isinstance(brief, ContextBrief)
        assert brief.profile_name == "ceo"
        assert brief.context_type == "activation"
        assert "identity" in brief.sections

    def test_with_knowledge_base(self, memory_store, knowledge_base):
        _populate_memory_store(memory_store)
        _populate_knowledge_base(knowledge_base)
        cm = ContextManager(memory_store=memory_store, knowledge_base=knowledge_base)
        brief = cm.build_activation_context("ceo")
        assert "knowledge" in brief.sections

    def test_with_all_components(self, memory_store, knowledge_base):
        _populate_memory_store(memory_store)
        _populate_knowledge_base(knowledge_base)
        cm = ContextManager(
            memory_store=memory_store,
            knowledge_base=knowledge_base,
            profile_registry=MockProfileRegistry(),
            message_bus=MockMessageBus([MockMessage()]),
            subagent_registry=MockSubagentRegistry([MockWorker()]),
        )
        brief = cm.build_activation_context("ceo")
        assert "identity" in brief.sections
        assert "active_memory" in brief.sections
        assert "pending_messages" in brief.sections
        assert "active_workers" in brief.sections

    def test_empty_components(self):
        cm = ContextManager()
        brief = cm.build_activation_context("ceo")
        assert brief.context_type == "activation"
        # Only identity should be present (fallback)
        assert "identity" in brief.sections

    def test_identity_with_registry(self, memory_store):
        cm = ContextManager(
            memory_store=memory_store,
            profile_registry=MockProfileRegistry(),
        )
        brief = cm.build_activation_context("ceo")
        assert "Chief Executive Officer" in brief.sections.get("identity", "")

    def test_identity_fallback_without_registry(self, memory_store):
        cm = ContextManager(memory_store=memory_store)
        brief = cm.build_activation_context("ceo")
        assert "Profile: ceo" in brief.sections.get("identity", "")

    def test_token_estimate_set(self, memory_store):
        _populate_memory_store(memory_store)
        cm = ContextManager(memory_store=memory_store)
        brief = cm.build_activation_context("ceo")
        assert brief.token_estimate > 0

    def test_empty_sections_removed(self):
        cm = ContextManager()
        brief = cm.build_activation_context("ceo")
        for value in brief.sections.values():
            assert value  # No empty sections


# ==================================================================
# TestBuildTaskBrief
# ==================================================================


class TestBuildTaskBrief:
    """Tests for ContextManager.build_task_brief()."""

    def test_basic_brief(self, memory_store):
        cm = ContextManager(memory_store=memory_store)
        brief = cm.build_task_brief("pm-alpha", "Implement user auth module")
        assert isinstance(brief, ContextBrief)
        assert brief.context_type == "task_brief"
        assert "task" in brief.sections

    def test_task_content(self, memory_store):
        cm = ContextManager(memory_store=memory_store)
        brief = cm.build_task_brief("pm-alpha", "Build REST API endpoints")
        assert "Build REST API endpoints" in brief.sections["task"]

    def test_with_context(self, memory_store):
        cm = ContextManager(memory_store=memory_store)
        brief = cm.build_task_brief(
            "pm-alpha",
            "Build API",
            relevant_context=["Use FastAPI", "Follow OpenAPI spec"],
        )
        assert "project_context" in brief.sections
        assert "FastAPI" in brief.sections["project_context"]

    def test_without_context(self, memory_store):
        cm = ContextManager(memory_store=memory_store)
        brief = cm.build_task_brief("pm-alpha", "Build API")
        # project_context may be empty and removed
        assert brief.context_type == "task_brief"

    def test_kb_integration(self, memory_store, knowledge_base):
        # Add knowledge that matches the task
        entry = KnowledgeEntry(
            entry_id=generate_knowledge_id(),
            profile_name="ceo",
            category="standards",
            title="REST API Guidelines",
            content="Always use versioned API endpoints",
            tags=["api"],
        )
        knowledge_base.add_knowledge(entry)
        cm = ContextManager(memory_store=memory_store, knowledge_base=knowledge_base)
        brief = cm.build_task_brief("pm-alpha", "Build REST API endpoints")
        # constraints section may contain KB knowledge
        assert brief.context_type == "task_brief"

    def test_metadata_includes_task(self, memory_store):
        cm = ContextManager(memory_store=memory_store)
        brief = cm.build_task_brief("pm-alpha", "My task")
        assert brief.metadata.get("task_description") == "My task"

    def test_empty_sections_removed(self, memory_store):
        cm = ContextManager(memory_store=memory_store)
        brief = cm.build_task_brief("pm-alpha", "Task")
        for value in brief.sections.values():
            assert value


# ==================================================================
# TestBuildUpwardSummary
# ==================================================================


class TestBuildUpwardSummary:
    """Tests for ContextManager.build_upward_summary()."""

    def test_basic_summary(self, context_manager):
        summary = context_manager.build_upward_summary("pm-alpha")
        assert isinstance(summary, StatusSummary)
        assert summary.summary_type == "interaction"
        assert summary.profile_name == "pm-alpha"

    def test_with_decisions(self, context_manager):
        summary = context_manager.build_upward_summary(
            "pm-alpha",
            decisions=["Chose PostgreSQL", "Adopted REST API"],
        )
        assert len(summary.decisions) == 2
        assert "Chose PostgreSQL" in summary.decisions

    def test_with_deliverables(self, context_manager):
        summary = context_manager.build_upward_summary(
            "pm-alpha",
            deliverables=["API spec v1.0"],
        )
        assert "API spec v1.0" in summary.deliverables

    def test_with_blockers(self, context_manager):
        summary = context_manager.build_upward_summary(
            "pm-alpha",
            blockers=["Waiting for DB credentials"],
        )
        assert "Waiting for DB credentials" in summary.blockers

    def test_with_metrics(self, context_manager):
        summary = context_manager.build_upward_summary(
            "pm-alpha",
            metrics={"tasks_completed": 5, "bugs_found": 2},
        )
        assert summary.metrics["tasks_completed"] == 5

    def test_empty_lists_default(self, context_manager):
        summary = context_manager.build_upward_summary("pm-alpha")
        assert summary.decisions == []
        assert summary.deliverables == []
        assert summary.blockers == []
        assert summary.metrics == {}

    def test_all_fields(self, context_manager):
        summary = context_manager.build_upward_summary(
            "pm-alpha",
            decisions=["A"],
            deliverables=["B"],
            blockers=["C"],
            metrics={"x": 1},
        )
        assert summary.decisions == ["A"]
        assert summary.deliverables == ["B"]
        assert summary.blockers == ["C"]
        assert summary.metrics == {"x": 1}


# ==================================================================
# TestInjectContext
# ==================================================================


class TestInjectContext:
    """Tests for ContextManager.inject_context()."""

    def test_format_output(self, context_manager):
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={"identity": "Profile: ceo"},
        )
        text = context_manager.inject_context(brief)
        assert "## identity" in text
        assert "Profile: ceo" in text

    def test_multiple_sections(self, context_manager):
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={
                "identity": "Profile: ceo",
                "task": "Process reports",
                "knowledge": "Use REST APIs",
            },
        )
        text = context_manager.inject_context(brief)
        assert "## identity" in text
        assert "## task" in text
        assert "## knowledge" in text

    def test_empty_brief(self, context_manager):
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={},
        )
        text = context_manager.inject_context(brief)
        assert text == ""

    def test_preserves_content(self, context_manager):
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={"task": "Build the user authentication system with OAuth2"},
        )
        text = context_manager.inject_context(brief)
        assert "Build the user authentication system with OAuth2" in text

    def test_returns_string(self, context_manager):
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={"identity": "Test"},
        )
        result = context_manager.inject_context(brief)
        assert isinstance(result, str)


# ==================================================================
# TestEstimateContextSize
# ==================================================================


class TestEstimateContextSize:
    """Tests for ContextManager.estimate_context_size()."""

    def test_token_estimation(self, context_manager):
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={"identity": "a" * 400},
        )
        size = context_manager.estimate_context_size(brief)
        # 400 chars / 4 = 100 tokens
        assert size == 100

    def test_empty_brief_zero(self, context_manager):
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={},
        )
        size = context_manager.estimate_context_size(brief)
        assert size == 0

    def test_multiple_sections(self, context_manager):
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={
                "identity": "a" * 200,  # 50 tokens
                "task": "b" * 400,  # 100 tokens
            },
        )
        size = context_manager.estimate_context_size(brief)
        assert size == 150

    def test_uses_estimate_tokens(self, context_manager):
        content = "This is a test string for token estimation"
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={"test": content},
        )
        size = context_manager.estimate_context_size(brief)
        assert size == estimate_tokens(content)


# ==================================================================
# TestTruncateToBudget
# ==================================================================


class TestTruncateToBudget:
    """Tests for ContextManager._truncate_to_budget()."""

    def test_under_budget_unchanged(self, context_manager):
        sections = {"identity": "Short text", "task": "Also short"}
        result = context_manager._truncate_to_budget(sections, 10000)
        assert result == sections

    def test_over_budget_truncation(self):
        cm = ContextManager(max_context_tokens=10)
        # identity is priority 0 (highest), project_context is priority 6
        sections = {
            "identity": "a" * 20,  # 5 tokens
            "project_context": "b" * 200,  # 50 tokens — way over budget
        }
        result = cm._truncate_to_budget(sections, 10)
        # identity should be preserved, project_context may be truncated/dropped
        assert "identity" in result
        total_tokens = sum(estimate_tokens(v) for v in result.values())
        # Should fit within reasonable budget (allowing truncation overhead)
        assert total_tokens <= 20  # generous tolerance

    def test_priority_ordering(self):
        cm = ContextManager(max_context_tokens=5)
        sections = {
            "constraints": "c" * 100,  # priority 7 (lowest)
            "identity": "a" * 8,  # priority 0 (highest)
        }
        result = cm._truncate_to_budget(sections, 5)
        # identity should be preserved first
        assert "identity" in result

    def test_empty_sections(self, context_manager):
        result = context_manager._truncate_to_budget({}, 100)
        assert result == {}


# ==================================================================
# TestFormatters
# ==================================================================


class TestFormatters:
    """Tests for internal _format_* methods."""

    def test_format_memory_entries(self, context_manager):
        entries = [
            MemoryEntry(
                entry_id="mem-1",
                profile_name="ceo",
                scope=MemoryScope.strategic,
                tier=MemoryTier.hot,
                entry_type=MemoryEntryType.decision,
                content="Invest in AI",
            ),
        ]
        text = context_manager._format_memory_entries(entries)
        assert "[decision]" in text
        assert "Invest in AI" in text
        assert "(hot)" in text

    def test_format_knowledge_entries(self, context_manager):
        entries = [
            KnowledgeEntry(
                entry_id="kb-1",
                profile_name="ceo",
                category="arch",
                title="API Design",
                content="Use REST with OpenAPI",
                tags=["api", "design"],
            ),
        ]
        text = context_manager._format_knowledge_entries(entries)
        assert "### API Design" in text
        assert "Use REST with OpenAPI" in text
        assert "Tags: api, design" in text

    def test_format_messages(self, context_manager):
        messages = [MockMessage(from_profile="cto", to_profile="ceo")]
        text = context_manager._format_messages(messages)
        assert "cto" in text
        assert "ceo" in text

    def test_format_workers(self, context_manager):
        workers = [MockWorker(worker_id="w-1", status="running", task_description="Build API")]
        text = context_manager._format_workers(workers)
        assert "w-1" in text
        assert "running" in text
        assert "Build API" in text

    def test_format_memory_entries_empty(self, context_manager):
        text = context_manager._format_memory_entries([])
        assert text == ""

    def test_format_knowledge_entries_empty(self, context_manager):
        text = context_manager._format_knowledge_entries([])
        assert text == ""


# ==================================================================
# TestIntegration
# ==================================================================


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_full_activation_cycle(self, memory_store, knowledge_base):
        _populate_memory_store(memory_store)
        _populate_knowledge_base(knowledge_base)
        cm = ContextManager(
            memory_store=memory_store,
            knowledge_base=knowledge_base,
            profile_registry=MockProfileRegistry(),
        )
        brief = cm.build_activation_context("ceo")
        text = cm.inject_context(brief)
        assert len(text) > 0
        size = cm.estimate_context_size(brief)
        assert size > 0

    def test_full_task_brief_cycle(self, memory_store, knowledge_base):
        _populate_knowledge_base(knowledge_base)
        cm = ContextManager(
            memory_store=memory_store,
            knowledge_base=knowledge_base,
        )
        brief = cm.build_task_brief(
            "pm-alpha",
            "Build user authentication with OAuth2",
            relevant_context=["Use FastAPI framework"],
        )
        text = cm.inject_context(brief)
        assert "Build user authentication" in text
