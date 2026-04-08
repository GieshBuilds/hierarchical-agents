"""Integration layer — delegation chains, orchestration, and cross-profile coordination."""

from core.integration.chain_store import ChainStore
from core.integration.delegation import (
    ChainStatus,
    DelegationChain,
    DelegationHop,
    HopStatus,
)
from core.integration.exceptions import (
    ChainAlreadyComplete,
    ChainNotFound,
    CircularDelegation,
    DelegationTimeout,
    IntegrationError,
    InvalidDelegation,
)
from core.integration.orchestrator import ChainOrchestrator
from core.integration.result_propagation import ResultCollector

__all__ = [
    # Models
    "DelegationChain",
    "DelegationHop",
    "ChainStatus",
    "HopStatus",
    # Persistence
    "ChainStore",
    # Orchestration
    "ChainOrchestrator",
    "ResultCollector",
    # Exceptions
    "IntegrationError",
    "ChainNotFound",
    "InvalidDelegation",
    "ChainAlreadyComplete",
    "DelegationTimeout",
    "CircularDelegation",
]
