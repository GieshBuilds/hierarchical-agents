"""Custom exceptions for the Subagent/Worker Registry."""

from __future__ import annotations


class SubagentError(Exception):
    """Base exception for all subagent-registry errors."""


class SubagentNotFound(SubagentError):
    """Raised when a requested subagent does not exist."""

    def __init__(self, subagent_id: str) -> None:
        self.subagent_id = subagent_id
        super().__init__(f"Subagent not found: '{subagent_id}'")


class InvalidSubagentStatus(SubagentError):
    """Raised when a status transition is invalid."""

    def __init__(
        self,
        subagent_id: str,
        current_status: str,
        target_status: str,
    ) -> None:
        self.subagent_id = subagent_id
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"Invalid status transition for subagent '{subagent_id}': "
            f"'{current_status}' → '{target_status}'"
        )


class InvalidProjectManager(SubagentError):
    """Raised when the specified project manager is invalid."""

    def __init__(self, profile_name: str, reason: str = "") -> None:
        self.profile_name = profile_name
        msg = f"Invalid project manager: '{profile_name}'"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)


class SerializationError(SubagentError):
    """Raised when serialization or deserialization of worker state fails."""

    def __init__(self, subagent_id: str, reason: str = "") -> None:
        self.subagent_id = subagent_id
        msg = f"Serialization error for subagent '{subagent_id}'"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)
