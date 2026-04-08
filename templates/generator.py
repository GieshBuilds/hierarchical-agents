"""AI-powered agent doc generation using hermes as the backend.

Calls `hermes chat --query "..."` to generate tailored SOUL.md,
HANDOFF.md, WORKFLOWS.md, TOOLS.md, and CONTEXT.md from a purpose
description.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from templates import PLAYBOOK_PATH, PROFILE_DOCS, ROLES_DIR

logger = logging.getLogger(__name__)

# The meta-prompt that instructs Claude to generate agent docs
META_PROMPT = """You are generating operational documentation for an AI agent in a hierarchical multi-agent system.

<agent_info>
Name: {profile_name}
Display Name: {display_name}
Role: {role}
Parent Profile: {parent_profile}
Department: {department}
Purpose: {purpose}
</agent_info>

<hierarchy_context>
This agent operates in a hierarchy: CEO → Department Heads → Project Managers → Specialists.
Agents communicate via IPC messages (TASK_REQUEST / TASK_RESPONSE) using tools like send_to_profile and check_inbox.
Each agent has a gateway that receives tasks and spawns workers to execute them.
Results automatically flow back up the chain via TASK_RESPONSE messages.
</hierarchy_context>

<playbook>
{playbook_content}
</playbook>

<role_template>
{template_content}
</role_template>

<instructions>
Generate the following document for this agent. Use the role template as a structural guide, but tailor ALL content specifically to this agent's purpose: "{purpose}"

Follow these best practices:
- Be clear and direct. Give specific instructions, not vague guidance.
- Name specific tools and when to use them, with examples relevant to this agent's purpose.
- Include concrete workflows that match what this agent will actually do.
- Write in second person ("You are...", "You should...").
- Keep it actionable — every section should tell the agent what to DO.
- Reference the PLAYBOOK for shared rules rather than repeating them.

Generate ONLY the content for: {doc_name}
Do not include any preamble or explanation outside the document. Start directly with the markdown heading.
</instructions>"""


def _call_hermes(query: str, timeout: int = 120) -> Optional[str]:
    """Call hermes CLI in quiet mode to generate text."""
    try:
        result = subprocess.run(
            ["hermes", "chat", "--quiet", "--query", query],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path.home()),
        )
        if result.returncode == 0 and result.stdout.strip():
            return _clean_output(result.stdout.strip())
        logger.warning("hermes returned code %d: %s", result.returncode, result.stderr[:200])
        return None
    except subprocess.TimeoutExpired:
        logger.warning("hermes timed out after %ds", timeout)
        return None
    except FileNotFoundError:
        logger.error("hermes CLI not found on PATH")
        return None


def _clean_output(text: str) -> str:
    """Strip hermes metadata from quiet-mode output."""
    lines = text.split("\n")
    # Remove trailing session_id line and any blank lines before it
    while lines and (lines[-1].startswith("session_id:") or not lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


def generate_doc(
    doc_name: str,
    profile_name: str,
    display_name: str,
    role: str,
    parent_profile: str,
    department: str,
    purpose: str,
) -> Optional[str]:
    """Generate a single agent doc using AI.

    Parameters
    ----------
    doc_name : str
        Which doc to generate (e.g. "SOUL.md", "HANDOFF.md").
    purpose : str
        Free-text description of what this agent should do.

    Returns
    -------
    str or None
        Generated markdown content, or None on failure.
    """
    # Load playbook
    playbook = ""
    if PLAYBOOK_PATH.exists():
        playbook = PLAYBOOK_PATH.read_text(encoding="utf-8")

    # Load role template for this doc
    template = ""
    template_path = ROLES_DIR / role / doc_name
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")

    prompt = META_PROMPT.format(
        profile_name=profile_name,
        display_name=display_name,
        role=role,
        parent_profile=parent_profile or "none",
        department=department or "general",
        purpose=purpose,
        playbook_content=playbook,
        template_content=template,
        doc_name=doc_name,
    )

    return _call_hermes(prompt)


def generate_all_docs(
    profile_dir: Path,
    profile_name: str,
    display_name: str,
    role: str,
    parent_profile: str,
    department: str,
    purpose: str,
    *,
    overwrite: bool = True,
    docs: list[str] | None = None,
) -> dict[str, bool]:
    """Generate all agent docs for a profile using AI.

    Parameters
    ----------
    profile_dir : Path
        Where to write the generated files.
    purpose : str
        Free-text description of what this agent should do.
    docs : list[str] | None
        Which docs to generate. Defaults to all PROFILE_DOCS.
    overwrite : bool
        Whether to overwrite existing files.

    Returns
    -------
    dict[str, bool]
        Mapping of doc name -> whether it was successfully generated.
    """
    import shutil

    profile_dir.mkdir(parents=True, exist_ok=True)
    target_docs = docs or PROFILE_DOCS
    results = {}

    # Always copy PLAYBOOK.md (it's global, not AI-generated)
    if PLAYBOOK_PATH.exists():
        playbook_dest = profile_dir / "PLAYBOOK.md"
        if overwrite or not playbook_dest.exists():
            shutil.copy2(PLAYBOOK_PATH, playbook_dest)
            results["PLAYBOOK.md"] = True

    for doc_name in target_docs:
        dest = profile_dir / doc_name
        if not overwrite and dest.exists():
            results[doc_name] = True  # Already exists
            continue

        logger.info("Generating %s for %s...", doc_name, profile_name)
        content = generate_doc(
            doc_name=doc_name,
            profile_name=profile_name,
            display_name=display_name,
            role=role,
            parent_profile=parent_profile,
            department=department,
            purpose=purpose,
        )

        if content:
            dest.write_text(content, encoding="utf-8")
            results[doc_name] = True
            logger.info("Generated %s (%d chars)", doc_name, len(content))
        else:
            results[doc_name] = False
            logger.warning("Failed to generate %s", doc_name)

    return results
