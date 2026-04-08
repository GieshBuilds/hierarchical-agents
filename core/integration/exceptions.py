"""Custom exceptions for integration / delegation operations."""

from __future__ import annotations

__all__ = [
    "IntegrationError",
    "ChainNotFound",
    "InvalidDelegation",
    "ChainAlreadyComplete",
    "DelegationTimeout",
    "CircularDelegation",
]


class IntegrationError(Exception):
    """Base exception for all integration operations."""
    pass


class ChainNotFound(IntegrationError):
    """Raised when a delegation chain is not found."""

    def __init__(self, chain_id: str) -> None:
        self.chain_id = chain_id
        super().__init__(f"Delegation chain not found: {chain_id}")


class InvalidDelegation(IntegrationError):
    """Raised when a delegation request is invalid.

    Parameters
    ----------
    message : str
        Human-readable description of why the delegation is invalid.
    reason : str, optional
        Structured reason (e.g., for programmatic handling).
    """

    def __init__(self, message: str = "", *, reason: str = "") -> None:
        self.reason = reason
        msg = message or "Invalid delegation"
        if reason and reason not in msg:
            msg += f" ({reason})"
        super().__init__(msg)


class ChainAlreadyComplete(IntegrationError):
    """Raised when attempting to modify an already-completed chain."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message or "Delegation chain already complete")


class DelegationTimeout(IntegrationError):
    """Raised when a delegation operation times out."""

    def __init__(self, chain_id: str = "", timeout_seconds: float | None = None) -> None:
        self.chain_id = chain_id
        self.timeout_seconds = timeout_seconds
        msg = f"Delegation timed out"
        if chain_id:
            msg += f": {chain_id}"
        if timeout_seconds is not None:
            msg += f" (after {timeout_seconds}s)"
        super().__init__(msg)


class CircularDelegation(IntegrationError):
    """Raised when a delegation would create a circular dependency."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message or "Circular delegation detected")
