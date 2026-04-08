"""Hermes-specific configuration for the integration layer.

Reads settings from environment variables or dictionaries,
providing sensible defaults for all paths and intervals.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_profiles_dir() -> Path:
    return Path.home() / ".hermes" / "profiles"


def _default_workspace_dir() -> Path:
    return Path.home() / ".hermes" / "workspace"


def _default_db_base_dir() -> Path:
    return Path.home() / ".hermes" / "hierarchy"


@dataclass
class HermesConfig:
    """Configuration for the Hermes integration layer.

    Attributes
    ----------
    profiles_dir : Path
        Directory containing Hermes profile subdirectories.
    workspace_dir : Path
        Root workspace directory for worker sessions.
    poll_interval_seconds : float
        How often (in seconds) the IPC listener polls for new messages.
    db_base_dir : Path
        Base directory for hierarchy databases (registry, bus, subagents).
    """

    profiles_dir: Path = field(default_factory=_default_profiles_dir)
    workspace_dir: Path = field(default_factory=_default_workspace_dir)
    poll_interval_seconds: float = 2.0
    db_base_dir: Path = field(default_factory=_default_db_base_dir)

    @classmethod
    def from_env(cls) -> HermesConfig:
        """Create a config by reading ``HERMES_*`` environment variables.

        Supported variables:

        - ``HERMES_PROFILES_DIR``
        - ``HERMES_WORKSPACE_DIR``
        - ``HERMES_POLL_INTERVAL``
        - ``HERMES_DB_BASE_DIR``

        Missing variables fall back to defaults.
        """
        kwargs: dict = {}

        profiles = os.environ.get("HERMES_PROFILES_DIR")
        if profiles is not None:
            kwargs["profiles_dir"] = Path(profiles)

        workspace = os.environ.get("HERMES_WORKSPACE_DIR")
        if workspace is not None:
            kwargs["workspace_dir"] = Path(workspace)

        interval = os.environ.get("HERMES_POLL_INTERVAL")
        if interval is not None:
            kwargs["poll_interval_seconds"] = float(interval)

        db_base = os.environ.get("HERMES_DB_BASE_DIR")
        if db_base is not None:
            kwargs["db_base_dir"] = Path(db_base)

        return cls(**kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> HermesConfig:
        """Create a config from a plain dictionary.

        Keys mirror the attribute names.  Path values may be strings.
        """
        kwargs: dict = {}

        if "profiles_dir" in data:
            kwargs["profiles_dir"] = Path(data["profiles_dir"])
        if "workspace_dir" in data:
            kwargs["workspace_dir"] = Path(data["workspace_dir"])
        if "poll_interval_seconds" in data:
            kwargs["poll_interval_seconds"] = float(data["poll_interval_seconds"])
        if "db_base_dir" in data:
            kwargs["db_base_dir"] = Path(data["db_base_dir"])

        return cls(**kwargs)
