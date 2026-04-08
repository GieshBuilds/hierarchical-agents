"""Worker state serialization and deserialization.

Handles saving/loading of subagent worker state to/from disk.
Each subagent's state lives in a directory structure:

    <base_path>/<pm_profile>/<subagent_id>/
        session.json        # Full conversation history
        config.json         # Model, provider, toolsets, system prompt
        metadata.json       # Task goal, parent ref, timestamps, status
        summary.md          # Human-readable summary of work done
        artifacts/          # Directory for files created during work
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.workers.exceptions import SerializationError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_FILE = "session.json"
CONFIG_FILE = "config.json"
METADATA_FILE = "metadata.json"
SUMMARY_FILE = "summary.md"
ARTIFACTS_DIR = "artifacts"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WorkerConfig:
    """Configuration for a worker agent."""

    model: str = ""
    provider: str = ""
    toolsets: list[str] = field(default_factory=list)
    system_prompt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerConfig:
        """Create from a plain dictionary."""
        return cls(
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            toolsets=data.get("toolsets", []),
            system_prompt=data.get("system_prompt", ""),
            extra=data.get("extra", {}),
        )


@dataclass
class WorkerMetadata:
    """Metadata about a worker's task and lifecycle."""

    subagent_id: str
    project_manager: str
    task_goal: str
    status: str
    created_at: str
    updated_at: str
    parent_request_id: str | None = None
    token_cost: int = 0
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerMetadata:
        """Create from a plain dictionary."""
        return cls(
            subagent_id=data.get("subagent_id", ""),
            project_manager=data.get("project_manager", ""),
            task_goal=data.get("task_goal", ""),
            status=data.get("status", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            parent_request_id=data.get("parent_request_id"),
            token_cost=data.get("token_cost", 0),
            artifacts=data.get("artifacts", []),
        )


@dataclass
class WorkerState:
    """Complete state of a worker agent, loaded from disk."""

    metadata: WorkerMetadata
    config: WorkerConfig
    session: list[dict[str, Any]]
    summary: str | None = None
    state_path: Path | None = None


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def get_state_path(
    base_path: str | Path,
    project_manager: str,
    subagent_id: str,
) -> Path:
    """Resolve the filesystem path for a subagent's state directory.

    Parameters
    ----------
    base_path:
        Root directory for all subagent state.
    project_manager:
        PM profile name.
    subagent_id:
        The unique subagent ID.

    Returns
    -------
    Path
        The directory path: ``<base_path>/<pm>/<subagent_id>/``
    """
    return Path(base_path) / project_manager / subagent_id


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def serialize_state(
    base_path: str | Path,
    project_manager: str,
    subagent_id: str,
    *,
    session: list[dict[str, Any]] | None = None,
    config: WorkerConfig | None = None,
    metadata: WorkerMetadata | None = None,
    summary: str | None = None,
) -> Path:
    """Save worker state to disk.

    Creates the state directory and writes whichever components are provided.
    Components that are ``None`` are skipped (not deleted).

    Parameters
    ----------
    base_path:
        Root directory for subagent state.
    project_manager:
        PM profile name.
    subagent_id:
        The unique subagent ID.
    session:
        Conversation history (list of message dicts).
    config:
        Worker configuration.
    metadata:
        Worker metadata.
    summary:
        Human-readable summary of work done.

    Returns
    -------
    Path
        The state directory path.
    """
    state_dir = get_state_path(base_path, project_manager, subagent_id)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Create artifacts directory
    (state_dir / ARTIFACTS_DIR).mkdir(exist_ok=True)

    if session is not None:
        _write_json(state_dir / SESSION_FILE, session)

    if config is not None:
        _write_json(state_dir / CONFIG_FILE, config.to_dict())

    if metadata is not None:
        _write_json(state_dir / METADATA_FILE, metadata.to_dict())

    if summary is not None:
        (state_dir / SUMMARY_FILE).write_text(summary, encoding="utf-8")

    return state_dir


def deserialize_state(
    base_path: str | Path,
    project_manager: str,
    subagent_id: str,
) -> WorkerState:
    """Load complete worker state from disk.

    Parameters
    ----------
    base_path:
        Root directory for subagent state.
    project_manager:
        PM profile name.
    subagent_id:
        The unique subagent ID.

    Returns
    -------
    WorkerState
        The complete loaded state.

    Raises
    ------
    SerializationError
        If the state directory doesn't exist or required files are missing/corrupt.
    """
    state_dir = get_state_path(base_path, project_manager, subagent_id)

    if not state_dir.exists():
        raise SerializationError(
            subagent_id, f"state directory not found: {state_dir}"
        )

    # Load metadata (required)
    metadata = _load_metadata(state_dir, subagent_id)

    # Load config (optional — may not exist for older entries)
    config = _load_config(state_dir, subagent_id)

    # Load session (optional — may not exist yet)
    session = _load_session(state_dir, subagent_id)

    # Load summary (optional)
    summary = _load_summary(state_dir)

    return WorkerState(
        metadata=metadata,
        config=config,
        session=session,
        summary=summary,
        state_path=state_dir,
    )


# ---------------------------------------------------------------------------
# Individual save/load helpers
# ---------------------------------------------------------------------------


def save_session(
    base_path: str | Path,
    project_manager: str,
    subagent_id: str,
    messages: list[dict[str, Any]],
) -> Path:
    """Save or update conversation history.

    Parameters
    ----------
    base_path:
        Root directory for subagent state.
    project_manager:
        PM profile name.
    subagent_id:
        The unique subagent ID.
    messages:
        List of message dictionaries (the conversation history).

    Returns
    -------
    Path
        Path to the session.json file.
    """
    state_dir = get_state_path(base_path, project_manager, subagent_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    filepath = state_dir / SESSION_FILE
    _write_json(filepath, messages)
    return filepath


def load_session(
    base_path: str | Path,
    project_manager: str,
    subagent_id: str,
) -> list[dict[str, Any]]:
    """Load conversation history from disk.

    Returns an empty list if the file doesn't exist.
    """
    state_dir = get_state_path(base_path, project_manager, subagent_id)
    return _load_session(state_dir, subagent_id)


def save_summary(
    base_path: str | Path,
    project_manager: str,
    subagent_id: str,
    summary: str,
) -> Path:
    """Save or update the human-readable summary.

    Returns
    -------
    Path
        Path to the summary.md file.
    """
    state_dir = get_state_path(base_path, project_manager, subagent_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    filepath = state_dir / SUMMARY_FILE
    filepath.write_text(summary, encoding="utf-8")
    return filepath


def load_summary(
    base_path: str | Path,
    project_manager: str,
    subagent_id: str,
) -> str | None:
    """Load the summary from disk. Returns ``None`` if not found."""
    state_dir = get_state_path(base_path, project_manager, subagent_id)
    return _load_summary(state_dir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    """Write data as pretty-printed JSON."""
    path.write_text(
        json.dumps(data, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_json(path: Path, subagent_id: str) -> Any:
    """Read and parse a JSON file."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise SerializationError(
            subagent_id, f"corrupt JSON in {path.name}: {exc}"
        ) from exc


def _load_metadata(state_dir: Path, subagent_id: str) -> WorkerMetadata:
    """Load metadata from disk. Raises if missing."""
    filepath = state_dir / METADATA_FILE
    data = _read_json(filepath, subagent_id)
    if data is None:
        raise SerializationError(
            subagent_id, f"metadata file not found: {filepath}"
        )
    return WorkerMetadata.from_dict(data)


def _load_config(state_dir: Path, subagent_id: str) -> WorkerConfig:
    """Load config from disk. Returns default if missing."""
    filepath = state_dir / CONFIG_FILE
    data = _read_json(filepath, subagent_id)
    if data is None:
        return WorkerConfig()
    return WorkerConfig.from_dict(data)


def _load_session(state_dir: Path, subagent_id: str) -> list[dict[str, Any]]:
    """Load session from disk. Returns empty list if missing."""
    filepath = state_dir / SESSION_FILE
    data = _read_json(filepath, subagent_id)
    if data is None:
        return []
    if not isinstance(data, list):
        raise SerializationError(
            subagent_id, "session.json must contain a JSON array"
        )
    return data


def _load_summary(state_dir: Path) -> str | None:
    """Load summary from disk. Returns None if missing."""
    filepath = state_dir / SUMMARY_FILE
    if not filepath.exists():
        return None
    return filepath.read_text(encoding="utf-8")
