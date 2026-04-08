"""Tests for knowledge-sharing tools in hierarchy_tools.py.

Covers: share_knowledge, search_knowledge_tool, read_ancestor_memory,
get_chain_context, and KnowledgeBase.search_all_profiles.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.memory.knowledge_base import KnowledgeBase
from core.memory.memory_store import MemoryStore
from core.memory.models import (
    KnowledgeEntry,
    MemoryEntry,
    MemoryEntryType,
    MemoryScope,
    MemoryTier,
    generate_knowledge_id,
    generate_memory_id,
)
from core.registry.models import Role
from core.registry.profile_registry import ProfileRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_dir(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "workers").mkdir()
    return tmp_path


@pytest.fixture
def registry(tmp_db_dir):
    reg = ProfileRegistry(str(tmp_db_dir / "registry.db"))
    reg.create_profile(name="test-cto", role="department_head", parent="hermes", department="engineering")
    reg.create_profile(name="test-pm", role="project_manager", parent="test-cto", department="engineering")
    reg.create_profile(name="test-pm-b", role="project_manager", parent="test-cto", department="engineering")
    reg.create_profile(name="test-spec", role="specialist", parent="test-pm", department="engineering")
    return reg


@pytest.fixture
def knowledge_db(tmp_db_dir):
    """Shared knowledge.db for all profiles."""
    return str(tmp_db_dir / "memory" / "knowledge.db")


@pytest.fixture
def injected(tmp_db_dir, registry, knowledge_db):
    """Inject singletons into hierarchy_tools for isolated testing."""
    import tools.hierarchy_tools as ht
    from core.ipc.message_bus import MessageBus
    from core.workers.subagent_registry import SubagentRegistry

    class _Adapter:
        def __init__(self, r): self._r = r
        def get(self, name): return self._r.get_profile(name)

    bus = MessageBus(str(tmp_db_dir / "ipc.db"), profile_registry=_Adapter(registry))
    sub_reg = SubagentRegistry(str(tmp_db_dir / "workers"), profile_registry=registry)

    old = {
        "registry": ht._profile_registry,
        "bus": ht._message_bus,
        "subreg": ht._subagent_registry,
        "mem": ht._memory_stores,
        "kb": ht._knowledge_bases,
        "orch": ht._chain_orchestrator,
        "db_dir": ht._DB_BASE_DIR,
        "profiles_dir": ht._PROFILES_DIR,
        "env": os.environ.get("HERMES_PROFILE"),
    }

    ht._profile_registry = registry
    ht._message_bus = bus
    ht._subagent_registry = sub_reg
    ht._memory_stores = {}
    ht._knowledge_bases = {}
    ht._chain_orchestrator = None
    ht._DB_BASE_DIR = tmp_db_dir
    ht._PROFILES_DIR = tmp_db_dir / "profiles"
    os.environ["HERMES_PROFILE"] = "test-pm"

    yield {"registry": registry, "bus": bus, "db_dir": tmp_db_dir, "knowledge_db": knowledge_db}

    ht._profile_registry = old["registry"]
    ht._message_bus = old["bus"]
    ht._subagent_registry = old["subreg"]
    ht._memory_stores = old["mem"]
    ht._knowledge_bases = old["kb"]
    ht._chain_orchestrator = old["orch"]
    ht._DB_BASE_DIR = old["db_dir"]
    ht._PROFILES_DIR = old["profiles_dir"]
    if old["env"] is not None:
        os.environ["HERMES_PROFILE"] = old["env"]
    else:
        os.environ.pop("HERMES_PROFILE", None)


@pytest.fixture
def hermes_memory(tmp_db_dir):
    """MemoryStore for hermes with a decision entry."""
    store = MemoryStore(
        db_path=str(tmp_db_dir / "memory" / "hermes.db"),
        profile_name="hermes",
        profile_scope=MemoryScope.strategic,
    )
    entry = MemoryEntry(
        entry_id=generate_memory_id(),
        profile_name="hermes",
        scope=MemoryScope.strategic,
        tier=MemoryTier.hot,
        entry_type=MemoryEntryType.decision,
        content="Use SQLite for all persistence",
    )
    store.store(entry)
    return store


@pytest.fixture
def cto_memory(tmp_db_dir):
    """MemoryStore for test-cto with a domain learning."""
    store = MemoryStore(
        db_path=str(tmp_db_dir / "memory" / "test-cto.db"),
        profile_name="test-cto",
        profile_scope=MemoryScope.domain,
    )
    entry = MemoryEntry(
        entry_id=generate_memory_id(),
        profile_name="test-cto",
        scope=MemoryScope.domain,
        tier=MemoryTier.hot,
        entry_type=MemoryEntryType.learning,
        content="FastAPI is the standard for new services",
    )
    store.store(entry)
    return store


# ===================================================================
# KnowledgeBase.search_all_profiles
# ===================================================================


class TestSearchAllProfiles:
    def test_cross_profile_visibility(self, knowledge_db):
        kb_a = KnowledgeBase(knowledge_db, "profile-a")
        kb_b = KnowledgeBase(knowledge_db, "profile-b")

        kb_a.add_knowledge(KnowledgeEntry(
            entry_id=generate_knowledge_id(), profile_name="profile-a",
            category="arch", title="Use REST", content="REST over GraphQL",
            source_profile="profile-a",
        ))
        kb_b.add_knowledge(KnowledgeEntry(
            entry_id=generate_knowledge_id(), profile_name="profile-b",
            category="arch", title="Use PostgreSQL", content="PostgreSQL for prod",
            source_profile="profile-b",
        ))

        results = kb_a.search_all_profiles("", limit=50)
        assert len(results) == 2
        titles = {e.title for e in results}
        assert "Use REST" in titles
        assert "Use PostgreSQL" in titles

    def test_source_profile_filter(self, knowledge_db):
        kb = KnowledgeBase(knowledge_db, "any")
        kb.add_knowledge(KnowledgeEntry(
            entry_id=generate_knowledge_id(), profile_name="any",
            category="test", title="A", content="from A",
            source_profile="profile-a",
        ))
        kb.add_knowledge(KnowledgeEntry(
            entry_id=generate_knowledge_id(), profile_name="any",
            category="test", title="B", content="from B",
            source_profile="profile-b",
        ))

        results = kb.search_all_profiles("", source_profile="profile-a")
        assert len(results) == 1
        assert results[0].title == "A"

    def test_category_and_query_filter(self, knowledge_db):
        kb = KnowledgeBase(knowledge_db, "x")
        kb.add_knowledge(KnowledgeEntry(
            entry_id=generate_knowledge_id(), profile_name="x",
            category="decision", title="Auth method", content="Use JWT tokens",
            source_profile="x",
        ))
        kb.add_knowledge(KnowledgeEntry(
            entry_id=generate_knowledge_id(), profile_name="x",
            category="learning", title="JWT perf", content="JWT is fast",
            source_profile="x",
        ))

        results = kb.search_all_profiles("JWT", category="decision")
        assert len(results) == 1
        assert results[0].title == "Auth method"

    def test_empty_db_returns_empty(self, knowledge_db):
        kb = KnowledgeBase(knowledge_db, "empty")
        results = kb.search_all_profiles("anything")
        assert results == []


# ===================================================================
# share_knowledge
# ===================================================================


class TestShareKnowledge:
    def test_happy_path(self, injected):
        from tools.hierarchy_tools import share_knowledge
        result = json.loads(share_knowledge({
            "title": "API Design Decision",
            "content": "Use REST with versioned endpoints",
            "category": "decision",
            "tags": ["api", "rest"],
        }))
        assert result["status"] == "shared"
        assert result["category"] == "decision"
        assert "entry_id" in result

    def test_missing_title(self, injected):
        from tools.hierarchy_tools import share_knowledge
        result = json.loads(share_knowledge({"content": "x", "category": "y"}))
        assert "error" in result

    def test_missing_content(self, injected):
        from tools.hierarchy_tools import share_knowledge
        result = json.loads(share_knowledge({"title": "x", "category": "y"}))
        assert "error" in result

    def test_missing_category(self, injected):
        from tools.hierarchy_tools import share_knowledge
        result = json.loads(share_knowledge({"title": "x", "content": "y"}))
        assert "error" in result

    def test_source_attribution(self, injected):
        from tools.hierarchy_tools import share_knowledge, _get_knowledge_base
        share_knowledge({
            "title": "Test Attribution",
            "content": "content",
            "category": "test",
        })
        kb = _get_knowledge_base("test-pm")
        results = kb.search_knowledge("Test Attribution")
        assert len(results) == 1
        assert results[0].source_profile == "test-pm"


# ===================================================================
# search_knowledge
# ===================================================================


class TestSearchKnowledgeTool:
    def test_cross_profile_search(self, injected):
        from tools.hierarchy_tools import share_knowledge, search_knowledge_tool

        # Share as test-pm
        share_knowledge({"title": "PM Finding", "content": "important thing", "category": "learning"})

        # Switch to specialist and search
        os.environ["HERMES_PROFILE"] = "test-spec"
        result = json.loads(search_knowledge_tool({"query": "important"}))
        assert result["result_count"] >= 1
        assert any(e["title"] == "PM Finding" for e in result["entries"])

    def test_category_filter(self, injected):
        from tools.hierarchy_tools import share_knowledge, search_knowledge_tool
        share_knowledge({"title": "D1", "content": "decision one", "category": "decision"})
        share_knowledge({"title": "L1", "content": "learning one", "category": "learning"})

        result = json.loads(search_knowledge_tool({"query": "one", "category": "decision"}))
        assert result["result_count"] == 1
        assert result["entries"][0]["title"] == "D1"

    def test_empty_query_requires_filter(self, injected):
        from tools.hierarchy_tools import search_knowledge_tool
        result = json.loads(search_knowledge_tool({"query": ""}))
        assert "error" in result


# ===================================================================
# read_ancestor_memory
# ===================================================================


class TestReadAncestorMemory:
    def test_read_ceo_memory(self, injected, hermes_memory):
        from tools.hierarchy_tools import read_ancestor_memory
        result = json.loads(read_ancestor_memory({"ancestor": "hermes"}))
        assert result["ancestor"] == "hermes"
        assert result["result_count"] >= 1
        assert any("SQLite" in e["content"] for e in result["entries"])

    def test_read_cto_memory(self, injected, cto_memory):
        from tools.hierarchy_tools import read_ancestor_memory
        result = json.loads(read_ancestor_memory({"ancestor": "test-cto"}))
        assert result["result_count"] >= 1
        assert any("FastAPI" in e["content"] for e in result["entries"])

    def test_chain_of_command_enforced(self, injected):
        from tools.hierarchy_tools import read_ancestor_memory
        # test-pm trying to read test-pm-b (sibling, not ancestor)
        result = json.loads(read_ancestor_memory({"ancestor": "test-pm-b"}))
        assert "error" in result
        assert "chain of command" in result["error"]

    def test_specialist_reads_pm(self, injected, hermes_memory):
        from tools.hierarchy_tools import read_ancestor_memory
        os.environ["HERMES_PROFILE"] = "test-spec"
        # Specialist reads CEO memory (skipping levels)
        result = json.loads(read_ancestor_memory({"ancestor": "hermes"}))
        assert result["result_count"] >= 1

    def test_no_memory_db_graceful(self, injected):
        from tools.hierarchy_tools import read_ancestor_memory
        # test-cto has no memory DB in this test (no cto_memory fixture)
        result = json.loads(read_ancestor_memory({"ancestor": "test-cto"}))
        assert result["entries"] == []
        assert "note" in result

    def test_query_filter(self, injected, hermes_memory):
        from tools.hierarchy_tools import read_ancestor_memory
        result = json.loads(read_ancestor_memory({"ancestor": "hermes", "query": "SQLite"}))
        assert result["result_count"] >= 1

        result2 = json.loads(read_ancestor_memory({"ancestor": "hermes", "query": "nonexistent_xyz"}))
        assert result2["result_count"] == 0

    def test_missing_ancestor_param(self, injected):
        from tools.hierarchy_tools import read_ancestor_memory
        result = json.loads(read_ancestor_memory({}))
        assert "error" in result


# ===================================================================
# get_chain_context
# ===================================================================


class TestGetChainContext:
    def test_full_chain(self, injected, hermes_memory, cto_memory):
        from tools.hierarchy_tools import get_chain_context, share_knowledge

        share_knowledge({"title": "Shared insight", "content": "useful knowledge", "category": "learning"})

        result = json.loads(get_chain_context({}))
        assert "chain_of_command" in result
        assert "ancestor_memory" in result
        assert "shared_knowledge" in result
        # Should have memory from hermes and/or cto
        assert len(result["ancestor_memory"]) >= 1

    def test_topic_filter(self, injected, hermes_memory):
        from tools.hierarchy_tools import get_chain_context
        result = json.loads(get_chain_context({"topic": "SQLite"}))
        # hermes has a "Use SQLite" entry — should be in ancestor_memory
        hermes_entries = result.get("ancestor_memory", {}).get("hermes", [])
        assert any("SQLite" in e["content"] for e in hermes_entries)

    def test_memory_only(self, injected, hermes_memory):
        from tools.hierarchy_tools import get_chain_context
        result = json.loads(get_chain_context({"include_knowledge": False}))
        assert "ancestor_memory" in result
        assert "shared_knowledge" not in result

    def test_knowledge_only(self, injected):
        from tools.hierarchy_tools import get_chain_context, share_knowledge
        share_knowledge({"title": "KB entry", "content": "kb content", "category": "test"})
        result = json.loads(get_chain_context({"include_memory": False}))
        assert "ancestor_memory" not in result
        assert "shared_knowledge" in result

    def test_specialist_sees_full_chain(self, injected, hermes_memory, cto_memory):
        from tools.hierarchy_tools import get_chain_context
        os.environ["HERMES_PROFILE"] = "test-spec"
        result = json.loads(get_chain_context({}))
        chain = result["chain_of_command"]
        assert "test-spec" in chain
        assert "test-pm" in chain
        assert "test-cto" in chain
        assert "hermes" in chain


# ===================================================================
# ContextManager hierarchy-aware activation
# ===================================================================


class TestContextManagerHierarchy:
    def test_ancestor_context_section(self, tmp_path):
        from core.memory.context_manager import ContextManager

        # Set up a mini hierarchy
        reg = ProfileRegistry(str(tmp_path / "reg.db"))
        reg.create_profile(name="cto", role="department_head", parent="hermes")
        reg.create_profile(name="pm", role="project_manager", parent="cto")

        # Wrap registry so ContextManager can use .get() for identity section
        class _RegAdapter:
            def __init__(self, r):
                self._r = r
            def get(self, name):
                return self._r.get_profile(name)
            def get_chain_of_command(self, name):
                return self._r.get_chain_of_command(name)

        # CEO memory
        ceo_store = MemoryStore(str(tmp_path / "hermes.db"), "hermes", MemoryScope.strategic)
        ceo_store.store(MemoryEntry(
            entry_id=generate_memory_id(), profile_name="hermes",
            scope=MemoryScope.strategic, tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content="Prioritize reliability over speed",
        ))

        stores = {"hermes": ceo_store}

        cm = ContextManager(
            profile_registry=_RegAdapter(reg),
            memory_store_factory=lambda name: stores.get(name),
        )

        brief = cm.build_activation_context("pm")
        assert "ancestor_context" in brief.sections
        assert "reliability" in brief.sections["ancestor_context"].lower()
        reg.close()

    def test_shared_knowledge_section(self, tmp_path):
        from core.memory.context_manager import ContextManager

        kb = KnowledgeBase(str(tmp_path / "kb.db"), "someone")
        kb.add_knowledge(KnowledgeEntry(
            entry_id=generate_knowledge_id(), profile_name="someone",
            category="arch", title="Use microservices", content="Microservices for scaling",
            source_profile="cto-agent",
        ))

        cm = ContextManager(knowledge_base=kb)
        brief = cm.build_activation_context("any-profile")
        assert "shared_knowledge" in brief.sections
        assert "microservices" in brief.sections["shared_knowledge"].lower()

    def test_graceful_without_factory(self):
        from core.memory.context_manager import ContextManager

        cm = ContextManager()
        brief = cm.build_activation_context("test")
        # Should not have ancestor_context since no factory/registry
        assert "ancestor_context" not in brief.sections
