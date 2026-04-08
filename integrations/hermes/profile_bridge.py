"""ProfileBridge — syncs Hermes profile directories to the ProfileRegistry.

Scans the Hermes ``profiles_dir`` for subdirectories and synchronizes
them into the core ProfileRegistry, optionally guessing roles from
SOUL.md files.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.registry.profile_registry import ProfileRegistry
from core.registry.exceptions import DuplicateProfile
from integrations.hermes.config import HermesConfig


# Keyword → role mapping for role_from_soul().
_ROLE_KEYWORDS: dict[str, str] = {
    "ceo": "ceo",
    "chief executive": "ceo",
    "department head": "department_head",
    "department_head": "department_head",
    "head of": "department_head",
    "director": "department_head",
    "project manager": "project_manager",
    "project_manager": "project_manager",
    "pm": "project_manager",
}


@dataclass
class SyncReport:
    """Result of a profile-sync operation.

    Attributes
    ----------
    added : list[str]
        Profile names successfully added to the registry.
    skipped : list[str]
        Profile names that already existed (no change).
    errors : list[str]
        Descriptions of any errors encountered.
    """

    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ProfileBridge:
    """Bridge between Hermes profile directories and the ProfileRegistry.

    Parameters
    ----------
    registry : ProfileRegistry
        The core profile registry to sync into.
    config : HermesConfig
        Hermes-specific configuration (provides ``profiles_dir``).
    """

    def __init__(self, registry: ProfileRegistry, config: HermesConfig) -> None:
        self._registry = registry
        self._config = config

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_profiles(self) -> list[str]:
        """Scan ``profiles_dir`` for subdirectories and return their names.

        Only immediate child directories are considered profiles.
        Hidden directories (starting with '.') are excluded.

        Returns
        -------
        list[str]
            Sorted list of discovered profile directory names.
        """
        profiles_dir = self._config.profiles_dir
        if not profiles_dir.is_dir():
            return []

        names: list[str] = []
        for entry in sorted(profiles_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                names.append(entry.name)
        return names

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync_to_registry(self) -> SyncReport:
        """Synchronize discovered profiles into the registry.

        For each discovered profile directory:
        - If a SOUL.md exists, guess the role via :meth:`role_from_soul`.
        - Attempt to create the profile in the registry.
        - Skip profiles that already exist; record errors for failures.

        Returns
        -------
        SyncReport
            Summary of added, skipped, and errored profiles.
        """
        report = SyncReport()
        discovered = self.discover_profiles()

        for name in discovered:
            profile_path = self._config.profiles_dir / name
            soul_path = profile_path / "SOUL.md"

            # Guess role from SOUL.md or default to department_head.
            role = "department_head"
            if soul_path.is_file():
                guessed = self.role_from_soul(soul_path)
                if guessed:
                    role = guessed

            # Determine parent: CEO has no parent; others default to 'hermes'.
            parent: Optional[str] = None if role == "ceo" else "hermes"

            try:
                self._registry.create_profile(
                    name=name,
                    display_name=name,
                    role=role,
                    parent=parent,
                    description=f"Synced from Hermes profile directory",
                )
                report.added.append(name)
            except DuplicateProfile:
                report.skipped.append(name)
            except Exception as exc:
                report.errors.append(f"{name}: {exc}")

        return report

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def get_hermes_profile_path(self, profile_name: str) -> Optional[Path]:
        """Return the filesystem path for a Hermes profile directory.

        Parameters
        ----------
        profile_name : str
            The profile name to look up.

        Returns
        -------
        Path | None
            The path if it exists as a directory, otherwise ``None``.
        """
        candidate = self._config.profiles_dir / profile_name
        return candidate if candidate.is_dir() else None

    # ------------------------------------------------------------------
    # Role guessing
    # ------------------------------------------------------------------

    @staticmethod
    def role_from_soul(soul_path: Path) -> str:
        """Read a SOUL.md file and guess the agent's role from keywords.

        Scans the file content (case-insensitive) for known role keywords.
        Returns the first match found.

        Parameters
        ----------
        soul_path : Path
            Path to the SOUL.md file.

        Returns
        -------
        str
            The guessed role string (e.g. ``'department_head'``),
            or ``'department_head'`` as a fallback.
        """
        try:
            content = soul_path.read_text(encoding="utf-8").lower()
        except OSError:
            return "department_head"

        for keyword, role in _ROLE_KEYWORDS.items():
            if keyword in content:
                return role

        return "department_head"
