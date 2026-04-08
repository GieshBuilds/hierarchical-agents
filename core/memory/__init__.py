"""Shared memory and state management for hierarchical agents.

Re-exports all public symbols from memory subsystem modules:
models, schema, exceptions, memory_store, knowledge_base,
context_manager, tiered_storage, garbage_collector, and interface.
"""

from core.memory.context_manager import ContextManager
from core.memory.exceptions import (
    ContextInjectionError,
    GarbageCollectionError,
    InvalidMemoryScope,
    InvalidTierTransition,
    KnowledgeEntryNotFound,
    MemoryBudgetExceeded,
    MemoryEntryNotFound,
    MemoryStoreError,
    ScopedMemoryError,
)
from core.memory.garbage_collector import GarbageCollector
from core.memory.interface import (
    ContextProvider,
    KnowledgeProvider,
    MemoryLifecycleManager,
    MemoryProvider,
)
from core.memory.knowledge_base import KnowledgeBase
from core.memory.memory_store import MemoryStore
from core.memory.models import (
    COLD_AGE_DAYS,
    COOL_AGE_DAYS,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_ENTRIES,
    ROLE_SCOPE_MAP,
    VALID_TIER_TRANSITIONS,
    WARM_AGE_DAYS,
    ContextBrief,
    GCReport,
    KnowledgeEntry,
    MemoryBudget,
    MemoryEntry,
    MemoryEntryType,
    MemoryScope,
    MemoryTier,
    StatusSummary,
    TierTransition,
    estimate_tokens,
    generate_knowledge_id,
    generate_memory_id,
    generate_transition_id,
    is_valid_tier_transition,
    scope_for_role,
)
from core.memory.schema import SCHEMA_VERSION, get_schema_version, init_memory_db
from core.memory.tiered_storage import TieredStorage

__all__ = [
    # Core classes
    "MemoryStore",
    "KnowledgeBase",
    "ContextManager",
    "TieredStorage",
    "GarbageCollector",
    # Schema
    "init_memory_db",
    "get_schema_version",
    "SCHEMA_VERSION",
    # Models — enums
    "MemoryScope",
    "MemoryTier",
    "MemoryEntryType",
    # Models — constants
    "WARM_AGE_DAYS",
    "COOL_AGE_DAYS",
    "COLD_AGE_DAYS",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_BYTES",
    "VALID_TIER_TRANSITIONS",
    "ROLE_SCOPE_MAP",
    # Models — ID generators
    "generate_memory_id",
    "generate_knowledge_id",
    "generate_transition_id",
    # Models — helper functions
    "scope_for_role",
    "is_valid_tier_transition",
    "estimate_tokens",
    # Models — dataclasses
    "MemoryEntry",
    "KnowledgeEntry",
    "MemoryBudget",
    "ContextBrief",
    "StatusSummary",
    "TierTransition",
    "GCReport",
    # Interfaces (protocols)
    "MemoryProvider",
    "KnowledgeProvider",
    "ContextProvider",
    "MemoryLifecycleManager",
    # Exceptions
    "ScopedMemoryError",
    "MemoryEntryNotFound",
    "KnowledgeEntryNotFound",
    "MemoryBudgetExceeded",
    "InvalidTierTransition",
    "InvalidMemoryScope",
    "ContextInjectionError",
    "GarbageCollectionError",
    "MemoryStoreError",
]
