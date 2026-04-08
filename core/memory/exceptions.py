"""Exception hierarchy for the memory subsystem. Stdlib only — no external dependencies."""

from __future__ import annotations

__all__ = [
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


class ScopedMemoryError(Exception):
    """Base exception for all memory subsystem operations."""


class MemoryEntryNotFound(ScopedMemoryError):
    """Raised when a memory entry is not found."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"Memory entry not found: {entry_id}")


class KnowledgeEntryNotFound(ScopedMemoryError):
    """Raised when a knowledge entry is not found."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"Knowledge entry not found: {entry_id}")


class MemoryBudgetExceeded(ScopedMemoryError):
    """Raised when a memory budget limit is exceeded."""

    def __init__(self, profile_name: str, budget_type: str, current: int, limit: int) -> None:
        self.profile_name = profile_name
        self.budget_type = budget_type
        self.current = current
        self.limit = limit
        super().__init__(f"Memory budget exceeded for {profile_name}: {budget_type} ({current}/{limit})")


class InvalidTierTransition(ScopedMemoryError):
    """Raised when an invalid memory tier transition is attempted."""

    def __init__(self, from_tier: str, to_tier: str) -> None:
        self.from_tier = from_tier
        self.to_tier = to_tier
        super().__init__(f"Invalid tier transition: {from_tier} -> {to_tier}")


class InvalidMemoryScope(ScopedMemoryError):
    """Raised when a scope is not valid for a given role."""

    def __init__(self, scope: str, role: str) -> None:
        self.scope = scope
        self.role = role
        super().__init__(f"Scope {scope!r} is not valid for role {role!r}")


class ContextInjectionError(ScopedMemoryError):
    """Raised when context injection fails for a profile."""

    def __init__(self, profile_name: str, reason: str) -> None:
        self.profile_name = profile_name
        self.reason = reason
        super().__init__(f"Context injection failed for {profile_name}: {reason}")


class GarbageCollectionError(ScopedMemoryError):
    """Raised when garbage collection fails."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Garbage collection failed: {reason}")


class MemoryStoreError(ScopedMemoryError):
    """General memory store operation error."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
