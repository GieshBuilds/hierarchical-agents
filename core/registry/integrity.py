"""Hierarchy integrity checker for the Profile Registry.

Provides a standalone ``scan_integrity()`` function that inspects the
entire database and returns a list of :class:`IntegrityIssue` objects
describing any problems found.

Usage::

    from core.registry.integrity import scan_integrity
    from core.registry.profile_registry import ProfileRegistry

    registry = ProfileRegistry(":memory:")
    issues = scan_integrity(registry)
    for issue in issues:
        print(f"[{issue.severity}] {issue.profile_name}: {issue.message}")
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from core.registry.models import (
    PROFILE_NAME_MAX_LENGTH,
    PROFILE_NAME_PATTERN,
    Role,
    Status,
)

if TYPE_CHECKING:
    from core.registry.profile_registry import ProfileRegistry


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    """Severity level of an integrity issue."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class IntegrityIssue:
    """A single integrity problem found during a scan.

    Attributes
    ----------
    severity:
        ``"error"`` for rule violations that break the hierarchy,
        ``"warning"`` for non-critical issues.
    profile_name:
        The profile that triggered this issue, or ``"<global>"`` for
        system-wide checks.
    message:
        Human-readable description of the problem.
    rule_violated:
        Machine-readable identifier for the rule, e.g.
        ``"exactly_one_ceo"`` or ``"orphaned_profile"``.
    """

    severity: str
    profile_name: str
    message: str
    rule_violated: str


# ---------------------------------------------------------------------------
# Rule identifiers (used as ``rule_violated`` values)
# ---------------------------------------------------------------------------

RULE_EXACTLY_ONE_CEO = "exactly_one_ceo"
RULE_DEPT_HEAD_PARENT_CEO = "dept_head_parent_ceo"
RULE_PM_PARENT_DEPT_HEAD = "pm_parent_dept_head"
RULE_SPECIALIST_PARENT_PM = "specialist_parent_pm"
RULE_ORPHANED_PROFILE = "orphaned_profile"
RULE_CIRCULAR_REFERENCE = "circular_reference"
RULE_ARCHIVED_WITH_ACTIVE_DEPS = "archived_with_active_dependents"
RULE_INVALID_PROFILE_NAME = "invalid_profile_name"
RULE_CONFIG_PATH_MISSING = "config_path_missing"


# ---------------------------------------------------------------------------
# Individual rule checkers
# ---------------------------------------------------------------------------

def _check_exactly_one_ceo(
    profiles: list[dict],
) -> list[IntegrityIssue]:
    """Exactly one active/suspended CEO must exist."""
    issues: list[IntegrityIssue] = []
    ceo_profiles = [
        p for p in profiles
        if p["role"] == Role.CEO.value
        and p["status"] != Status.ARCHIVED.value
    ]
    if len(ceo_profiles) == 0:
        issues.append(IntegrityIssue(
            severity=Severity.ERROR.value,
            profile_name="<global>",
            message="No active CEO profile found — the hierarchy requires exactly one",
            rule_violated=RULE_EXACTLY_ONE_CEO,
        ))
    elif len(ceo_profiles) > 1:
        names = [p["profile_name"] for p in ceo_profiles]
        issues.append(IntegrityIssue(
            severity=Severity.ERROR.value,
            profile_name="<global>",
            message=f"Multiple CEO profiles found: {names} — only one is allowed",
            rule_violated=RULE_EXACTLY_ONE_CEO,
        ))
    return issues


RULE_NON_CEO_HAS_PARENT = "non_ceo_has_parent"


def _check_non_ceo_has_parent(
    profiles: list[dict],
) -> list[IntegrityIssue]:
    """All non-archived, non-CEO profiles must have a parent."""
    issues: list[IntegrityIssue] = []
    for p in profiles:
        if (
            p["role"] != Role.CEO.value
            and p["status"] != Status.ARCHIVED.value
            and p["parent_profile"] is None
        ):
            issues.append(IntegrityIssue(
                severity=Severity.ERROR.value,
                profile_name=p["profile_name"],
                message=f"{p['role']} has no parent — all non-CEO profiles require a parent",
                rule_violated=RULE_NON_CEO_HAS_PARENT,
            ))
    return issues


def _check_dept_heads_parent_ceo(
    profiles: list[dict],
    by_name: dict[str, dict],
) -> list[IntegrityIssue]:
    """Legacy check — kept for backward compatibility but no longer used by scan_integrity."""
    issues: list[IntegrityIssue] = []
    for p in profiles:
        if (
            p["role"] == Role.DEPARTMENT_HEAD.value
            and p["status"] != Status.ARCHIVED.value
        ):
            parent_name = p["parent_profile"]
            if parent_name is None:
                issues.append(IntegrityIssue(
                    severity=Severity.ERROR.value,
                    profile_name=p["profile_name"],
                    message="Department head has no parent — must report to CEO",
                    rule_violated=RULE_DEPT_HEAD_PARENT_CEO,
                ))
                continue
            parent = by_name.get(parent_name)
            if parent is None:
                # Orphan check handled separately
                continue
            if parent["role"] != Role.CEO.value:
                issues.append(IntegrityIssue(
                    severity=Severity.ERROR.value,
                    profile_name=p["profile_name"],
                    message=(
                        f"Department head parents to '{parent_name}' "
                        f"(role={parent['role']}) — must report to CEO"
                    ),
                    rule_violated=RULE_DEPT_HEAD_PARENT_CEO,
                ))
    return issues


def _check_pms_parent_dept_head(
    profiles: list[dict],
    by_name: dict[str, dict],
) -> list[IntegrityIssue]:
    """All non-archived PMs must parent to the CEO or a department head."""
    issues: list[IntegrityIssue] = []
    for p in profiles:
        if (
            p["role"] == Role.PROJECT_MANAGER.value
            and p["status"] != Status.ARCHIVED.value
        ):
            parent_name = p["parent_profile"]
            if parent_name is None:
                issues.append(IntegrityIssue(
                    severity=Severity.ERROR.value,
                    profile_name=p["profile_name"],
                    message="Project manager has no parent — must report to the CEO or a department head",
                    rule_violated=RULE_PM_PARENT_DEPT_HEAD,
                ))
                continue
            parent = by_name.get(parent_name)
            if parent is None:
                # Orphan check handled separately
                continue
            if parent["role"] not in (Role.CEO.value, Role.DEPARTMENT_HEAD.value):
                issues.append(IntegrityIssue(
                    severity=Severity.ERROR.value,
                    profile_name=p["profile_name"],
                    message=(
                        f"Project manager parents to '{parent_name}' "
                        f"(role={parent['role']}) — must report to the CEO or a department head"
                    ),
                    rule_violated=RULE_PM_PARENT_DEPT_HEAD,
                ))
    return issues


def _check_specialists_parent_pm(
    profiles: list[dict],
    by_name: dict[str, dict],
) -> list[IntegrityIssue]:
    """All non-archived specialists must parent to a CEO, department head, or project manager."""
    valid_parent_roles = {
        Role.CEO.value,
        Role.DEPARTMENT_HEAD.value,
        Role.PROJECT_MANAGER.value,
    }
    issues: list[IntegrityIssue] = []
    for p in profiles:
        if (
            p["role"] == Role.SPECIALIST.value
            and p["status"] != Status.ARCHIVED.value
        ):
            parent_name = p["parent_profile"]
            if parent_name is None:
                issues.append(IntegrityIssue(
                    severity=Severity.ERROR.value,
                    profile_name=p["profile_name"],
                    message="Specialist has no parent — must report to a CEO, department head, or project manager",
                    rule_violated=RULE_SPECIALIST_PARENT_PM,
                ))
                continue
            parent = by_name.get(parent_name)
            if parent is None:
                # Orphan check handled separately
                continue
            if parent["role"] not in valid_parent_roles:
                issues.append(IntegrityIssue(
                    severity=Severity.ERROR.value,
                    profile_name=p["profile_name"],
                    message=(
                        f"Specialist parents to '{parent_name}' "
                        f"(role={parent['role']}) — must report to a CEO, department head, or project manager"
                    ),
                    rule_violated=RULE_SPECIALIST_PARENT_PM,
                ))
    return issues


def _check_orphaned_profiles(
    profiles: list[dict],
    by_name: dict[str, dict],
) -> list[IntegrityIssue]:
    """No profile should reference a parent that doesn't exist."""
    issues: list[IntegrityIssue] = []
    for p in profiles:
        parent_name = p["parent_profile"]
        if parent_name is not None and parent_name not in by_name:
            issues.append(IntegrityIssue(
                severity=Severity.ERROR.value,
                profile_name=p["profile_name"],
                message=(
                    f"Parent profile '{parent_name}' does not exist — "
                    f"profile is orphaned"
                ),
                rule_violated=RULE_ORPHANED_PROFILE,
            ))
    return issues


def _check_circular_references(
    profiles: list[dict],
    by_name: dict[str, dict],
) -> list[IntegrityIssue]:
    """No profile should be its own ancestor (cycle detection)."""
    issues: list[IntegrityIssue] = []
    # Track already-reported cycles to avoid duplicates
    reported_cycles: set[frozenset[str]] = set()

    for p in profiles:
        visited: set[str] = set()
        current_name: str | None = p["profile_name"]
        cycle_members: list[str] = []

        while current_name is not None:
            if current_name in visited:
                # We found a cycle
                cycle_key = frozenset(visited)
                if cycle_key not in reported_cycles:
                    reported_cycles.add(cycle_key)
                    issues.append(IntegrityIssue(
                        severity=Severity.ERROR.value,
                        profile_name=p["profile_name"],
                        message=(
                            f"Circular reference detected in hierarchy chain: "
                            f"{' -> '.join(cycle_members)} -> {current_name}"
                        ),
                        rule_violated=RULE_CIRCULAR_REFERENCE,
                    ))
                break
            visited.add(current_name)
            cycle_members.append(current_name)
            node = by_name.get(current_name)
            if node is None:
                break
            current_name = node["parent_profile"]

    return issues


def _check_archived_with_active_dependents(
    profiles: list[dict],
    children_map: dict[str, list[dict]],
) -> list[IntegrityIssue]:
    """No archived profile should have active (non-archived) dependents."""
    issues: list[IntegrityIssue] = []
    for p in profiles:
        if p["status"] == Status.ARCHIVED.value:
            children = children_map.get(p["profile_name"], [])
            active_children = [
                c for c in children
                if c["status"] != Status.ARCHIVED.value
            ]
            if active_children:
                child_names = [c["profile_name"] for c in active_children]
                issues.append(IntegrityIssue(
                    severity=Severity.ERROR.value,
                    profile_name=p["profile_name"],
                    message=(
                        f"Archived profile has active dependents: {child_names}"
                    ),
                    rule_violated=RULE_ARCHIVED_WITH_ACTIVE_DEPS,
                ))
    return issues


def _check_profile_names(
    profiles: list[dict],
) -> list[IntegrityIssue]:
    """All profile names must pass the naming convention validation."""
    issues: list[IntegrityIssue] = []
    for p in profiles:
        name = p["profile_name"]
        if not name:
            issues.append(IntegrityIssue(
                severity=Severity.ERROR.value,
                profile_name=name or "<empty>",
                message="Profile name is empty",
                rule_violated=RULE_INVALID_PROFILE_NAME,
            ))
            continue
        if len(name) > PROFILE_NAME_MAX_LENGTH:
            issues.append(IntegrityIssue(
                severity=Severity.ERROR.value,
                profile_name=name,
                message=(
                    f"Profile name exceeds {PROFILE_NAME_MAX_LENGTH} characters "
                    f"(length={len(name)})"
                ),
                rule_violated=RULE_INVALID_PROFILE_NAME,
            ))
        if not PROFILE_NAME_PATTERN.match(name):
            issues.append(IntegrityIssue(
                severity=Severity.ERROR.value,
                profile_name=name,
                message=(
                    "Profile name does not match naming convention "
                    "(must be ^[a-z][a-z0-9-]*$)"
                ),
                rule_violated=RULE_INVALID_PROFILE_NAME,
            ))
    return issues


def _check_config_paths(
    profiles: list[dict],
) -> list[IntegrityIssue]:
    """Config paths, when set, should exist on disk (warning only)."""
    issues: list[IntegrityIssue] = []
    for p in profiles:
        config_path = p.get("config_path")
        if config_path is not None and config_path.strip():
            expanded = os.path.expanduser(config_path)
            if not os.path.exists(expanded):
                issues.append(IntegrityIssue(
                    severity=Severity.WARNING.value,
                    profile_name=p["profile_name"],
                    message=f"Config path does not exist on disk: '{config_path}'",
                    rule_violated=RULE_CONFIG_PATH_MISSING,
                ))
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_integrity(registry: ProfileRegistry) -> list[IntegrityIssue]:
    """Scan the entire registry database for integrity issues.

    This performs a read-only scan of all profiles and checks every
    structural rule defined for the hierarchy.  It does NOT modify
    any data.

    Parameters
    ----------
    registry:
        The :class:`ProfileRegistry` instance to scan.

    Returns
    -------
    list[IntegrityIssue]
        A (possibly empty) list of integrity issues found, sorted by
        severity (errors first) then profile name.
    """
    # Fetch all profiles as dicts for easy manipulation.
    all_profiles_objs = registry.list_profiles()
    profiles: list[dict] = [
        {
            "profile_name": p.profile_name,
            "display_name": p.display_name,
            "role": p.role,
            "parent_profile": p.parent_profile,
            "department": p.department,
            "status": p.status,
            "config_path": p.config_path,
            "description": p.description,
        }
        for p in all_profiles_objs
    ]

    # Build lookup structures.
    by_name: dict[str, dict] = {p["profile_name"]: p for p in profiles}
    children_map: dict[str, list[dict]] = {}
    for p in profiles:
        parent = p["parent_profile"]
        if parent is not None:
            children_map.setdefault(parent, []).append(p)

    # Run all checks.
    issues: list[IntegrityIssue] = []
    issues.extend(_check_exactly_one_ceo(profiles))
    issues.extend(_check_non_ceo_has_parent(profiles))
    issues.extend(_check_orphaned_profiles(profiles, by_name))
    issues.extend(_check_circular_references(profiles, by_name))
    issues.extend(_check_archived_with_active_dependents(profiles, children_map))
    issues.extend(_check_profile_names(profiles))
    issues.extend(_check_config_paths(profiles))

    # Sort: errors first, then by profile name.
    severity_order = {Severity.ERROR.value: 0, Severity.WARNING.value: 1}
    issues.sort(
        key=lambda i: (severity_order.get(i.severity, 2), i.profile_name)
    )

    return issues
