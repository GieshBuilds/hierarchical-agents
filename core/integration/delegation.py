"""Delegation chain models for tracking multi-hop task delegation.

A *DelegationChain* represents a task flowing through the hierarchy,
e.g. CEO → CTO → PM.  Each step is a *DelegationHop*.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from core.integration.exceptions import ChainAlreadyComplete


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ChainStatus(str, Enum):
    """Overall status of a delegation chain."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class HopStatus(str, Enum):
    """Status of a single delegation hop."""

    PENDING = "pending"
    DELEGATED = "delegated"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_chain_id() -> str:
    """Generate a unique chain ID with ``chain-`` prefix."""
    return f"chain-{uuid.uuid4().hex[:12]}"


def _now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# DelegationHop
# ---------------------------------------------------------------------------

@dataclass
class DelegationHop:
    """A single hop in a delegation chain.

    Attributes
    ----------
    from_profile : str
        Profile delegating the task.
    to_profile : str
        Profile receiving the task.
    status : HopStatus
        Current hop status.
    message_id : str | None
        IPC message ID associated with this hop.
    delegated_at : datetime | None
        When the hop was delegated.
    completed_at : datetime | None
        When the hop was completed or failed.
    """

    from_profile: str = ""
    to_profile: str = ""
    status: HopStatus = HopStatus.PENDING
    message_id: Optional[str] = None
    delegated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def mark_delegated(self, message_id: str) -> None:
        """Mark the hop as delegated (message sent)."""
        self.status = HopStatus.DELEGATED
        self.message_id = message_id
        self.delegated_at = _now_utc()

    def mark_working(self) -> None:
        """Mark the hop as actively being worked on."""
        self.status = HopStatus.WORKING

    def mark_completed(self) -> None:
        """Mark the hop as completed."""
        self.status = HopStatus.COMPLETED
        self.completed_at = _now_utc()

    def mark_failed(self) -> None:
        """Mark the hop as failed."""
        self.status = HopStatus.FAILED
        self.completed_at = _now_utc()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the hop to a dictionary."""
        return {
            "from_profile": self.from_profile,
            "to_profile": self.to_profile,
            "status": self.status.value,
            "message_id": self.message_id,
            "delegated_at": self.delegated_at.isoformat() if self.delegated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DelegationHop:
        """Deserialize a hop from a dictionary."""
        return cls(
            from_profile=data["from_profile"],
            to_profile=data["to_profile"],
            status=HopStatus(data["status"]),
            message_id=data.get("message_id"),
            delegated_at=(
                datetime.fromisoformat(data["delegated_at"])
                if data.get("delegated_at")
                else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at")
                else None
            ),
        )


# ---------------------------------------------------------------------------
# DelegationChain
# ---------------------------------------------------------------------------

@dataclass
class DelegationChain:
    """A chain of delegation hops tracking a task through the hierarchy.

    Attributes
    ----------
    chain_id : str
        Unique identifier (chain-XXXX format).
    task_description : str
        Human-readable task description.
    originator : str
        Profile that originated the chain.
    status : ChainStatus
        Overall chain status.
    hops : list[DelegationHop]
        Ordered list of delegation hops.
    workers : list[str]
        Profiles actively working on the task.
    worker_results : dict[str, str]
        Mapping of subagent_id -> result string for completed workers.
    created_at : datetime
        When the chain was created (UTC).
    completed_at : datetime | None
        When the chain was completed (UTC).
    """

    chain_id: str = field(default_factory=_generate_chain_id)
    task_description: str = ""
    originator: str = ""
    status: ChainStatus = ChainStatus.PENDING
    hops: list[DelegationHop] = field(default_factory=list)
    workers: list[str] = field(default_factory=list)
    worker_results: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now_utc)
    completed_at: Optional[datetime] = None

    def add_hop(self, from_profile: str, to_profile: str) -> DelegationHop:
        """Add a new hop to the chain and return it."""
        hop = DelegationHop(from_profile=from_profile, to_profile=to_profile)
        self.hops.append(hop)
        return hop

    def current_hop(self) -> Optional[DelegationHop]:
        """Return the latest active (non-pending) hop, or None."""
        # Walk hops in reverse to find the latest one that is not PENDING.
        for hop in reversed(self.hops):
            if hop.status != HopStatus.PENDING:
                return hop
        return None

    def add_worker(self, profile: str) -> None:
        """Register a profile as working on this chain."""
        if profile not in self.workers:
            self.workers.append(profile)

    def activate(self) -> None:
        """Activate the chain.

        Raises
        ------
        ChainAlreadyComplete
            If the chain is in a terminal state.
        """
        if self.status in (
            ChainStatus.COMPLETED,
            ChainStatus.FAILED,
            ChainStatus.EXPIRED,
        ):
            raise ChainAlreadyComplete(self.chain_id)
        self.status = ChainStatus.ACTIVE

    def complete(self) -> None:
        """Mark the chain as completed.

        Raises
        ------
        ChainAlreadyComplete
            If the chain is already completed.
        """
        if self.status == ChainStatus.COMPLETED:
            raise ChainAlreadyComplete(self.chain_id)
        self.status = ChainStatus.COMPLETED
        self.completed_at = _now_utc()

    def fail(self) -> None:
        """Mark the chain as failed."""
        self.status = ChainStatus.FAILED
        self.completed_at = _now_utc()

    def expire(self) -> None:
        """Mark the chain as expired."""
        self.status = ChainStatus.EXPIRED
        self.completed_at = _now_utc()

    @property
    def is_terminal(self) -> bool:
        """Return True if the chain is in a terminal state."""
        return self.status in (
            ChainStatus.COMPLETED,
            ChainStatus.FAILED,
            ChainStatus.EXPIRED,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the chain to a dictionary."""
        return {
            "chain_id": self.chain_id,
            "task_description": self.task_description,
            "originator": self.originator,
            "status": self.status.value,
            "hops": [hop.to_dict() for hop in self.hops],
            "workers": list(self.workers),
            "worker_results": dict(self.worker_results),
            "created_at": self.created_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DelegationChain:
        """Deserialize a chain from a dictionary."""
        return cls(
            chain_id=data["chain_id"],
            task_description=data.get("task_description", ""),
            originator=data.get("originator", ""),
            status=ChainStatus(data["status"]),
            hops=[DelegationHop.from_dict(h) for h in data.get("hops", [])],
            workers=list(data.get("workers", [])),
            worker_results=dict(data.get("worker_results", {})),
            created_at=datetime.fromisoformat(data["created_at"]),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at")
                else None
            ),
        )
