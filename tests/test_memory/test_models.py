"""Tests for memory data models, enums, constants, and helpers."""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone, timedelta

from core.memory.models import (
    MemoryScope,
    MemoryTier,
    MemoryEntryType,
    MemoryEntry,
    KnowledgeEntry,
    MemoryBudget,
    ContextBrief,
    StatusSummary,
    TierTransition,
    GCReport,
    WARM_AGE_DAYS,
    COOL_AGE_DAYS,
    COLD_AGE_DAYS,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_MAX_BYTES,
    VALID_TIER_TRANSITIONS,
    ROLE_SCOPE_MAP,
    generate_memory_id,
    generate_knowledge_id,
    generate_transition_id,
    scope_for_role,
    is_valid_tier_transition,
    estimate_tokens,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestMemoryScope:
    """Tests for the MemoryScope enum."""

    def test_values(self) -> None:
        assert MemoryScope.strategic == "strategic"
        assert MemoryScope.domain == "domain"
        assert MemoryScope.project == "project"
        assert MemoryScope.task == "task"

    def test_str_compatibility(self) -> None:
        """MemoryScope values are also str instances."""
        assert isinstance(MemoryScope.strategic, str)
        assert isinstance(MemoryScope.domain, str)

    def test_count(self) -> None:
        assert len(MemoryScope) == 4


class TestMemoryTier:
    """Tests for the MemoryTier enum."""

    def test_values(self) -> None:
        assert MemoryTier.hot == "hot"
        assert MemoryTier.warm == "warm"
        assert MemoryTier.cool == "cool"
        assert MemoryTier.cold == "cold"

    def test_str_compatibility(self) -> None:
        assert isinstance(MemoryTier.hot, str)

    def test_count(self) -> None:
        assert len(MemoryTier) == 4

    def test_ordering(self) -> None:
        """Tiers are ordered hot -> warm -> cool -> cold."""
        tiers = list(MemoryTier)
        assert tiers == [
            MemoryTier.hot,
            MemoryTier.warm,
            MemoryTier.cool,
            MemoryTier.cold,
        ]


class TestMemoryEntryType:
    """Tests for the MemoryEntryType enum."""

    def test_values(self) -> None:
        assert MemoryEntryType.preference == "preference"
        assert MemoryEntryType.decision == "decision"
        assert MemoryEntryType.learning == "learning"
        assert MemoryEntryType.context == "context"
        assert MemoryEntryType.summary == "summary"
        assert MemoryEntryType.artifact == "artifact"

    def test_str_compatibility(self) -> None:
        assert isinstance(MemoryEntryType.decision, str)

    def test_count(self) -> None:
        assert len(MemoryEntryType) == 6


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for module-level constants."""

    def test_warm_age_days(self) -> None:
        assert WARM_AGE_DAYS == 0

    def test_cool_age_days(self) -> None:
        assert COOL_AGE_DAYS == 30

    def test_cold_age_days(self) -> None:
        assert COLD_AGE_DAYS == 90

    def test_default_max_entries(self) -> None:
        assert DEFAULT_MAX_ENTRIES == 1000

    def test_default_max_bytes(self) -> None:
        assert DEFAULT_MAX_BYTES == 10 * 1024 * 1024

    def test_valid_tier_transitions_hot(self) -> None:
        assert VALID_TIER_TRANSITIONS[MemoryTier.hot] == {MemoryTier.warm}

    def test_valid_tier_transitions_warm(self) -> None:
        assert VALID_TIER_TRANSITIONS[MemoryTier.warm] == {MemoryTier.cool}

    def test_valid_tier_transitions_cool(self) -> None:
        assert VALID_TIER_TRANSITIONS[MemoryTier.cool] == {MemoryTier.cold}

    def test_valid_tier_transitions_cold_terminal(self) -> None:
        assert VALID_TIER_TRANSITIONS[MemoryTier.cold] == set()

    def test_role_scope_map_ceo(self) -> None:
        assert ROLE_SCOPE_MAP["ceo"] == MemoryScope.strategic

    def test_role_scope_map_department_head(self) -> None:
        assert ROLE_SCOPE_MAP["department_head"] == MemoryScope.domain

    def test_role_scope_map_project_manager(self) -> None:
        assert ROLE_SCOPE_MAP["project_manager"] == MemoryScope.project

    def test_role_scope_map_worker(self) -> None:
        assert ROLE_SCOPE_MAP["worker"] == MemoryScope.task


# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------


class TestGenerateMemoryId:
    """Tests for the generate_memory_id helper."""

    def test_prefix(self) -> None:
        mid = generate_memory_id()
        assert mid.startswith("mem-")

    def test_length(self) -> None:
        mid = generate_memory_id()
        assert len(mid) == 12  # mem- (4) + 8 hex chars

    def test_unique(self) -> None:
        ids = {generate_memory_id() for _ in range(100)}
        assert len(ids) == 100


class TestGenerateKnowledgeId:
    """Tests for the generate_knowledge_id helper."""

    def test_prefix(self) -> None:
        kid = generate_knowledge_id()
        assert kid.startswith("kb-")

    def test_length(self) -> None:
        kid = generate_knowledge_id()
        assert len(kid) == 11  # kb- (3) + 8 hex chars

    def test_unique(self) -> None:
        ids = {generate_knowledge_id() for _ in range(100)}
        assert len(ids) == 100


class TestGenerateTransitionId:
    """Tests for the generate_transition_id helper."""

    def test_prefix(self) -> None:
        tid = generate_transition_id()
        assert tid.startswith("tt-")

    def test_length(self) -> None:
        tid = generate_transition_id()
        assert len(tid) == 11  # tt- (3) + 8 hex chars

    def test_unique(self) -> None:
        ids = {generate_transition_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for public helper functions."""

    def test_scope_for_role_ceo(self) -> None:
        assert scope_for_role("ceo") == MemoryScope.strategic

    def test_scope_for_role_department_head(self) -> None:
        assert scope_for_role("department_head") == MemoryScope.domain

    def test_scope_for_role_project_manager(self) -> None:
        assert scope_for_role("project_manager") == MemoryScope.project

    def test_scope_for_role_worker(self) -> None:
        assert scope_for_role("worker") == MemoryScope.task

    def test_scope_for_role_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown role"):
            scope_for_role("intern")

    def test_is_valid_tier_transition_hot_to_warm(self) -> None:
        assert is_valid_tier_transition(MemoryTier.hot, MemoryTier.warm) is True

    def test_is_valid_tier_transition_warm_to_cool(self) -> None:
        assert is_valid_tier_transition(MemoryTier.warm, MemoryTier.cool) is True

    def test_is_valid_tier_transition_cool_to_cold(self) -> None:
        assert is_valid_tier_transition(MemoryTier.cool, MemoryTier.cold) is True

    def test_is_valid_tier_transition_cold_terminal(self) -> None:
        assert is_valid_tier_transition(MemoryTier.cold, MemoryTier.hot) is False

    def test_is_valid_tier_transition_backward_invalid(self) -> None:
        assert is_valid_tier_transition(MemoryTier.warm, MemoryTier.hot) is False

    def test_is_valid_tier_transition_skip_tier(self) -> None:
        assert is_valid_tier_transition(MemoryTier.hot, MemoryTier.cool) is False

    def test_estimate_tokens_simple(self) -> None:
        text = "a" * 100
        assert estimate_tokens(text) == 25

    def test_estimate_tokens_empty(self) -> None:
        assert estimate_tokens("") == 0

    def test_estimate_tokens_short(self) -> None:
        assert estimate_tokens("hi") == 0  # 2 // 4 == 0


# ---------------------------------------------------------------------------
# MemoryEntry
# ---------------------------------------------------------------------------


class TestMemoryEntry:
    """Tests for the MemoryEntry dataclass."""

    def test_creation(self, sample_memory_entry) -> None:
        """Basic creation produces valid entry."""
        assert sample_memory_entry.entry_id.startswith("mem-")
        assert sample_memory_entry.profile_name == "ceo"
        assert sample_memory_entry.scope == MemoryScope.strategic
        assert sample_memory_entry.tier == MemoryTier.hot
        assert sample_memory_entry.entry_type == MemoryEntryType.decision
        assert sample_memory_entry.content == "Decided to prioritize Counter-Liquid launch"

    def test_post_init_byte_size_auto(self) -> None:
        """byte_size is auto-calculated if not provided."""
        entry = MemoryEntry(
            entry_id="mem-test1234",
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Hello",
        )
        assert entry.byte_size == 5

    def test_post_init_byte_size_unicode(self) -> None:
        """byte_size counts UTF-8 bytes, not characters."""
        entry = MemoryEntry(
            entry_id="mem-test1234",
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="café",  # é is 2 bytes in UTF-8
        )
        assert entry.byte_size == 5  # c(1) + a(1) + f(1) + é(2)

    def test_post_init_byte_size_explicit(self) -> None:
        """Explicit byte_size is preserved."""
        entry = MemoryEntry(
            entry_id="mem-test1234",
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Hello",
            byte_size=999,
        )
        assert entry.byte_size == 999

    def test_default_metadata(self) -> None:
        entry = MemoryEntry(
            entry_id="mem-test1234",
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Hello",
        )
        assert entry.metadata == {}

    def test_default_timestamps(self) -> None:
        """Timestamps are auto-generated."""
        entry = MemoryEntry(
            entry_id="mem-test1234",
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Hello",
        )
        assert entry.created_at is not None
        assert entry.updated_at is not None
        assert entry.accessed_at is not None
        assert entry.expires_at is None

    def test_is_expired_no_expiry(self, sample_memory_entry) -> None:
        assert sample_memory_entry.is_expired() is False

    def test_is_expired_future(self) -> None:
        entry = MemoryEntry(
            entry_id="mem-test1234",
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Hello",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert entry.is_expired() is False

    def test_is_expired_past(self) -> None:
        entry = MemoryEntry(
            entry_id="mem-test1234",
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Hello",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert entry.is_expired() is True

    def test_can_transition_to_valid(self, sample_memory_entry) -> None:
        """Hot tier can transition to warm."""
        assert sample_memory_entry.can_transition_to(MemoryTier.warm) is True

    def test_can_transition_to_invalid(self, sample_memory_entry) -> None:
        """Hot tier cannot transition to cold (skip)."""
        assert sample_memory_entry.can_transition_to(MemoryTier.cold) is False

    def test_can_transition_to_same_tier(self, sample_memory_entry) -> None:
        """Cannot transition to the same tier."""
        assert sample_memory_entry.can_transition_to(MemoryTier.hot) is False

    def test_to_dict(self, sample_memory_entry) -> None:
        d = sample_memory_entry.to_dict()
        assert d["entry_id"] == sample_memory_entry.entry_id
        assert d["profile_name"] == "ceo"
        assert d["scope"] == "strategic"
        assert d["tier"] == "hot"
        assert d["entry_type"] == "decision"
        assert d["content"] == "Decided to prioritize Counter-Liquid launch"
        assert isinstance(d["created_at"], str)
        assert isinstance(d["byte_size"], int)

    def test_from_dict(self, sample_memory_entry) -> None:
        d = sample_memory_entry.to_dict()
        restored = MemoryEntry.from_dict(d)
        assert restored.entry_id == sample_memory_entry.entry_id
        assert restored.scope == sample_memory_entry.scope
        assert restored.tier == sample_memory_entry.tier

    def test_round_trip(self, sample_memory_entry) -> None:
        """to_dict -> from_dict produces equivalent object."""
        d = sample_memory_entry.to_dict()
        restored = MemoryEntry.from_dict(d)
        assert restored.to_dict() == d

    def test_to_dict_json_serializable(self, sample_memory_entry) -> None:
        """to_dict result must be JSON-serializable."""
        d = sample_memory_entry.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# KnowledgeEntry
# ---------------------------------------------------------------------------


class TestKnowledgeEntry:
    """Tests for the KnowledgeEntry dataclass."""

    def test_creation(self, sample_knowledge_entry) -> None:
        assert sample_knowledge_entry.entry_id.startswith("kb-")
        assert sample_knowledge_entry.profile_name == "cto"
        assert sample_knowledge_entry.category == "architecture"
        assert sample_knowledge_entry.title == "Database Migration Strategy"
        assert sample_knowledge_entry.tags == ["database", "migrations"]

    def test_default_source_fields(self) -> None:
        entry = KnowledgeEntry(
            entry_id="kb-test1234",
            profile_name="cto",
            category="arch",
            title="Title",
            content="Content",
        )
        assert entry.source_profile == ""
        assert entry.source_context == ""
        assert entry.tags == []

    def test_to_dict(self, sample_knowledge_entry) -> None:
        d = sample_knowledge_entry.to_dict()
        assert d["entry_id"] == sample_knowledge_entry.entry_id
        assert d["profile_name"] == "cto"
        assert d["category"] == "architecture"
        assert d["tags"] == ["database", "migrations"]
        assert isinstance(d["created_at"], str)

    def test_from_dict(self, sample_knowledge_entry) -> None:
        d = sample_knowledge_entry.to_dict()
        restored = KnowledgeEntry.from_dict(d)
        assert restored.entry_id == sample_knowledge_entry.entry_id
        assert restored.category == sample_knowledge_entry.category

    def test_round_trip(self, sample_knowledge_entry) -> None:
        d = sample_knowledge_entry.to_dict()
        restored = KnowledgeEntry.from_dict(d)
        assert restored.to_dict() == d

    def test_to_dict_json_serializable(self, sample_knowledge_entry) -> None:
        d = sample_knowledge_entry.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# MemoryBudget
# ---------------------------------------------------------------------------


class TestMemoryBudget:
    """Tests for the MemoryBudget dataclass."""

    def test_creation(self, sample_budget) -> None:
        assert sample_budget.profile_name == "ceo"

    def test_default_max_entries(self, sample_budget) -> None:
        assert sample_budget.max_entries == DEFAULT_MAX_ENTRIES

    def test_default_max_bytes(self, sample_budget) -> None:
        assert sample_budget.max_bytes == DEFAULT_MAX_BYTES

    def test_default_tier_quotas(self, sample_budget) -> None:
        assert sample_budget.tier_quotas == {
            "hot": 200,
            "warm": 300,
            "cool": 300,
            "cold": 200,
        }

    def test_custom_values(self) -> None:
        budget = MemoryBudget(
            profile_name="cto",
            max_entries=500,
            max_bytes=5 * 1024 * 1024,
            tier_quotas={"hot": 100, "warm": 150, "cool": 150, "cold": 100},
        )
        assert budget.max_entries == 500
        assert budget.max_bytes == 5 * 1024 * 1024

    def test_to_dict(self, sample_budget) -> None:
        d = sample_budget.to_dict()
        assert d["profile_name"] == "ceo"
        assert d["max_entries"] == DEFAULT_MAX_ENTRIES
        assert d["max_bytes"] == DEFAULT_MAX_BYTES
        assert "tier_quotas" in d

    def test_from_dict(self, sample_budget) -> None:
        d = sample_budget.to_dict()
        restored = MemoryBudget.from_dict(d)
        assert restored.profile_name == sample_budget.profile_name

    def test_round_trip(self, sample_budget) -> None:
        d = sample_budget.to_dict()
        restored = MemoryBudget.from_dict(d)
        assert restored.to_dict() == d

    def test_to_dict_json_serializable(self, sample_budget) -> None:
        d = sample_budget.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# ContextBrief
# ---------------------------------------------------------------------------


class TestContextBrief:
    """Tests for the ContextBrief dataclass."""

    def test_creation(self) -> None:
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={"role": "You are the CEO", "goals": "Maximize revenue"},
            token_estimate=50,
        )
        assert brief.profile_name == "ceo"
        assert brief.context_type == "activation"
        assert len(brief.sections) == 2
        assert brief.token_estimate == 50

    def test_default_values(self) -> None:
        brief = ContextBrief(profile_name="ceo", context_type="task_brief")
        assert brief.sections == {}
        assert brief.metadata == {}
        assert brief.token_estimate == 0
        assert brief.created_at is not None

    def test_to_dict(self) -> None:
        brief = ContextBrief(
            profile_name="ceo",
            context_type="activation",
            sections={"role": "CEO"},
        )
        d = brief.to_dict()
        assert d["profile_name"] == "ceo"
        assert d["context_type"] == "activation"
        assert d["sections"] == {"role": "CEO"}
        assert isinstance(d["created_at"], str)

    def test_from_dict(self) -> None:
        brief = ContextBrief(
            profile_name="ceo",
            context_type="escalation",
            sections={"issue": "Budget overrun"},
            token_estimate=30,
        )
        d = brief.to_dict()
        restored = ContextBrief.from_dict(d)
        assert restored.profile_name == "ceo"
        assert restored.context_type == "escalation"
        assert restored.token_estimate == 30

    def test_round_trip(self) -> None:
        brief = ContextBrief(
            profile_name="cto",
            context_type="task_brief",
            sections={"task": "Review PR"},
            metadata={"priority": "high"},
            token_estimate=20,
        )
        d = brief.to_dict()
        restored = ContextBrief.from_dict(d)
        assert restored.to_dict() == d

    def test_to_dict_json_serializable(self) -> None:
        brief = ContextBrief(profile_name="ceo", context_type="activation")
        d = brief.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# StatusSummary
# ---------------------------------------------------------------------------


class TestStatusSummary:
    """Tests for the StatusSummary dataclass."""

    def test_creation(self) -> None:
        summary = StatusSummary(
            profile_name="cto",
            summary_type="interaction",
            decisions=["Approved migration plan"],
            deliverables=["Migration script v1"],
            blockers=["Waiting on DBA approval"],
            metrics={"tasks_completed": 3},
        )
        assert summary.profile_name == "cto"
        assert summary.summary_type == "interaction"
        assert len(summary.decisions) == 1
        assert len(summary.deliverables) == 1
        assert len(summary.blockers) == 1

    def test_default_values(self) -> None:
        summary = StatusSummary(profile_name="ceo", summary_type="periodic")
        assert summary.decisions == []
        assert summary.deliverables == []
        assert summary.blockers == []
        assert summary.metrics == {}
        assert summary.created_at is not None

    def test_to_dict(self) -> None:
        summary = StatusSummary(
            profile_name="ceo",
            summary_type="periodic",
            decisions=["Hired new dev"],
        )
        d = summary.to_dict()
        assert d["profile_name"] == "ceo"
        assert d["summary_type"] == "periodic"
        assert d["decisions"] == ["Hired new dev"]

    def test_from_dict(self) -> None:
        summary = StatusSummary(
            profile_name="cto",
            summary_type="escalation",
            blockers=["Outage in prod"],
        )
        d = summary.to_dict()
        restored = StatusSummary.from_dict(d)
        assert restored.profile_name == "cto"
        assert restored.blockers == ["Outage in prod"]

    def test_round_trip(self) -> None:
        summary = StatusSummary(
            profile_name="ceo",
            summary_type="interaction",
            decisions=["A"],
            deliverables=["B"],
            blockers=["C"],
            metrics={"x": 1},
        )
        d = summary.to_dict()
        restored = StatusSummary.from_dict(d)
        assert restored.to_dict() == d

    def test_to_dict_json_serializable(self) -> None:
        summary = StatusSummary(profile_name="ceo", summary_type="periodic")
        d = summary.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# TierTransition
# ---------------------------------------------------------------------------


class TestTierTransition:
    """Tests for the TierTransition dataclass."""

    def test_creation(self) -> None:
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="aging",
        )
        assert tt.transition_id == "tt-test1234"
        assert tt.entry_id == "mem-test1234"
        assert tt.from_tier == MemoryTier.hot
        assert tt.to_tier == MemoryTier.warm
        assert tt.reason == "aging"
        assert tt.transitioned_at is not None

    def test_to_dict(self) -> None:
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.warm,
            to_tier=MemoryTier.cool,
            reason="gc_pass",
        )
        d = tt.to_dict()
        assert d["transition_id"] == "tt-test1234"
        assert d["from_tier"] == "warm"
        assert d["to_tier"] == "cool"
        assert d["reason"] == "gc_pass"
        assert isinstance(d["transitioned_at"], str)

    def test_from_dict(self) -> None:
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.cool,
            to_tier=MemoryTier.cold,
            reason="budget",
        )
        d = tt.to_dict()
        restored = TierTransition.from_dict(d)
        assert restored.transition_id == "tt-test1234"
        assert restored.from_tier == MemoryTier.cool
        assert restored.to_tier == MemoryTier.cold

    def test_round_trip(self) -> None:
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="aging",
        )
        d = tt.to_dict()
        restored = TierTransition.from_dict(d)
        assert restored.to_dict() == d

    def test_to_dict_json_serializable(self) -> None:
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="aging",
        )
        d = tt.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# GCReport
# ---------------------------------------------------------------------------


class TestGCReport:
    """Tests for the GCReport dataclass."""

    def test_creation_defaults(self) -> None:
        report = GCReport()
        assert report.entries_transitioned == 0
        assert report.entries_purged == 0
        assert report.bytes_freed == 0
        assert report.budget_status == {}
        assert report.recommendations == []
        assert report.transitions == []
        assert report.purged_ids == []
        assert report.dry_run is False
        assert report.ran_at is not None

    def test_creation_custom(self) -> None:
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="aging",
        )
        report = GCReport(
            entries_transitioned=1,
            entries_purged=2,
            bytes_freed=1024,
            budget_status={"ceo": "ok"},
            recommendations=["Consider increasing budget"],
            transitions=[tt],
            purged_ids=["mem-old12345"],
            dry_run=True,
        )
        assert report.entries_transitioned == 1
        assert report.entries_purged == 2
        assert report.bytes_freed == 1024
        assert report.dry_run is True
        assert len(report.transitions) == 1

    def test_to_dict(self) -> None:
        report = GCReport(
            entries_transitioned=5,
            bytes_freed=2048,
            recommendations=["cleanup needed"],
        )
        d = report.to_dict()
        assert d["entries_transitioned"] == 5
        assert d["bytes_freed"] == 2048
        assert d["recommendations"] == ["cleanup needed"]
        assert isinstance(d["ran_at"], str)

    def test_to_dict_with_nested_transitions(self) -> None:
        """Nested TierTransition objects are serialized."""
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="aging",
        )
        report = GCReport(transitions=[tt])
        d = report.to_dict()
        assert len(d["transitions"]) == 1
        assert d["transitions"][0]["transition_id"] == "tt-test1234"
        assert d["transitions"][0]["from_tier"] == "hot"

    def test_from_dict(self) -> None:
        report = GCReport(
            entries_transitioned=3,
            entries_purged=1,
            bytes_freed=512,
        )
        d = report.to_dict()
        restored = GCReport.from_dict(d)
        assert restored.entries_transitioned == 3
        assert restored.entries_purged == 1
        assert restored.bytes_freed == 512

    def test_from_dict_with_nested_transitions(self) -> None:
        """Nested transitions are deserialized."""
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="aging",
        )
        report = GCReport(transitions=[tt])
        d = report.to_dict()
        restored = GCReport.from_dict(d)
        assert len(restored.transitions) == 1
        assert isinstance(restored.transitions[0], TierTransition)
        assert restored.transitions[0].from_tier == MemoryTier.hot

    def test_round_trip(self) -> None:
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="aging",
        )
        report = GCReport(
            entries_transitioned=1,
            entries_purged=0,
            bytes_freed=100,
            budget_status={"ceo": "ok"},
            recommendations=["none"],
            transitions=[tt],
            purged_ids=[],
            dry_run=False,
        )
        d = report.to_dict()
        restored = GCReport.from_dict(d)
        assert restored.to_dict() == d

    def test_to_dict_json_serializable(self) -> None:
        tt = TierTransition(
            transition_id="tt-test1234",
            entry_id="mem-test1234",
            from_tier=MemoryTier.hot,
            to_tier=MemoryTier.warm,
            reason="aging",
        )
        report = GCReport(transitions=[tt])
        d = report.to_dict()
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
