"""Fixtures for the memory subsystem test suite."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import sqlite3

from core.memory.schema import init_memory_db
from core.memory.models import (
    MemoryEntry,
    KnowledgeEntry,
    MemoryBudget,
    MemoryScope,
    MemoryTier,
    MemoryEntryType,
    generate_memory_id,
    generate_knowledge_id,
)
from core.memory.memory_store import MemoryStore
from core.memory.knowledge_base import KnowledgeBase
from core.memory.context_manager import ContextManager
from core.memory.tiered_storage import TieredStorage


@pytest.fixture
def memory_db():
    """Create an in-memory SQLite database initialized with memory schema."""
    conn = init_memory_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def memory_db_path(temp_dir):
    """Path for a temporary memory database."""
    return os.path.join(temp_dir, "memory.db")


@pytest.fixture
def sample_memory_entry() -> MemoryEntry:
    """Create a sample MemoryEntry for testing."""
    return MemoryEntry(
        entry_id=generate_memory_id(),
        profile_name="ceo",
        scope=MemoryScope.strategic,
        tier=MemoryTier.hot,
        entry_type=MemoryEntryType.decision,
        content="Decided to prioritize Project Alpha launch",
    )


@pytest.fixture
def sample_knowledge_entry() -> KnowledgeEntry:
    """Create a sample KnowledgeEntry for testing."""
    return KnowledgeEntry(
        entry_id=generate_knowledge_id(),
        profile_name="cto",
        category="architecture",
        title="Database Migration Strategy",
        content="Use Alembic for schema migrations in production",
        tags=["database", "migrations"],
    )


@pytest.fixture
def sample_budget() -> MemoryBudget:
    """Create a sample MemoryBudget for testing."""
    return MemoryBudget(profile_name="ceo")


# ------------------------------------------------------------------
# MemoryStore fixtures
# ------------------------------------------------------------------


@pytest.fixture
def memory_store():
    """Create an in-memory MemoryStore scoped to CEO/strategic."""
    store = MemoryStore(":memory:", "ceo", MemoryScope.strategic)
    yield store
    store.close()


@pytest.fixture
def task_memory_store():
    """Create an in-memory MemoryStore scoped to worker/task."""
    store = MemoryStore(":memory:", "worker-1", MemoryScope.task)
    yield store
    store.close()


@pytest.fixture
def populated_memory_store(memory_store):
    """Create a MemoryStore with 5 hot decision entries."""
    entries = []
    for i in range(5):
        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType.decision,
            content=f"Decision {i}: Test content for entry {i}",
        )
        memory_store.store(entry)
        entries.append(entry)
    return memory_store, entries


@pytest.fixture
def mixed_memory_store(memory_store):
    """Create a MemoryStore with entries across tiers and types."""
    entries = []
    types = [
        MemoryEntryType.decision,
        MemoryEntryType.learning,
        MemoryEntryType.context,
        MemoryEntryType.artifact,
        MemoryEntryType.summary,
    ]
    for i, entry_type in enumerate(types):
        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name="ceo",
            scope=MemoryScope.strategic,
            tier=MemoryTier.hot,
            entry_type=entry_type,
            content=f"Entry {i}: Content of type {entry_type.value}",
        )
        memory_store.store(entry)
        entries.append(entry)
    return memory_store, entries


# ------------------------------------------------------------------
# KnowledgeBase fixtures
# ------------------------------------------------------------------


@pytest.fixture
def knowledge_base():
    """Create an in-memory KnowledgeBase for CEO."""
    kb = KnowledgeBase(":memory:", "ceo")
    yield kb
    kb.close()


@pytest.fixture
def populated_knowledge_base(knowledge_base):
    """Create a KnowledgeBase with several entries across categories."""
    entries = []
    data = [
        ("architecture", "Microservice Design", "Use event-driven microservices", ["arch", "design"]),
        ("architecture", "API Standards", "All APIs must use REST with OpenAPI specs", ["api", "standards"]),
        ("process", "Code Review", "All changes require two approvals", ["review", "process"]),
        ("process", "Deployment", "Deploy to staging before production", ["deploy", "process"]),
        ("domain", "User Auth", "Use OAuth2 with JWT tokens", ["auth", "security"]),
    ]
    for category, title, content, tags in data:
        entry = KnowledgeEntry(
            entry_id=generate_knowledge_id(),
            profile_name="ceo",
            category=category,
            title=title,
            content=content,
            tags=tags,
        )
        knowledge_base.add_knowledge(entry)
        entries.append(entry)
    return knowledge_base, entries


# ------------------------------------------------------------------
# ContextManager fixtures
# ------------------------------------------------------------------


@pytest.fixture
def context_manager(memory_store, knowledge_base):
    """Create a ContextManager with memory store and knowledge base."""
    return ContextManager(memory_store=memory_store, knowledge_base=knowledge_base)


@pytest.fixture
def empty_context_manager():
    """Create a ContextManager with no backing components."""
    return ContextManager()


# ------------------------------------------------------------------
# TieredStorage fixtures
# ------------------------------------------------------------------


@pytest.fixture
def tiered_storage():
    """Create a TieredStorage with default thresholds."""
    return TieredStorage()


@pytest.fixture
def fast_tiered_storage():
    """Create a TieredStorage with very short thresholds for testing."""
    return TieredStorage(warm_age_days=1, cool_age_days=5, cold_age_days=10)
