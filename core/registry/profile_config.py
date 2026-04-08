"""Profile configuration template system.

Provides default SOUL.md templates and config.json defaults for each role,
plus utilities to create and load profile directories on disk.

Directory layout per profile::

    <base_path>/profiles/<profile_name>/
        SOUL.md
        config.json
        memory/
            knowledge_base/
            active_context/

Stdlib-only — no Hermes imports.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Role enum (local, mirrors core.registry.models.Role values)
# We redefine the values here to avoid importing from the rest of the
# registry package so this module stays standalone.
# ---------------------------------------------------------------------------

class _Role(str, Enum):
    CEO = "ceo"
    DEPARTMENT_HEAD = "department_head"
    PROJECT_MANAGER = "project_manager"


# ---------------------------------------------------------------------------
# SOUL.md templates
# ---------------------------------------------------------------------------

_SOUL_TEMPLATES: dict[str, str] = {
    _Role.CEO: """\
# {display_name}

**Profile:** {profile_name}
**Role:** Chief Executive Officer
**Department:** {department}

## Identity

You are {display_name}, the strategic leader of the organization.
{description}

## Responsibilities

- Set high-level strategy and organizational direction.
- Delegate work to department heads; never micro-manage individual tasks.
- Maintain a holistic overview of all departments and ongoing initiatives.
- Resolve cross-department conflicts and prioritize organizational goals.
- Ensure alignment between department objectives and the overall mission.

## Operating Principles

1. **Delegate, don't execute.** Route tasks to the appropriate department head.
2. **Think long-term.** Every decision should consider strategic impact.
3. **Stay informed.** Regularly review department status reports and metrics.
4. **Communicate clearly.** Provide context and rationale when delegating.
5. **Maintain accountability.** Track commitments and follow up on deliverables.
""",

    _Role.DEPARTMENT_HEAD: """\
# {display_name}

**Profile:** {profile_name}
**Role:** Department Head
**Department:** {department}

## Identity

You are {display_name}, the domain expert and leader of the {department} department.
{description}

## Responsibilities

- Own all initiatives within the {department} domain.
- Manage project managers assigned to your department.
- Report progress, blockers, and strategic recommendations to the CEO.
- Break high-level directives into actionable projects for your PMs.
- Ensure quality standards and best practices across your department.

## Operating Principles

1. **Domain expertise.** You are the authority on {department} matters.
2. **Manage through PMs.** Assign projects to project managers, provide guidance.
3. **Report upward.** Keep the CEO informed of status, risks, and wins.
4. **Collaborate laterally.** Coordinate with other department heads when needed.
5. **Empower your team.** Give PMs autonomy while maintaining oversight.
""",

    _Role.PROJECT_MANAGER: """\
# {display_name}

**Profile:** {profile_name}
**Role:** Project Manager
**Department:** {department}

## Identity

You are {display_name}, a project-focused manager within the {department} department.
{description}

## Responsibilities

- Execute specific projects assigned by your department head.
- Manage workers and coordinate task execution.
- Report project status, progress, and blockers to your department head.
- Break projects into discrete tasks and track completion.
- Maintain project documentation and knowledge artifacts.

## Operating Principles

1. **Project focus.** Stay scoped to your assigned projects.
2. **Hands-on execution.** Use tools directly to accomplish tasks.
3. **Report upward.** Keep your department head informed of progress.
4. **Manage workers.** Delegate sub-tasks and review output.
5. **Document everything.** Maintain clear records of decisions and progress.
""",
}


# ---------------------------------------------------------------------------
# Default config per role
# ---------------------------------------------------------------------------

@dataclass
class ProfileConfig:
    """Default configuration for a profile, serialized as config.json."""

    model: str = "sonnet"
    tools: list[str] = field(default_factory=list)
    provider: str = "anthropic"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileConfig":
        return cls(
            model=data.get("model", "sonnet"),
            tools=data.get("tools", []),
            provider=data.get("provider", "anthropic"),
            description=data.get("description", ""),
        )


_DEFAULT_CONFIGS: dict[str, ProfileConfig] = {
    _Role.CEO: ProfileConfig(
        model="sonnet",
        tools=["delegate", "memory", "search"],
        provider="anthropic",
        description="CEO — strategic leader",
    ),
    _Role.DEPARTMENT_HEAD: ProfileConfig(
        model="sonnet",
        tools=["delegate", "memory", "search", "terminal"],
        provider="anthropic",
        description="Department Head — domain expert",
    ),
    _Role.PROJECT_MANAGER: ProfileConfig(
        model="sonnet",
        tools=["delegate", "memory", "search", "terminal", "file"],
        provider="anthropic",
        description="Project Manager — execution focused",
    ),
}


# ---------------------------------------------------------------------------
# Helper: normalise role string to _Role enum
# ---------------------------------------------------------------------------

def _normalise_role(role: str | _Role) -> _Role:
    """Accept a role as a string or enum and return the canonical _Role."""
    if isinstance(role, _Role):
        return role
    # Accept the enum value string directly ("ceo", "department_head", ...)
    try:
        return _Role(role)
    except ValueError:
        pass
    # Accept the enum *name* ("CEO", "DEPARTMENT_HEAD", ...)
    try:
        return _Role[role.upper()]
    except KeyError:
        pass
    raise ValueError(
        f"Unknown role {role!r}. "
        f"Valid roles: {[r.value for r in _Role]}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_soul_template(role: str) -> str:
    """Return the default SOUL.md template string for *role*.

    The returned string contains ``{profile_name}``, ``{display_name}``,
    ``{department}``, and ``{description}`` placeholders ready for
    ``.format()`` or ``.format_map()`` substitution.

    Parameters
    ----------
    role:
        Role value — ``"ceo"``, ``"department_head"``, or
        ``"project_manager"`` (case-insensitive enum name also accepted).

    Returns
    -------
    str
        The raw template string with placeholders.
    """
    return _SOUL_TEMPLATES[_normalise_role(role)]


def get_default_config(role: str) -> dict[str, Any]:
    """Return the default config dict for *role*.

    Parameters
    ----------
    role:
        Role value — ``"ceo"``, ``"department_head"``, or
        ``"project_manager"``.

    Returns
    -------
    dict
        A fresh dict suitable for writing as ``config.json``.
    """
    return _DEFAULT_CONFIGS[_normalise_role(role)].to_dict()


def create_profile_directory(
    base_path: str | Path,
    profile_name: str,
    role: str,
    **template_vars: str,
) -> Path:
    """Create a profile directory tree with templated files.

    Creates::

        <base_path>/profiles/<profile_name>/
            SOUL.md
            config.json
            memory/knowledge_base/
            memory/active_context/

    Parameters
    ----------
    base_path:
        Root directory (e.g. ``~/.hermes``).
    profile_name:
        Unique profile identifier.
    role:
        One of ``"ceo"``, ``"department_head"``, ``"project_manager"``.
    **template_vars:
        Extra keyword arguments forwarded as template placeholders.
        Recognized keys: ``display_name``, ``department``, ``description``.
        Defaults are derived from *profile_name* and *role* if not provided.

    Returns
    -------
    Path
        The created profile directory (``<base_path>/profiles/<profile_name>``).
    """
    normalised_role = _normalise_role(role)
    base = Path(base_path).expanduser().resolve()
    profile_dir = base / "profiles" / profile_name

    # Create directory structure
    (profile_dir / "memory" / "knowledge_base").mkdir(parents=True, exist_ok=True)
    (profile_dir / "memory" / "active_context").mkdir(parents=True, exist_ok=True)

    # Merge defaults with caller-supplied template variables
    defaults: dict[str, str] = {
        "profile_name": profile_name,
        "display_name": template_vars.get(
            "display_name", profile_name.replace("-", " ").title()
        ),
        "department": template_vars.get("department", "General"),
        "description": template_vars.get("description", ""),
    }
    # Override defaults with any extra template_vars not already handled
    merged = {**defaults, **template_vars}

    # Write SOUL.md
    soul_content = _SOUL_TEMPLATES[normalised_role].format_map(merged)
    (profile_dir / "SOUL.md").write_text(soul_content, encoding="utf-8")

    # Write config.json
    config = _DEFAULT_CONFIGS[normalised_role].to_dict()
    (profile_dir / "config.json").write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )

    return profile_dir


def load_profile_config(base_path: str | Path, profile_name: str) -> dict[str, Any]:
    """Load and return the ``config.json`` for an existing profile.

    Parameters
    ----------
    base_path:
        Root directory (e.g. ``~/.hermes``).
    profile_name:
        Profile identifier whose config should be loaded.

    Returns
    -------
    dict
        Parsed JSON content of ``config.json``.

    Raises
    ------
    FileNotFoundError
        If the config file does not exist.
    """
    base = Path(base_path).expanduser().resolve()
    config_path = base / "profiles" / profile_name / "config.json"
    with config_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)
