"""ClaudeCodeMessageBridge — translate IPC messages to/from Claude Code tasks.

Claude Code stores pending work as task JSON files in ``~/.claude/tasks/``.
This bridge converts IPC messages into task files so Claude Code agents
can pick them up natively, and converts task result files back into IPC
response messages.

File layout
-----------
Each pending IPC message becomes a JSON file::

    ~/.claude/tasks/
    └── <profile_name>/
        ├── pending/
        │   └── <message_id>.json   ← exported from IPC bus
        └── results/
            └── <message_id>.json   ← imported back to IPC bus

Message file schema (pending)
------------------------------
::

    {
      "message_id": "msg-abc123",
      "from_profile": "hermes",
      "to_profile": "pm-hier-arch",
      "message_type": "task_request",
      "priority": "normal",
      "payload": {"message": "Implement feature X"},
      "created_at": "2025-04-01T12:00:00"
    }

Result file schema
------------------
::

    {
      "message_id": "msg-abc123",
      "status": "completed",
      "result": "Feature X implemented. All tests pass.",
      "completed_at": "2025-04-01T12:30:00"
    }

Stdlib only — no external dependencies.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from integrations.claude_code.config import ClaudeCodeConfig


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class ExportReport:
    """Result of a message export operation.

    Attributes
    ----------
    exported : list[str]
        Message IDs successfully exported to task files.
    skipped : list[str]
        Message IDs skipped (file already exists).
    errors : list[str]
        Error descriptions.
    """

    exported: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class ImportReport:
    """Result of a result import operation.

    Attributes
    ----------
    imported : list[str]
        Message IDs whose results were successfully imported to IPC.
    errors : list[str]
        Error descriptions.
    """

    imported: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ClaudeCodeMessageBridge:
    """Translates between IPC MessageBus and Claude Code task files.

    Parameters
    ----------
    config : ClaudeCodeConfig
        Integration configuration.
    """

    def __init__(self, config: ClaudeCodeConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Export: IPC → task files
    # ------------------------------------------------------------------

    def export_pending_messages(
        self,
        profile_name: str,
        messages: List[Any],
    ) -> ExportReport:
        """Export IPC messages to Claude Code task files.

        Parameters
        ----------
        profile_name : str
            Profile whose pending messages to export.
        messages : list
            Pre-fetched list of IPC Message objects (from MessageBus.poll).

        Returns
        -------
        ExportReport
            Summary of exported, skipped, and errored messages.
        """
        report = ExportReport()
        pending_dir = self._get_pending_dir(profile_name)
        pending_dir.mkdir(parents=True, exist_ok=True)

        for msg in messages:
            msg_id = msg.message_id
            task_file = pending_dir / f"{msg_id}.json"

            if task_file.exists():
                report.skipped.append(msg_id)
                continue

            try:
                task_data = self._message_to_task_dict(msg)
                task_file.write_text(
                    json.dumps(task_data, indent=2, default=str),
                    encoding="utf-8",
                )
                report.exported.append(msg_id)
            except Exception as e:
                report.errors.append(f"{msg_id}: {e}")

        return report

    def import_results(
        self,
        profile_name: str,
        message_bus: Any,
    ) -> ImportReport:
        """Import task result files back into the IPC bus as responses.

        Scans the results directory for JSON files, sends each as a
        TASK_RESPONSE message in the bus, then removes the processed file.

        Parameters
        ----------
        profile_name : str
            Profile whose results to import.
        message_bus : MessageBus
            The IPC message bus to send responses into.

        Returns
        -------
        ImportReport
            Summary of imported messages and any errors.
        """
        from core.ipc.models import MessageType, MessagePriority

        report = ImportReport()
        results_dir = self._get_results_dir(profile_name)

        if not results_dir.is_dir():
            return report

        for result_file in sorted(results_dir.glob("*.json")):
            msg_id = result_file.stem
            try:
                result_data = json.loads(result_file.read_text(encoding="utf-8"))

                # Determine who to respond to from the original pending file
                from_profile = result_data.get("from_profile", "unknown")
                payload = {
                    "result": result_data.get("result", ""),
                    "status": result_data.get("status", "completed"),
                    "original_message_id": msg_id,
                }

                message_bus.send(
                    from_profile=profile_name,
                    to_profile=from_profile,
                    message_type=MessageType.TASK_RESPONSE,
                    payload=payload,
                    priority=MessagePriority.NORMAL,
                    correlation_id=msg_id,
                )

                # Clean up the result file
                result_file.unlink()
                report.imported.append(msg_id)

            except Exception as e:
                report.errors.append(f"{msg_id}: {e}")

        return report

    # ------------------------------------------------------------------
    # Task file management
    # ------------------------------------------------------------------

    def list_pending_task_files(self, profile_name: str) -> List[Path]:
        """List all pending task JSON files for a profile.

        Parameters
        ----------
        profile_name : str
            The profile to check.

        Returns
        -------
        list[Path]
            Sorted list of pending task file paths.
        """
        pending_dir = self._get_pending_dir(profile_name)
        if not pending_dir.is_dir():
            return []
        return sorted(pending_dir.glob("*.json"))

    def read_task_file(self, task_file: Path) -> Dict[str, Any]:
        """Read and parse a task JSON file.

        Parameters
        ----------
        task_file : Path
            Path to the task file.

        Returns
        -------
        dict
            Parsed task data.
        """
        return json.loads(task_file.read_text(encoding="utf-8"))

    def write_result_file(
        self,
        profile_name: str,
        message_id: str,
        result: str,
        *,
        from_profile: str = "unknown",
        status: str = "completed",
    ) -> Path:
        """Write a task result file for later import.

        Parameters
        ----------
        profile_name : str
            Profile that completed the task.
        message_id : str
            Original message ID.
        result : str
            Result text to send back.
        from_profile : str
            Who originally sent the message (for routing responses).
        status : str
            Completion status ("completed" or "failed").

        Returns
        -------
        Path
            Path to the written result file.
        """
        results_dir = self._get_results_dir(profile_name)
        results_dir.mkdir(parents=True, exist_ok=True)

        result_data = {
            "message_id": message_id,
            "from_profile": from_profile,
            "status": status,
            "result": result,
            "completed_at": _now_iso(),
        }

        result_file = results_dir / f"{message_id}.json"
        result_file.write_text(
            json.dumps(result_data, indent=2),
            encoding="utf-8",
        )
        return result_file

    def clear_pending_file(self, profile_name: str, message_id: str) -> bool:
        """Remove a processed pending task file.

        Parameters
        ----------
        profile_name : str
            The profile that processed the task.
        message_id : str
            Message ID of the processed task.

        Returns
        -------
        bool
            True if the file was found and deleted; False otherwise.
        """
        task_file = self._get_pending_dir(profile_name) / f"{message_id}.json"
        if task_file.is_file():
            task_file.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def get_profile_task_dir(self, profile_name: str) -> Path:
        """Return the root task directory for a profile."""
        return self._config.tasks_dir / profile_name

    def _get_pending_dir(self, profile_name: str) -> Path:
        return self.get_profile_task_dir(profile_name) / "pending"

    def _get_results_dir(self, profile_name: str) -> Path:
        return self.get_profile_task_dir(profile_name) / "results"

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _message_to_task_dict(msg: Any) -> Dict[str, Any]:
        """Convert an IPC Message object to a task dictionary."""
        return {
            "message_id": msg.message_id,
            "from_profile": msg.from_profile,
            "to_profile": msg.to_profile,
            "message_type": getattr(msg.message_type, "value", str(msg.message_type)),
            "priority": getattr(msg.priority, "value", str(msg.priority)),
            "payload": msg.payload,
            "created_at": msg.created_at.isoformat() if hasattr(msg.created_at, "isoformat") else str(msg.created_at),
        }
