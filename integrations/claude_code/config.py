"""Configuration for the Claude Code integration adapter.

Reads settings from environment variables or plain dicts.
All paths have sensible defaults so zero configuration is needed
for the most common setup.

Stdlib only — no external dependencies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_projects_dir() -> Path:
    """~/.claude/projects/ — where Claude Code stores project contexts."""
    return Path.home() / ".claude" / "projects"


def _default_tasks_dir() -> Path:
    """~/.claude/tasks/ — where Claude Code stores task files."""
    return Path.home() / ".claude" / "tasks"


def _default_db_base_dir() -> Path:
    """~/.hermes/hierarchy/ — hierarchy SQLite databases."""
    return Path.home() / ".hermes" / "hierarchy"


def _default_profiles_dir() -> Path:
    """~/.hermes/profiles/ — Hermes profile directories."""
    return Path.home() / ".hermes" / "profiles"


@dataclass
class ClaudeCodeConfig:
    """Configuration for the Claude Code integration layer.

    Attributes
    ----------
    projects_dir : Path
        Root directory where Claude Code project contexts are stored.
        Each project gets a subdirectory with a CLAUDE.md file.
    tasks_dir : Path
        Directory where Claude Code task JSON files are written/read.
        Used by ClaudeCodeMessageBridge for IPC-to-task translation.
    db_base_dir : Path
        Base directory for hierarchy SQLite databases
        (registry.db, ipc.db, memory/).
    profiles_dir : Path
        Hermes profiles directory (source of SOUL.md files).
    include_memory_in_claude_md : bool
        Whether to embed recent memory entries in generated CLAUDE.md files.
        Default True.
    max_memory_entries : int
        Maximum number of memory entries to embed in CLAUDE.md.
        Default 10.
    include_pending_messages : bool
        Whether to embed pending IPC messages in CLAUDE.md.
        Default True.
    max_pending_messages : int
        Maximum number of IPC messages to embed in CLAUDE.md.
        Default 5.
    """

    projects_dir: Path = field(default_factory=_default_projects_dir)
    tasks_dir: Path = field(default_factory=_default_tasks_dir)
    db_base_dir: Path = field(default_factory=_default_db_base_dir)
    profiles_dir: Path = field(default_factory=_default_profiles_dir)
    include_memory_in_claude_md: bool = True
    max_memory_entries: int = 10
    include_pending_messages: bool = True
    max_pending_messages: int = 5

    @classmethod
    def from_env(cls) -> "ClaudeCodeConfig":
        """Create a config from ``CLAUDE_CODE_*`` and ``HERMES_*`` env vars.

        Supported variables:

        - ``CLAUDE_CODE_PROJECTS_DIR``
        - ``CLAUDE_CODE_TASKS_DIR``
        - ``HERMES_DB_BASE_DIR``
        - ``HERMES_PROFILES_DIR``
        - ``CLAUDE_CODE_INCLUDE_MEMORY`` (true/false)
        - ``CLAUDE_CODE_MAX_MEMORY_ENTRIES`` (integer)
        - ``CLAUDE_CODE_INCLUDE_MESSAGES`` (true/false)
        - ``CLAUDE_CODE_MAX_MESSAGES`` (integer)
        """
        kwargs: dict = {}

        if v := os.environ.get("CLAUDE_CODE_PROJECTS_DIR"):
            kwargs["projects_dir"] = Path(v)
        if v := os.environ.get("CLAUDE_CODE_TASKS_DIR"):
            kwargs["tasks_dir"] = Path(v)
        if v := os.environ.get("HERMES_DB_BASE_DIR"):
            kwargs["db_base_dir"] = Path(v)
        if v := os.environ.get("HERMES_PROFILES_DIR"):
            kwargs["profiles_dir"] = Path(v)
        if v := os.environ.get("CLAUDE_CODE_INCLUDE_MEMORY"):
            kwargs["include_memory_in_claude_md"] = v.lower() not in ("0", "false", "no")
        if v := os.environ.get("CLAUDE_CODE_MAX_MEMORY_ENTRIES"):
            kwargs["max_memory_entries"] = int(v)
        if v := os.environ.get("CLAUDE_CODE_INCLUDE_MESSAGES"):
            kwargs["include_pending_messages"] = v.lower() not in ("0", "false", "no")
        if v := os.environ.get("CLAUDE_CODE_MAX_MESSAGES"):
            kwargs["max_pending_messages"] = int(v)

        return cls(**kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> "ClaudeCodeConfig":
        """Create a config from a plain dictionary.

        Keys mirror the attribute names. Path values may be strings.
        """
        kwargs: dict = {}

        for path_key in ("projects_dir", "tasks_dir", "db_base_dir", "profiles_dir"):
            if path_key in data:
                kwargs[path_key] = Path(data[path_key])

        for bool_key in ("include_memory_in_claude_md", "include_pending_messages"):
            if bool_key in data:
                kwargs[bool_key] = bool(data[bool_key])

        for int_key in ("max_memory_entries", "max_pending_messages"):
            if int_key in data:
                kwargs[int_key] = int(data[int_key])

        return cls(**kwargs)
