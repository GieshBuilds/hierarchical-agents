"""Template engine for generating agent documentation from role-based templates.

Templates use {{variable}} syntax for substitution.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).parent
ROLES_DIR = TEMPLATES_DIR / "roles"
PLAYBOOK_PATH = TEMPLATES_DIR / "PLAYBOOK.md"

# Template files generated per profile
PROFILE_DOCS = ["SOUL.md", "HANDOFF.md", "WORKFLOWS.md", "TOOLS.md", "CONTEXT.md"]

# Variables available in templates
TEMPLATE_VARS = [
    "profile_name",     # e.g. "pm-backend"
    "display_name",     # e.g. "Backend PM"
    "role",             # e.g. "project_manager"
    "parent_profile",   # e.g. "cto"
    "department",       # e.g. "engineering"
    "description",      # e.g. "Manages backend implementation"
]


def render_template(template_content: str, variables: dict[str, Any]) -> str:
    """Render a template by replacing {{variable}} placeholders."""
    def replacer(match):
        key = match.group(1).strip()
        return str(variables.get(key, match.group(0)))

    return re.sub(r"\{\{(\w+)\}\}", replacer, template_content)


def get_role_templates(role: str) -> dict[str, str]:
    """Return the raw template content for each doc file for a given role.

    Returns a dict of filename -> template content.
    """
    role_dir = ROLES_DIR / role
    if not role_dir.exists():
        return {}

    templates = {}
    for doc in PROFILE_DOCS:
        path = role_dir / doc
        if path.exists():
            templates[doc] = path.read_text(encoding="utf-8")
    return templates


def generate_profile_docs(
    profile_dir: Path,
    role: str,
    variables: dict[str, Any],
    *,
    overwrite: bool = False,
    include_playbook: bool = True,
) -> list[str]:
    """Generate documentation files for a profile from role templates.

    Parameters
    ----------
    profile_dir : Path
        The profile's directory (e.g. ~/.hermes/profiles/pm-backend/).
    role : str
        The profile's role (ceo, department_head, project_manager, specialist).
    variables : dict
        Template variables for substitution.
    overwrite : bool
        If True, overwrite existing files. If False, skip files that exist.
    include_playbook : bool
        If True, copy PLAYBOOK.md into the profile dir.

    Returns
    -------
    list[str]
        Names of files that were written.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    templates = get_role_templates(role)
    written = []

    # Copy global PLAYBOOK.md
    if include_playbook and PLAYBOOK_PATH.exists():
        playbook_dest = profile_dir / "PLAYBOOK.md"
        if overwrite or not playbook_dest.exists():
            shutil.copy2(PLAYBOOK_PATH, playbook_dest)
            written.append("PLAYBOOK.md")

    # Render and write role-specific templates
    for filename, content in templates.items():
        dest = profile_dir / filename
        if not overwrite and dest.exists():
            continue
        rendered = render_template(content, variables)
        dest.write_text(rendered, encoding="utf-8")
        written.append(filename)

    return written


def build_variables(
    profile_name: str,
    display_name: str = "",
    role: str = "",
    parent_profile: str = "",
    department: str = "",
    description: str = "",
) -> dict[str, str]:
    """Build the template variables dict from profile attributes."""
    return {
        "profile_name": profile_name,
        "display_name": display_name or profile_name,
        "role": role,
        "parent_profile": parent_profile or "none",
        "department": department or "general",
        "description": description or "",
    }
