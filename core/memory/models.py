"""Memory data models, enums, and constants. Stdlib only — no external dependencies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ---------------------------------------------------------------------------
# Helper functions (private)
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def _dt_to_iso(dt: datetime | None) -> str | None:
    """Convert a datetime to ISO-8601 string, or None."""
    if dt is None:
        return None
    return dt.isoformat()


def _iso_to_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string to datetime, or return None."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MemoryScope(str, Enum):
    """Scope of memory entries mapped to hierarchy roles."""

    strategic = "strategic"  # CEO
    domain = "domain"        # Department heads
    project = "project"      # Project managers
    task = "task"            # Workers


class MemoryTier(str, Enum):
    """Storage tier for memory lifecycle management."""

    hot = "hot"
    warm = "warm"
    cool = "cool"
    cold = "cold"


class MemoryEntryType(str, Enum):
    """Classification of memory entry content."""

    preference = "preference"
    decision = "decision"
    learning = "learning"
    context = "context"
    summary = "summary"
    artifact = "artifact"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Completed entries move to warm immediately.
WARM_AGE_DAYS: int = 0
#: 30 days after completion -> cool.
COOL_AGE_DAYS: int = 30
#: 90 days after completion -> cold.
COLD_AGE_DAYS: int = 90

#: Default maximum number of memory entries per profile.
DEFAULT_MAX_ENTRIES: int = 1000
#: Default maximum byte budget per profile (10 MB).
DEFAULT_MAX_BYTES: int = 10 * 1024 * 1024

#: Valid tier transitions (forward only).
VALID_TIER_TRANSITIONS: dict[MemoryTier, set[MemoryTier]] = {
    MemoryTier.hot: {MemoryTier.warm},
    MemoryTier.warm: {MemoryTier.cool},
    MemoryTier.cool: {MemoryTier.cold},
    MemoryTier.cold: set(),  # terminal
}

#: Role to scope mapping.
ROLE_SCOPE_MAP: dict[str, MemoryScope] = {
    "ceo": MemoryScope.strategic,
    "department_head": MemoryScope.domain,
    "project_manager": MemoryScope.project,
    "specialist": MemoryScope.task,
    "worker": MemoryScope.task,
}


# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------


def generate_memory_id() -> str:
    """Generate a unique memory entry ID with ``mem-`` prefix."""
    return f"mem-{uuid.uuid4().hex[:8]}"


def generate_knowledge_id() -> str:
    """Generate a unique knowledge entry ID with ``kb-`` prefix."""
    return f"kb-{uuid.uuid4().hex[:8]}"


def generate_transition_id() -> str:
    """Generate a unique tier-transition ID with ``tt-`` prefix."""
    return f"tt-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def scope_for_role(role: str) -> MemoryScope:
    """Return the :class:`MemoryScope` corresponding to *role*.

    Raises :exc:`ValueError` if *role* is unknown.
    """
    if role not in ROLE_SCOPE_MAP:
        raise ValueError(f"Unknown role: {role}")
    return ROLE_SCOPE_MAP[role]


def is_valid_tier_transition(
    from_tier: MemoryTier,
    to_tier: MemoryTier,
) -> bool:
    """Check whether a tier transition is allowed (forward-only)."""
    return to_tier in VALID_TIER_TRANSITIONS.get(from_tier, set())


def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars per token)."""
    return len(text) // 4


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """A single memory entry scoped to a profile."""

    entry_id: str
    profile_name: str
    scope: MemoryScope
    tier: MemoryTier
    entry_type: MemoryEntryType
    content: str
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)
    accessed_at: datetime = field(default_factory=_now_utc)
    expires_at: datetime | None = None
    byte_size: int = 0

    def __post_init__(self) -> None:
        if not self.byte_size:
            self.byte_size = len(self.content.encode("utf-8"))

    def is_expired(self) -> bool:
        """Return True if the entry has passed its expiry time."""
        if self.expires_at is None:
            return False
        return _now_utc() >= self.expires_at

    def can_transition_to(self, new_tier: MemoryTier) -> bool:
        """Check if transition from current tier to *new_tier* is valid."""
        return new_tier in VALID_TIER_TRANSITIONS.get(self.tier, set())

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        return {
            "entry_id": self.entry_id,
            "profile_name": self.profile_name,
            "scope": self.scope.value,
            "tier": self.tier.value,
            "entry_type": self.entry_type.value,
            "content": self.content,
            "metadata": self.metadata,
            "created_at": _dt_to_iso(self.created_at),
            "updated_at": _dt_to_iso(self.updated_at),
            "accessed_at": _dt_to_iso(self.accessed_at),
            "expires_at": _dt_to_iso(self.expires_at),
            "byte_size": self.byte_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MemoryEntry:
        """Deserialize from a plain dict."""
        return cls(
            entry_id=data["entry_id"],
            profile_name=data["profile_name"],
            scope=MemoryScope(data["scope"]),
            tier=MemoryTier(data["tier"]),
            entry_type=MemoryEntryType(data["entry_type"]),
            content=data["content"],
            metadata=data.get("metadata", {}),
            created_at=_iso_to_dt(data["created_at"]) or _now_utc(),
            updated_at=_iso_to_dt(data["updated_at"]) or _now_utc(),
            accessed_at=_iso_to_dt(data["accessed_at"]) or _now_utc(),
            expires_at=_iso_to_dt(data.get("expires_at")),
            byte_size=data.get("byte_size", 0),
        )


@dataclass
class KnowledgeEntry:
    """A knowledge-base entry for cross-profile knowledge sharing."""

    entry_id: str
    profile_name: str
    category: str
    title: str
    content: str
    source_profile: str = ""
    source_context: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        return {
            "entry_id": self.entry_id,
            "profile_name": self.profile_name,
            "category": self.category,
            "title": self.title,
            "content": self.content,
            "source_profile": self.source_profile,
            "source_context": self.source_context,
            "tags": list(self.tags),
            "created_at": _dt_to_iso(self.created_at),
            "updated_at": _dt_to_iso(self.updated_at),
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeEntry:
        """Deserialize from a plain dict."""
        return cls(
            entry_id=data["entry_id"],
            profile_name=data["profile_name"],
            category=data["category"],
            title=data["title"],
            content=data["content"],
            source_profile=data.get("source_profile", ""),
            source_context=data.get("source_context", ""),
            tags=list(data.get("tags", [])),
            created_at=_iso_to_dt(data["created_at"]) or _now_utc(),
            updated_at=_iso_to_dt(data["updated_at"]) or _now_utc(),
        )


@dataclass
class MemoryBudget:
    """Per-profile memory budget constraints."""

    profile_name: str
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_bytes: int = DEFAULT_MAX_BYTES
    tier_quotas: dict[str, int] = field(default_factory=lambda: {
        "hot": 200,
        "warm": 300,
        "cool": 300,
        "cold": 200,
    })

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        return {
            "profile_name": self.profile_name,
            "max_entries": self.max_entries,
            "max_bytes": self.max_bytes,
            "tier_quotas": dict(self.tier_quotas),
        }

    @classmethod
    def from_dict(cls, data: dict) -> MemoryBudget:
        """Deserialize from a plain dict."""
        return cls(
            profile_name=data["profile_name"],
            max_entries=data.get("max_entries", DEFAULT_MAX_ENTRIES),
            max_bytes=data.get("max_bytes", DEFAULT_MAX_BYTES),
            tier_quotas=data.get("tier_quotas", {
                "hot": 200,
                "warm": 300,
                "cool": 300,
                "cold": 200,
            }),
        )


@dataclass
class ContextBrief:
    """A context brief assembled for agent activation or task handoff."""

    profile_name: str
    context_type: str  # 'activation', 'task_brief', 'escalation'
    sections: dict[str, str] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    token_estimate: int = 0
    created_at: datetime = field(default_factory=_now_utc)

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        return {
            "profile_name": self.profile_name,
            "context_type": self.context_type,
            "sections": dict(self.sections),
            "metadata": dict(self.metadata),
            "token_estimate": self.token_estimate,
            "created_at": _dt_to_iso(self.created_at),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ContextBrief:
        """Deserialize from a plain dict."""
        tok = data.get("token_estimate", 0)
        return cls(
            profile_name=data["profile_name"],
            context_type=data["context_type"],
            sections=data.get("sections", {}),
            metadata=data.get("metadata", {}),
            token_estimate=tok,
            created_at=_iso_to_dt(
                data.get("created_at"),
            ) or _now_utc(),
        )


@dataclass
class StatusSummary:
    """A status summary produced by an agent for its parent."""

    profile_name: str
    summary_type: str  # 'interaction', 'periodic', 'escalation'
    decisions: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now_utc)

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        return {
            "profile_name": self.profile_name,
            "summary_type": self.summary_type,
            "decisions": list(self.decisions),
            "deliverables": list(self.deliverables),
            "blockers": list(self.blockers),
            "metrics": dict(self.metrics),
            "created_at": _dt_to_iso(self.created_at),
        }

    @classmethod
    def from_dict(cls, data: dict) -> StatusSummary:
        """Deserialize from a plain dict."""
        return cls(
            profile_name=data["profile_name"],
            summary_type=data["summary_type"],
            decisions=list(data.get("decisions", [])),
            deliverables=list(data.get("deliverables", [])),
            blockers=list(data.get("blockers", [])),
            metrics=data.get("metrics", {}),
            created_at=_iso_to_dt(
                data.get("created_at"),
            ) or _now_utc(),
        )


@dataclass
class TierTransition:
    """Record of a memory entry moving between storage tiers."""

    transition_id: str
    entry_id: str
    from_tier: MemoryTier
    to_tier: MemoryTier
    reason: str
    transitioned_at: datetime = field(default_factory=_now_utc)

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        return {
            "transition_id": self.transition_id,
            "entry_id": self.entry_id,
            "from_tier": self.from_tier.value,
            "to_tier": self.to_tier.value,
            "reason": self.reason,
            "transitioned_at": _dt_to_iso(self.transitioned_at),
        }

    @classmethod
    def from_dict(cls, data: dict) -> TierTransition:
        """Deserialize from a plain dict."""
        return cls(
            transition_id=data["transition_id"],
            entry_id=data["entry_id"],
            from_tier=MemoryTier(data["from_tier"]),
            to_tier=MemoryTier(data["to_tier"]),
            reason=data["reason"],
            transitioned_at=_iso_to_dt(
                data["transitioned_at"],
            ) or _now_utc(),
        )


@dataclass
class GCReport:
    """Report produced by a garbage-collection / tier-management pass."""

    entries_transitioned: int = 0
    entries_purged: int = 0
    bytes_freed: int = 0
    budget_status: dict = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    transitions: list[TierTransition] = field(default_factory=list)
    purged_ids: list[str] = field(default_factory=list)
    ran_at: datetime = field(default_factory=_now_utc)
    dry_run: bool = False

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        return {
            "entries_transitioned": self.entries_transitioned,
            "entries_purged": self.entries_purged,
            "bytes_freed": self.bytes_freed,
            "budget_status": dict(self.budget_status),
            "recommendations": list(self.recommendations),
            "transitions": [t.to_dict() for t in self.transitions],
            "purged_ids": list(self.purged_ids),
            "ran_at": _dt_to_iso(self.ran_at),
            "dry_run": self.dry_run,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GCReport:
        """Deserialize from a plain dict."""
        transitions = [
            TierTransition.from_dict(t)
            for t in data.get("transitions", [])
        ]
        return cls(
            entries_transitioned=data.get(
                "entries_transitioned", 0,
            ),
            entries_purged=data.get("entries_purged", 0),
            bytes_freed=data.get("bytes_freed", 0),
            budget_status=data.get("budget_status", {}),
            recommendations=list(
                data.get("recommendations", []),
            ),
            transitions=transitions,
            purged_ids=list(data.get("purged_ids", [])),
            ran_at=_iso_to_dt(data.get("ran_at")) or _now_utc(),
            dry_run=data.get("dry_run", False),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "MemoryScope",
    "MemoryTier",
    "MemoryEntryType",
    # Constants
    "WARM_AGE_DAYS",
    "COOL_AGE_DAYS",
    "COLD_AGE_DAYS",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_BYTES",
    "VALID_TIER_TRANSITIONS",
    "ROLE_SCOPE_MAP",
    # ID generators
    "generate_memory_id",
    "generate_knowledge_id",
    "generate_transition_id",
    # Helper functions
    "scope_for_role",
    "is_valid_tier_transition",
    "estimate_tokens",
    # Dataclasses
    "MemoryEntry",
    "KnowledgeEntry",
    "MemoryBudget",
    "ContextBrief",
    "StatusSummary",
    "TierTransition",
    "GCReport",
]
