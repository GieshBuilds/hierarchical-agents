"""Resume function for sleeping/paused subagent workers.

Reconstructs a worker's complete context from serialized state so that
a framework (e.g., Hermes) can create a new agent instance that continues
where the previous one left off.

From the design doc:
    "LLMs re-read the entire conversation history on every API call.
     There is no hidden state in a running process that isn't captured
     in the message history. The serialized conversation IS the complete state."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.workers.exceptions import (
    InvalidSubagentStatus,
    SerializationError,
    SubagentNotFound,
)
from core.workers.models import SubagentStatus, is_valid_transition
from core.workers.serialization import (
    WorkerConfig,
    WorkerMetadata,
    deserialize_state,
)


@dataclass
class ResumeContext:
    """Everything needed to reconstruct a paused worker agent.

    A framework receives this and uses it to create a new agent instance
    with the same configuration, injecting the conversation history so
    the LLM sees the full prior context.

    Attributes
    ----------
    subagent_id:
        Unique identifier of the worker being resumed.
    project_manager:
        Profile name of the PM that owns this worker.
    task_goal:
        The original task description.
    session_history:
        Full conversation history (list of message dicts) to inject.
    config:
        Worker configuration (model, provider, toolsets, system prompt).
    metadata:
        Full metadata about the worker's lifecycle.
    artifacts_path:
        Path to the worker's artifacts directory.
    summary:
        Previously generated summary of work done (if any).
    """

    subagent_id: str
    project_manager: str
    task_goal: str
    session_history: list[dict[str, Any]]
    config: WorkerConfig
    metadata: WorkerMetadata
    artifacts_path: Path | None = None
    summary: str | None = None


def resume(
    subagent_id: str,
    *,
    base_path: str | Path,
    project_manager: str,
    registry: object | None = None,
) -> ResumeContext:
    """Load all state for a sleeping subagent and prepare it for resumption.

    Steps (from design doc Section 3.3):
        1. Validate the subagent is in 'sleeping' status
        2. Load session.json (conversation history)
        3. Load config.json (model settings, toolsets)
        4. Load metadata.json (task goal, timestamps, etc.)
        5. Load summary.md (if exists)
        6. Update status to 'running' in the registry (if provided)
        7. Return a ResumeContext with everything needed

    Parameters
    ----------
    subagent_id:
        ID of the subagent to resume.
    base_path:
        Root directory for subagent state files.
    project_manager:
        PM profile name that owns this subagent.
    registry:
        Optional :class:`~core.workers.SubagentRegistry` instance.
        When provided, validates status and updates it to ``running``.
        When ``None``, skips status validation/update (useful for
        standalone deserialization).

    Returns
    -------
    ResumeContext
        Complete context for reconstructing the agent.

    Raises
    ------
    InvalidSubagentStatus
        If the subagent is not in 'sleeping' status.
    SerializationError
        If state files are missing or corrupt.
    SubagentNotFound
        If the subagent doesn't exist in the registry.
    """
    # Step 1: Validate status via registry (if available)
    if registry is not None:
        _validate_and_transition(registry, subagent_id, project_manager)

    # Steps 2-5: Load state from disk
    try:
        state = deserialize_state(base_path, project_manager, subagent_id)
    except SerializationError:
        raise
    except Exception as exc:
        raise SerializationError(
            subagent_id, f"failed to load state: {exc}"
        ) from exc

    # Determine artifacts path
    artifacts_path = None
    if state.state_path is not None:
        ap = state.state_path / "artifacts"
        if ap.exists():
            artifacts_path = ap

    # Step 7: Return the resume context
    return ResumeContext(
        subagent_id=subagent_id,
        project_manager=project_manager,
        task_goal=state.metadata.task_goal,
        session_history=state.session,
        config=state.config,
        metadata=state.metadata,
        artifacts_path=artifacts_path,
        summary=state.summary,
    )


def _validate_and_transition(
    registry: object,
    subagent_id: str,
    project_manager: str,
) -> None:
    """Validate that the subagent can be resumed and update its status.

    Uses duck-typing to interact with the registry.
    """
    # Get current subagent
    try:
        current = registry.get(subagent_id, project_manager=project_manager)  # type: ignore[union-attr]
    except Exception as exc:
        if "not found" in str(exc).lower():
            raise SubagentNotFound(subagent_id) from exc
        raise

    current_status = SubagentStatus(current.status)

    # Validate transition
    if not is_valid_transition(current_status, SubagentStatus.RUNNING):
        raise InvalidSubagentStatus(
            subagent_id,
            current_status.value,
            SubagentStatus.RUNNING.value,
        )

    # Update status to running
    registry.update_status(  # type: ignore[union-attr]
        subagent_id,
        SubagentStatus.RUNNING,
        project_manager=project_manager,
    )
