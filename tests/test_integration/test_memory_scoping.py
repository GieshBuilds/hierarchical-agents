"""Memory scoping integration tests.

Verifies that MemoryStore enforces scope isolation per-profile:
- CEO (hermes) → STRATEGIC scope only
- CTO → DOMAIN scope only
- PM → PROJECT scope only
- Worker → TASK scope only

Also tests ContextManager.build_task_brief() for scoped context assembly.
"""
from __future__ import annotations

import os

import pytest

from core.memory.context_manager import ContextManager
from core.memory.exceptions import MemoryStoreError
from core.memory.knowledge_base import KnowledgeBase
from core.memory.memory_store import MemoryStore
from core.memory.models import (
    ContextBrief,
    KnowledgeEntry,
    MemoryEntry,
    MemoryEntryType,
    MemoryScope,
    MemoryTier,
    generate_knowledge_id,
    generate_memory_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir(tmp_path):
    """Return a temp directory for database files."""
    return str(tmp_path)


@pytest.fixture
def ceo_store(tmp_dir):
    """MemoryStore for CEO (hermes) at STRATEGIC scope."""
    db_path = os.path.join(tmp_dir, "ceo_memory.db")
    store = MemoryStore(db_path, "hermes", MemoryScope.strategic)
    yield store
    store.close()


@pytest.fixture
def cto_store(tmp_dir):
    """MemoryStore for CTO at DOMAIN scope."""
    db_path = os.path.join(tmp_dir, "cto_memory.db")
    store = MemoryStore(db_path, "cto", MemoryScope.domain)
    yield store
    store.close()


@pytest.fixture
def pm_store(tmp_dir):
    """MemoryStore for PM at PROJECT scope."""
    db_path = os.path.join(tmp_dir, "pm_memory.db")
    store = MemoryStore(db_path, "pm-alpha", MemoryScope.project)
    yield store
    store.close()


@pytest.fixture
def worker_store(tmp_dir):
    """MemoryStore for Worker at TASK scope."""
    db_path = os.path.join(tmp_dir, "worker_memory.db")
    store = MemoryStore(db_path, "worker-1", MemoryScope.task)
    yield store
    store.close()


def _make_entry(
    scope: MemoryScope,
    content: str = "Test content",
    entry_type: MemoryEntryType = MemoryEntryType.decision,
    tier: MemoryTier = MemoryTier.hot,
) -> MemoryEntry:
    """Create a MemoryEntry with the given scope."""
    return MemoryEntry(
        entry_id=generate_memory_id(),
        profile_name="",  # store() will override
        scope=scope,
        tier=tier,
        entry_type=entry_type,
        content=content,
    )


# ===========================================================================
# Scope Enforcement — Each Store Accepts Only Its Scope
# ===========================================================================


class TestScopeEnforcement:
    """Each MemoryStore only accepts entries matching its profile scope."""

    def test_ceo_accepts_strategic_scope(self, ceo_store):
        """CEO store accepts STRATEGIC scope entries."""
        entry = _make_entry(MemoryScope.strategic, "Company-wide vision update")
        stored = ceo_store.store(entry)
        assert stored.scope == MemoryScope.strategic
        assert stored.profile_name == "hermes"

    def test_ceo_rejects_task_scope(self, ceo_store):
        """CEO store rejects TASK scope entries — raises MemoryStoreError."""
        entry = _make_entry(MemoryScope.task, "Implementation detail")
        with pytest.raises(MemoryStoreError, match="does not match"):
            ceo_store.store(entry)

    def test_ceo_rejects_domain_scope(self, ceo_store):
        """CEO store rejects DOMAIN scope entries."""
        entry = _make_entry(MemoryScope.domain, "Engineering architecture")
        with pytest.raises(MemoryStoreError, match="does not match"):
            ceo_store.store(entry)

    def test_ceo_rejects_project_scope(self, ceo_store):
        """CEO store rejects PROJECT scope entries."""
        entry = _make_entry(MemoryScope.project, "Sprint plan")
        with pytest.raises(MemoryStoreError, match="does not match"):
            ceo_store.store(entry)

    def test_cto_accepts_domain_scope(self, cto_store):
        """CTO store accepts DOMAIN scope entries."""
        entry = _make_entry(MemoryScope.domain, "API architecture decision")
        stored = cto_store.store(entry)
        assert stored.scope == MemoryScope.domain
        assert stored.profile_name == "cto"

    def test_cto_rejects_strategic_scope(self, cto_store):
        """CTO store rejects STRATEGIC scope entries."""
        entry = _make_entry(MemoryScope.strategic, "Corporate strategy")
        with pytest.raises(MemoryStoreError, match="does not match"):
            cto_store.store(entry)

    def test_cto_rejects_task_scope(self, cto_store):
        """CTO store rejects TASK scope entries."""
        entry = _make_entry(MemoryScope.task, "Write unit test")
        with pytest.raises(MemoryStoreError, match="does not match"):
            cto_store.store(entry)

    def test_pm_accepts_project_scope(self, pm_store):
        """PM store accepts PROJECT scope entries."""
        entry = _make_entry(MemoryScope.project, "Sprint backlog update")
        stored = pm_store.store(entry)
        assert stored.scope == MemoryScope.project
        assert stored.profile_name == "pm-alpha"

    def test_pm_rejects_strategic_scope(self, pm_store):
        """PM store rejects STRATEGIC scope entries."""
        entry = _make_entry(MemoryScope.strategic, "Company vision")
        with pytest.raises(MemoryStoreError, match="does not match"):
            pm_store.store(entry)

    def test_pm_rejects_task_scope(self, pm_store):
        """PM store rejects TASK scope entries."""
        entry = _make_entry(MemoryScope.task, "Code implementation")
        with pytest.raises(MemoryStoreError, match="does not match"):
            pm_store.store(entry)

    def test_worker_accepts_task_scope(self, worker_store):
        """Worker store accepts TASK scope entries."""
        entry = _make_entry(MemoryScope.task, "Implemented login form")
        stored = worker_store.store(entry)
        assert stored.scope == MemoryScope.task
        assert stored.profile_name == "worker-1"

    def test_worker_rejects_strategic_scope(self, worker_store):
        """Worker store rejects STRATEGIC scope entries."""
        entry = _make_entry(MemoryScope.strategic, "Company direction")
        with pytest.raises(MemoryStoreError, match="does not match"):
            worker_store.store(entry)

    def test_worker_rejects_domain_scope(self, worker_store):
        """Worker store rejects DOMAIN scope entries."""
        entry = _make_entry(MemoryScope.domain, "Architecture decision")
        with pytest.raises(MemoryStoreError, match="does not match"):
            worker_store.store(entry)

    def test_worker_rejects_project_scope(self, worker_store):
        """Worker store rejects PROJECT scope entries."""
        entry = _make_entry(MemoryScope.project, "Sprint plan")
        with pytest.raises(MemoryStoreError, match="does not match"):
            worker_store.store(entry)


# ===========================================================================
# Scope Isolation — Stores Are Independent Per-Profile
# ===========================================================================


class TestScopeIsolation:
    """Verify that each MemoryStore is independent — workers can't see CEO context."""

    def test_ceo_entries_not_visible_to_worker(self, ceo_store, worker_store):
        """Worker cannot see entries stored by CEO."""
        # Store entry in CEO store
        ceo_entry = _make_entry(
            MemoryScope.strategic,
            "Confidential strategic direction",
        )
        ceo_store.store(ceo_entry)

        # Worker store is completely separate
        worker_entries = worker_store.list_entries()
        assert len(worker_entries) == 0

    def test_worker_entries_not_visible_to_ceo(self, ceo_store, worker_store):
        """CEO cannot see entries stored by worker."""
        worker_entry = _make_entry(
            MemoryScope.task,
            "Low-level implementation detail",
        )
        worker_store.store(worker_entry)

        ceo_entries = ceo_store.list_entries()
        assert len(ceo_entries) == 0

    def test_cto_entries_not_visible_to_pm(self, cto_store, pm_store):
        """PM cannot see entries stored by CTO."""
        cto_entry = _make_entry(
            MemoryScope.domain,
            "Engineering architecture decision",
        )
        cto_store.store(cto_entry)

        pm_entries = pm_store.list_entries()
        assert len(pm_entries) == 0

    def test_each_store_only_sees_own_entries(
        self, ceo_store, cto_store, pm_store, worker_store
    ):
        """Each store sees only its own entries, not others'."""
        ceo_store.store(
            _make_entry(MemoryScope.strategic, "CEO decision 1")
        )
        ceo_store.store(
            _make_entry(MemoryScope.strategic, "CEO decision 2")
        )
        cto_store.store(
            _make_entry(MemoryScope.domain, "CTO decision 1")
        )
        pm_store.store(
            _make_entry(MemoryScope.project, "PM plan 1")
        )
        worker_store.store(
            _make_entry(MemoryScope.task, "Worker note 1")
        )

        assert len(ceo_store.list_entries()) == 2
        assert len(cto_store.list_entries()) == 1
        assert len(pm_store.list_entries()) == 1
        assert len(worker_store.list_entries()) == 1

    def test_search_scoped_to_own_profile(self, ceo_store, cto_store):
        """Search only returns entries from the queried store's profile."""
        ceo_store.store(
            _make_entry(MemoryScope.strategic, "Launch plan for Q4")
        )
        cto_store.store(
            _make_entry(MemoryScope.domain, "Launch technical requirements")
        )

        # Both contain "Launch" but search is scoped
        ceo_results = ceo_store.search("Launch")
        cto_results = cto_store.search("Launch")

        assert len(ceo_results) == 1
        assert ceo_results[0].profile_name == "hermes"

        assert len(cto_results) == 1
        assert cto_results[0].profile_name == "cto"


# ===========================================================================
# Entry Type Varieties Within Scope
# ===========================================================================


class TestEntryTypes:
    """Test storing different entry types at each scope level."""

    def test_ceo_stores_multiple_entry_types(self, ceo_store):
        """CEO can store decisions, learnings, context entries — all at strategic scope."""
        decision = _make_entry(
            MemoryScope.strategic,
            "Decided to pivot product direction",
            MemoryEntryType.decision,
        )
        learning = _make_entry(
            MemoryScope.strategic,
            "Market analysis showed new opportunity",
            MemoryEntryType.learning,
        )
        context = _make_entry(
            MemoryScope.strategic,
            "Board meeting context: Q3 review",
            MemoryEntryType.context,
        )

        ceo_store.store(decision)
        ceo_store.store(learning)
        ceo_store.store(context)

        entries = ceo_store.list_entries()
        assert len(entries) == 3
        types_stored = {e.entry_type for e in entries}
        assert types_stored == {
            MemoryEntryType.decision,
            MemoryEntryType.learning,
            MemoryEntryType.context,
        }

    def test_worker_stores_observations_at_task_scope(self, worker_store):
        """Worker can store different entry types at task scope."""
        for i in range(5):
            entry = _make_entry(
                MemoryScope.task,
                f"Worker observation {i}",
                MemoryEntryType.context,
            )
            worker_store.store(entry)

        entries = worker_store.list_entries()
        assert len(entries) == 5


# ===========================================================================
# Memory Tiers Within Scope
# ===========================================================================


class TestMemoryTiers:
    """Test that entries can be stored at different tiers within scope."""

    def test_store_and_retrieve_hot_entry(self, ceo_store):
        """Hot tier entries are stored and retrievable."""
        entry = _make_entry(
            MemoryScope.strategic,
            "Urgent strategic decision",
            tier=MemoryTier.hot,
        )
        stored = ceo_store.store(entry)
        retrieved = ceo_store.get(stored.entry_id)
        assert retrieved.tier == MemoryTier.hot

    def test_store_different_tiers(self, pm_store):
        """PM can store entries at different tiers."""
        hot = _make_entry(
            MemoryScope.project, "Active sprint plan", tier=MemoryTier.hot,
        )
        warm = _make_entry(
            MemoryScope.project, "Last sprint retro", tier=MemoryTier.warm,
        )
        pm_store.store(hot)
        pm_store.store(warm)

        hot_entries = pm_store.list_entries(tier=MemoryTier.hot)
        warm_entries = pm_store.list_entries(tier=MemoryTier.warm)
        assert len(hot_entries) == 1
        assert len(warm_entries) == 1

    def test_list_entries_filter_by_tier(self, cto_store):
        """list_entries can filter by tier."""
        for tier in [MemoryTier.hot, MemoryTier.warm, MemoryTier.cool]:
            cto_store.store(
                _make_entry(MemoryScope.domain, f"Entry at {tier.value}", tier=tier)
            )

        hot_only = cto_store.list_entries(tier=MemoryTier.hot)
        assert len(hot_only) == 1
        assert hot_only[0].tier == MemoryTier.hot


# ===========================================================================
# ContextManager — Scoped Task Brief
# ===========================================================================


class TestContextManagerTaskBrief:
    """Test ContextManager.build_task_brief() produces scoped context."""

    def test_build_task_brief_basic(self, pm_store):
        """build_task_brief returns a ContextBrief with task section."""
        ctx_mgr = ContextManager(memory_store=pm_store)
        brief = ctx_mgr.build_task_brief(
            pm_profile="pm-alpha",
            task_description="Implement login page",
        )
        assert isinstance(brief, ContextBrief)
        assert brief.context_type == "task_brief"
        assert "task" in brief.sections
        assert brief.sections["task"] == "Implement login page"

    def test_build_task_brief_with_context(self, pm_store):
        """build_task_brief includes relevant_context in project_context section."""
        ctx_mgr = ContextManager(memory_store=pm_store)
        brief = ctx_mgr.build_task_brief(
            pm_profile="pm-alpha",
            task_description="Build API endpoint",
            relevant_context=["Use REST conventions", "Rate limit at 100 req/s"],
        )
        assert "project_context" in brief.sections
        assert "REST conventions" in brief.sections["project_context"]
        assert "Rate limit" in brief.sections["project_context"]

    def test_build_task_brief_no_context(self, pm_store):
        """build_task_brief without relevant_context omits project_context."""
        ctx_mgr = ContextManager(memory_store=pm_store)
        brief = ctx_mgr.build_task_brief(
            pm_profile="pm-alpha",
            task_description="Simple task",
        )
        # project_context should be absent or empty (filtered out)
        if "project_context" in brief.sections:
            assert brief.sections["project_context"] == ""

    def test_build_task_brief_with_knowledge_base(self, tmp_dir, pm_store):
        """build_task_brief cherry-picks from knowledge base when available."""
        kb_path = os.path.join(tmp_dir, "pm_kb.db")
        kb = KnowledgeBase(kb_path, "pm-alpha")
        kb.add_knowledge(
            KnowledgeEntry(
                entry_id=generate_knowledge_id(),
                profile_name="pm-alpha",
                category="constraints",
                title="API Standards",
                content="All APIs must use REST with OpenAPI specs",
                tags=["api", "standards"],
            )
        )

        ctx_mgr = ContextManager(
            memory_store=pm_store,
            knowledge_base=kb,
        )
        brief = ctx_mgr.build_task_brief(
            pm_profile="pm-alpha",
            task_description="Build REST API",
        )
        assert brief.context_type == "task_brief"
        assert brief.token_estimate >= 0

        kb.close()

    def test_build_task_brief_metadata_contains_task(self, pm_store):
        """build_task_brief includes task_description in metadata."""
        ctx_mgr = ContextManager(memory_store=pm_store)
        brief = ctx_mgr.build_task_brief(
            pm_profile="pm-alpha",
            task_description="Deploy to staging",
        )
        assert brief.metadata.get("task_description") == "Deploy to staging"

    def test_inject_context_formats_sections(self, pm_store):
        """inject_context() formats ContextBrief into readable text."""
        ctx_mgr = ContextManager(memory_store=pm_store)
        brief = ctx_mgr.build_task_brief(
            pm_profile="pm-alpha",
            task_description="Write tests",
            relevant_context=["Use pytest", "100% coverage required"],
        )
        text = ctx_mgr.inject_context(brief)
        assert isinstance(text, str)
        assert "## task" in text
        assert "Write tests" in text
        assert "## project_context" in text


# ===========================================================================
# ContextManager — Activation Context
# ===========================================================================


class TestContextManagerActivation:
    """Test ContextManager.build_activation_context() with memory stores."""

    def test_activation_context_includes_hot_memory(self, ceo_store):
        """Activation context includes active (hot) memory entries."""
        ceo_store.store(
            _make_entry(
                MemoryScope.strategic,
                "Strategic initiative: expand to APAC",
                tier=MemoryTier.hot,
            )
        )
        ctx_mgr = ContextManager(memory_store=ceo_store)
        brief = ctx_mgr.build_activation_context("hermes")

        assert brief.context_type == "activation"
        assert "active_memory" in brief.sections
        assert "APAC" in brief.sections["active_memory"]

    def test_activation_context_without_memory_store(self):
        """Activation context works with no memory store — memory section omitted."""
        ctx_mgr = ContextManager()
        brief = ctx_mgr.build_activation_context("hermes")
        assert brief.context_type == "activation"
        # No active_memory section when no store
        assert "active_memory" not in brief.sections or brief.sections.get("active_memory") == ""

    def test_activation_context_uses_profile_identity(self, ceo_store):
        """Activation context includes identity section."""
        ctx_mgr = ContextManager(memory_store=ceo_store)
        brief = ctx_mgr.build_activation_context("hermes")
        assert "identity" in brief.sections
        assert "hermes" in brief.sections["identity"]


# ===========================================================================
# Cross-Scope Interactions
# ===========================================================================


class TestCrossScopeInteractions:
    """Test that memory scoping interacts correctly across the hierarchy."""

    def test_ceo_and_cto_stores_independent(self, ceo_store, cto_store):
        """CEO and CTO stores don't leak entries to each other."""
        ceo_store.store(
            _make_entry(MemoryScope.strategic, "CEO: prioritize revenue")
        )
        cto_store.store(
            _make_entry(MemoryScope.domain, "CTO: adopt microservices")
        )

        # Each has exactly 1 entry
        assert len(ceo_store.list_entries()) == 1
        assert len(cto_store.list_entries()) == 1

        # Content doesn't leak
        ceo_entries = ceo_store.list_entries()
        assert ceo_entries[0].content == "CEO: prioritize revenue"
        cto_entries = cto_store.list_entries()
        assert cto_entries[0].content == "CTO: adopt microservices"

    def test_all_four_scopes_coexist(
        self, ceo_store, cto_store, pm_store, worker_store
    ):
        """All four scope levels can store entries simultaneously."""
        ceo_store.store(
            _make_entry(MemoryScope.strategic, "Vision 2026")
        )
        cto_store.store(
            _make_entry(MemoryScope.domain, "Tech stack migration")
        )
        pm_store.store(
            _make_entry(MemoryScope.project, "Sprint 42 plan")
        )
        worker_store.store(
            _make_entry(MemoryScope.task, "Implement auth module")
        )

        # All stores have exactly 1 entry
        for store, expected_scope in [
            (ceo_store, MemoryScope.strategic),
            (cto_store, MemoryScope.domain),
            (pm_store, MemoryScope.project),
            (worker_store, MemoryScope.task),
        ]:
            entries = store.list_entries()
            assert len(entries) == 1
            assert entries[0].scope == expected_scope

    def test_context_manager_uses_correct_store_scope(self, pm_store, worker_store):
        """ContextManager with PM store only sees PM scope entries."""
        pm_store.store(
            _make_entry(
                MemoryScope.project,
                "Sprint goal: ship v2.0",
                tier=MemoryTier.hot,
            )
        )
        worker_store.store(
            _make_entry(
                MemoryScope.task,
                "Worker implementation detail",
                tier=MemoryTier.hot,
            )
        )

        # PM context manager only sees PM entries
        pm_ctx = ContextManager(memory_store=pm_store)
        pm_brief = pm_ctx.build_activation_context("pm-alpha")
        if "active_memory" in pm_brief.sections:
            assert "Sprint goal" in pm_brief.sections["active_memory"]
            assert "Worker implementation" not in pm_brief.sections["active_memory"]

        # Worker context manager only sees worker entries
        worker_ctx = ContextManager(memory_store=worker_store)
        worker_brief = worker_ctx.build_activation_context("worker-1")
        if "active_memory" in worker_brief.sections:
            assert "Worker implementation" in worker_brief.sections["active_memory"]
            assert "Sprint goal" not in worker_brief.sections["active_memory"]


# ===========================================================================
# MemoryEntry Construction Patterns
# ===========================================================================


class TestMemoryEntryConstruction:
    """Test MemoryEntry construction with various patterns."""

    def test_entry_with_content_scope_and_type(self):
        """MemoryEntry can be constructed with content, scope, and entry_type."""
        entry = MemoryEntry(
            entry_id="",
            profile_name="",
            content="Strategic observation about market",
            scope=MemoryScope.strategic,
            entry_type=MemoryEntryType.decision,
            tier=MemoryTier.hot,
        )
        assert entry.content == "Strategic observation about market"
        assert entry.scope == MemoryScope.strategic
        assert entry.entry_type == MemoryEntryType.decision
        assert entry.byte_size > 0

    def test_entry_byte_size_auto_calculated(self):
        """byte_size is automatically calculated from content."""
        content = "Hello, world! 🌍"
        entry = MemoryEntry(
            entry_id="test-id",
            profile_name="hermes",
            content=content,
            scope=MemoryScope.strategic,
            entry_type=MemoryEntryType.context,
            tier=MemoryTier.hot,
        )
        assert entry.byte_size == len(content.encode("utf-8"))

    def test_store_auto_populates_profile_and_id(self, ceo_store):
        """store() auto-sets profile_name and entry_id on entry."""
        entry = MemoryEntry(
            entry_id="",
            profile_name="",
            content="Auto-populated entry",
            scope=MemoryScope.strategic,
            entry_type=MemoryEntryType.decision,
            tier=MemoryTier.hot,
        )
        stored = ceo_store.store(entry)
        assert stored.profile_name == "hermes"
        assert stored.entry_id.startswith("mem-")
