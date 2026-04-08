"""Worker agent lifecycle management — subagent registry, serialization, and resume."""

from core.workers.exceptions import (
    InvalidProjectManager,
    InvalidSubagentStatus,
    SerializationError,
    SubagentError,
    SubagentNotFound,
)
from core.workers.interface import WorkerManager, WorkerResult
from core.workers.models import (
    VALID_TRANSITIONS,
    Subagent,
    SubagentStatus,
    generate_subagent_id,
    is_valid_transition,
)
from core.workers.resume import ResumeContext, resume
from core.workers.serialization import (
    WorkerConfig,
    WorkerMetadata,
    WorkerState,
    deserialize_state,
    get_state_path,
    load_session,
    load_summary,
    save_session,
    save_summary,
    serialize_state,
)
from core.workers.subagent_registry import SubagentRegistry

__all__ = [
    # Registry
    "SubagentRegistry",
    # Models
    "Subagent",
    "SubagentStatus",
    "VALID_TRANSITIONS",
    "generate_subagent_id",
    "is_valid_transition",
    # Serialization
    "WorkerConfig",
    "WorkerMetadata",
    "WorkerState",
    "serialize_state",
    "deserialize_state",
    "get_state_path",
    "save_session",
    "load_session",
    "save_summary",
    "load_summary",
    # Resume
    "ResumeContext",
    "resume",
    # Interface
    "WorkerManager",
    "WorkerResult",
    # Exceptions
    "SubagentError",
    "SubagentNotFound",
    "InvalidSubagentStatus",
    "InvalidProjectManager",
    "SerializationError",
]
