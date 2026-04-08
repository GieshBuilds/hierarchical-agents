"""Data models and constants for the Profile Registry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from core.registry.exceptions import InvalidProfileName

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Profile names must start with a lowercase letter followed by lowercase
#: alphanumerics or hyphens.  Maximum 64 characters.
PROFILE_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9-]*$")
PROFILE_NAME_MAX_LENGTH: int = 64


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Role(str, Enum):
    """Valid agent roles in the hierarchy."""

    CEO = "ceo"
    DEPARTMENT_HEAD = "department_head"
    PROJECT_MANAGER = "project_manager"
    SPECIALIST = "specialist"


class Status(str, Enum):
    """Lifecycle status of a profile."""

    ONBOARDING = "onboarding"  # New profile: awaiting discovery brief from parent PM
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


def role_requires_initial_plan(role: str) -> bool:
    """Return whether *role* requires an initial plan before activation.

    Owner directive: new PMs and specialists must not become active until the
    parent has both completed discovery and attached an initial plan. We apply
    the same policy to department heads for consistency.
    """
    return role in {
        Role.DEPARTMENT_HEAD.value,
        Role.PROJECT_MANAGER.value,
        Role.SPECIALIST.value,
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_profile_name(name: str) -> str:
    """Validate and return *name*, or raise :exc:`InvalidProfileName`.

    Rules:
    - Must match ``^[a-z][a-z0-9-]*$``
    - Maximum 64 characters
    """
    if not name:
        raise InvalidProfileName(name, "name must not be empty")
    if len(name) > PROFILE_NAME_MAX_LENGTH:
        raise InvalidProfileName(
            name,
            f"exceeds maximum length of {PROFILE_NAME_MAX_LENGTH} characters",
        )
    if not PROFILE_NAME_PATTERN.match(name):
        raise InvalidProfileName(
            name,
            "must start with a lowercase letter and contain only lowercase "
            "letters, digits, and hyphens",
        )
    return name


# ---------------------------------------------------------------------------
# Profile dataclass
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


@dataclass
class Profile:
    """In-memory representation of an agent profile row."""

    profile_name: str
    display_name: str
    role: str
    parent_profile: str | None = None
    department: str | None = None
    status: str = Status.ACTIVE.value
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)
    config_path: str | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Required fields for an onboarding brief
# ---------------------------------------------------------------------------

#: Fields that MUST be present (non-empty) to complete onboarding.
ONBOARDING_REQUIRED_FIELDS: tuple[str, ...] = (
    "role_definition",        # What this profile does
    "scope",                  # Boundaries / what is NOT in scope
    "success_criteria",       # How we know this agent succeeded
    "handoff_protocol",       # How finished work is returned upstream
)

#: Optional but strongly recommended fields.
ONBOARDING_RECOMMENDED_FIELDS: tuple[str, ...] = (
    "discovery_answers",      # Free-form Q&A from the discovery interview
    "dependencies",           # Other profiles / systems this agent depends on
    "first_task",             # Concrete first task to confirm readiness
)


@dataclass
class OnboardingBrief:
    """Structured brief produced by the parent PM during onboarding.

    Stored in the ``onboarding_briefs`` table.  A complete brief with all
    required fields is what allows a profile to transition from
    ``onboarding`` → ``active``.
    """

    profile_name: str
    parent_pm: str
    role_definition: str
    scope: str
    success_criteria: str
    handoff_protocol: str
    discovery_answers: str = ""
    dependencies: str = ""
    first_task: str = ""
    submitted_at: datetime = field(default_factory=_now_utc)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON storage."""
        return {
            "profile_name": self.profile_name,
            "parent_pm": self.parent_pm,
            "role_definition": self.role_definition,
            "scope": self.scope,
            "success_criteria": self.success_criteria,
            "handoff_protocol": self.handoff_protocol,
            "discovery_answers": self.discovery_answers,
            "dependencies": self.dependencies,
            "first_task": self.first_task,
            "submitted_at": self.submitted_at.isoformat(),
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OnboardingBrief":
        """Deserialise from a plain dict (e.g. from SQLite JSON column)."""
        submitted_raw = data.get("submitted_at")
        submitted_at = (
            datetime.fromisoformat(submitted_raw)
            if submitted_raw
            else _now_utc()
        )
        return cls(
            profile_name=data["profile_name"],
            parent_pm=data["parent_pm"],
            role_definition=data["role_definition"],
            scope=data["scope"],
            success_criteria=data["success_criteria"],
            handoff_protocol=data["handoff_protocol"],
            discovery_answers=data.get("discovery_answers", ""),
            dependencies=data.get("dependencies", ""),
            first_task=data.get("first_task", ""),
            submitted_at=submitted_at,
            extra=data.get("extra", {}),
        )


@dataclass
class OnboardingState:
    """Machine-readable onboarding readiness state for a profile."""

    profile_name: str
    owner_profile: str
    discovery_completed_at: datetime | None = None
    brief_completed_at: datetime | None = None
    plan_required: bool = True
    plan_completed_at: datetime | None = None
    plan_summary: str = ""
    plan_path: str = ""
    activation_ready: bool = False
    activated_at: datetime | None = None
    notes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "owner_profile": self.owner_profile,
            "discovery_completed_at": self.discovery_completed_at.isoformat()
            if self.discovery_completed_at else None,
            "brief_completed_at": self.brief_completed_at.isoformat()
            if self.brief_completed_at else None,
            "plan_required": self.plan_required,
            "plan_completed_at": self.plan_completed_at.isoformat()
            if self.plan_completed_at else None,
            "plan_summary": self.plan_summary,
            "plan_path": self.plan_path,
            "activation_ready": self.activation_ready,
            "activated_at": self.activated_at.isoformat()
            if self.activated_at else None,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OnboardingState":
        def _parse_dt(value: str | None) -> datetime | None:
            return datetime.fromisoformat(value) if value else None

        return cls(
            profile_name=data["profile_name"],
            owner_profile=data["owner_profile"],
            discovery_completed_at=_parse_dt(data.get("discovery_completed_at")),
            brief_completed_at=_parse_dt(data.get("brief_completed_at")),
            plan_required=bool(data.get("plan_required", True)),
            plan_completed_at=_parse_dt(data.get("plan_completed_at")),
            plan_summary=data.get("plan_summary", ""),
            plan_path=data.get("plan_path", ""),
            activation_ready=bool(data.get("activation_ready", False)),
            activated_at=_parse_dt(data.get("activated_at")),
            notes=data.get("notes", {}),
        )
