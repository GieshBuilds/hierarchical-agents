"""Data models and constants for the Subagent/Worker Registry."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Prefix for subagent IDs to distinguish them from other UUIDs.
SUBAGENT_ID_PREFIX: str = "sa-"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SubagentStatus(str, Enum):
    """Lifecycle status of a subagent worker."""

    RUNNING = "running"
    SLEEPING = "sleeping"
    COMPLETED = "completed"
    ARCHIVED = "archived"


#: Valid status transitions as a state machine.
#: Maps current_status -> set of allowed target statuses.
VALID_TRANSITIONS: dict[SubagentStatus, set[SubagentStatus]] = {
    SubagentStatus.RUNNING: {SubagentStatus.SLEEPING, SubagentStatus.COMPLETED},
    SubagentStatus.SLEEPING: {SubagentStatus.RUNNING},
    SubagentStatus.COMPLETED: {SubagentStatus.ARCHIVED},
    SubagentStatus.ARCHIVED: set(),  # Terminal state — no transitions
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def generate_subagent_id() -> str:
    """Generate a unique subagent ID with the ``sa-`` prefix."""
    return f"{SUBAGENT_ID_PREFIX}{uuid.uuid4()}"


def _now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def is_valid_transition(current: SubagentStatus, target: SubagentStatus) -> bool:
    """Check whether a status transition is allowed by the state machine."""
    return target in VALID_TRANSITIONS.get(current, set())


# ---------------------------------------------------------------------------
# Subagent dataclass
# ---------------------------------------------------------------------------


@dataclass
class Subagent:
    """In-memory representation of a subagent worker row."""

    subagent_id: str
    project_manager: str
    task_goal: str
    status: str = SubagentStatus.RUNNING.value
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)
    conversation_path: str | None = None
    result_summary: str | None = None
    artifacts: list[str] = field(default_factory=list)
    token_cost: int = 0
    parent_request_id: str | None = None

    def artifacts_as_json(self) -> str:
        """Serialize the artifacts list to a JSON string for DB storage."""
        return json.dumps(self.artifacts)

    @staticmethod
    def artifacts_from_json(raw: str | None) -> list[str]:
        """Deserialize a JSON string to an artifacts list."""
        if not raw:
            return []
        try:
            result = json.loads(raw)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
