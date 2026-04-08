"""ClaudeCodeAdapter — top-level facade for the Claude Code integration.

Combines ClaudeCodeProfileAdapter, ClaudeCodeMessageBridge, and
ClaudeCodeMemoryAdapter into a single entry point. This is the primary
interface for teams integrating the hierarchy system with Claude Code.

Usage
-----
::

    from core.registry.profile_registry import ProfileRegistry
    from integrations.claude_code import ClaudeCodeAdapter, ClaudeCodeConfig

    # 1. Build a config (or use ClaudeCodeConfig.from_env())
    config = ClaudeCodeConfig(
        projects_dir=Path("~/.claude/projects").expanduser(),
        db_base_dir=Path("~/.hermes/hierarchy").expanduser(),
    )

    # 2. Build the adapter
    registry = ProfileRegistry("~/.hermes/hierarchy/registry.db")
    adapter = ClaudeCodeAdapter(registry=registry, config=config)

    # 3. Prepare a Claude Code session for "pm-hier-arch"
    report = adapter.prepare_session("pm-hier-arch", message_bus=bus)
    print(report)

    # 4. Sync all profiles at once
    adapter.sync_all_profiles()

Stdlib only — no external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.registry.profile_registry import ProfileRegistry
from integrations.claude_code.config import ClaudeCodeConfig
from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter
from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge, ExportReport
from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter


@dataclass
class SessionReport:
    """Result of adapter.prepare_session().

    Attributes
    ----------
    profile_name : str
        The profile for which the session was prepared.
    claude_md_path : Path | None
        Path to the generated CLAUDE.md file, or None if generation failed.
    exported_messages : int
        Number of IPC messages exported to task files.
    memory_entries_embedded : int
        Number of memory entries embedded in CLAUDE.md.
    errors : list[str]
        Any errors encountered during preparation.
    """

    profile_name: str
    claude_md_path: Optional[Path] = None
    exported_messages: int = 0
    memory_entries_embedded: int = 0
    errors: List[str] = field(default_factory=list)

    def is_ok(self) -> bool:
        """True if no errors occurred."""
        return len(self.errors) == 0

    def __str__(self) -> str:
        status = "✅" if self.is_ok() else "⚠️"
        lines = [
            f"{status} Session prepared for '{self.profile_name}'",
            f"   CLAUDE.md: {self.claude_md_path}",
            f"   Messages exported: {self.exported_messages}",
            f"   Memory entries: {self.memory_entries_embedded}",
        ]
        if self.errors:
            lines.append(f"   Errors: {', '.join(self.errors)}")
        return "\n".join(lines)


class ClaudeCodeAdapter:
    """Top-level facade for Claude Code ↔ hierarchy integration.

    Parameters
    ----------
    registry : ProfileRegistry
        The hierarchy profile registry.
    config : ClaudeCodeConfig
        Integration configuration.
    """

    def __init__(
        self,
        registry: ProfileRegistry,
        config: Optional[ClaudeCodeConfig] = None,
    ) -> None:
        self._registry = registry
        self._config = config or ClaudeCodeConfig()

        self.profile_adapter = ClaudeCodeProfileAdapter(registry, self._config)
        self.message_bridge = ClaudeCodeMessageBridge(self._config)
        self.memory_adapter = ClaudeCodeMemoryAdapter(self._config)

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def prepare_session(
        self,
        profile_name: str,
        *,
        message_bus: Optional[Any] = None,
        memory_store: Optional[Any] = None,
    ) -> SessionReport:
        """Prepare a complete Claude Code session for a profile.

        Steps:
        1. Load memory entries (if memory_store provided)
        2. Poll pending IPC messages (if message_bus provided)
        3. Export pending messages to task files
        4. Generate and write CLAUDE.md with full context

        Parameters
        ----------
        profile_name : str
            The profile to prepare.
        message_bus : MessageBus, optional
            If provided, pending messages are fetched and exported.
        memory_store : MemoryStore, optional
            If provided, memory entries are embedded in CLAUDE.md.

        Returns
        -------
        SessionReport
            Summary of what was prepared.
        """
        report = SessionReport(profile_name=profile_name)

        # 1. Load memory entries
        memory_entries: List[Any] = []
        if memory_store is not None:
            try:
                memory_entries = memory_store.list_entries(
                    limit=self._config.max_memory_entries
                )
                report.memory_entries_embedded = len(memory_entries)
            except Exception as e:
                report.errors.append(f"Memory load failed: {e}")

        # 2. Poll + export pending messages
        pending_messages: List[Any] = []
        if message_bus is not None:
            try:
                pending_messages = message_bus.poll(
                    profile_name,
                    limit=self._config.max_pending_messages,
                )
                if pending_messages:
                    export_report = self.message_bridge.export_pending_messages(
                        profile_name, pending_messages
                    )
                    report.exported_messages = len(export_report.exported)
                    if export_report.errors:
                        report.errors.extend(export_report.errors)
            except Exception as e:
                report.errors.append(f"Message export failed: {e}")

        # 3. Generate and write CLAUDE.md
        try:
            claude_md_path = self.profile_adapter.write_claude_md(
                profile_name,
                memory_entries=memory_entries if memory_entries else None,
                pending_messages=pending_messages if pending_messages else None,
            )
            report.claude_md_path = claude_md_path
        except Exception as e:
            report.errors.append(f"CLAUDE.md generation failed: {e}")

        return report

    def sync_all_profiles(
        self,
        *,
        memory_stores: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, SessionReport]:
        """Prepare sessions for all registered profiles.

        Parameters
        ----------
        memory_stores : dict, optional
            Mapping of profile_name → MemoryStore.

        Returns
        -------
        dict
            Mapping of profile_name → SessionReport.
        """
        results: Dict[str, SessionReport] = {}
        profiles = self._registry.list_profiles()

        for profile in profiles:
            name = profile.profile_name
            store = (memory_stores or {}).get(name)
            results[name] = self.prepare_session(name, memory_store=store)

        return results

    def import_results(
        self,
        profile_name: str,
        message_bus: Any,
    ) -> Any:
        """Import completed task results back into the IPC bus.

        Delegates to ClaudeCodeMessageBridge.import_results.

        Parameters
        ----------
        profile_name : str
            Profile whose results to import.
        message_bus : MessageBus
            The IPC bus to send responses into.

        Returns
        -------
        ImportReport
            Summary of imported messages.
        """
        return self.message_bridge.import_results(profile_name, message_bus)

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> ClaudeCodeConfig:
        """The integration configuration."""
        return self._config

    @property
    def registry(self) -> ProfileRegistry:
        """The profile registry."""
        return self._registry
