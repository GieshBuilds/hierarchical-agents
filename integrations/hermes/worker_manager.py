"""Hermes-specific WorkerManager implementation.

Provides :class:`HermesWorkerManager`, a concrete implementation of the
:class:`~core.workers.interface.WorkerManager` protocol that wires together
the :class:`~core.workers.subagent_registry.SubagentRegistry` and
:class:`~core.integration.result_propagation.ResultCollector` for the Hermes
framework.

Also provides :class:`WorkerResultData`, a concrete dataclass satisfying the
:class:`~core.workers.interface.WorkerResult` protocol.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.integration.delegation import DelegationChain
from core.integration.result_propagation import ResultCollector
from core.workers.subagent_registry import SubagentRegistry


__all__ = ["HermesWorkerManager", "WorkerResultData"]


# ---------------------------------------------------------------------------
# WorkerResultData
# ---------------------------------------------------------------------------


@dataclass
class WorkerResultData:
    """Concrete worker result satisfying the WorkerResult protocol.

    Parameters
    ----------
    summary : str
        Human-readable summary of what the worker accomplished.
    artifacts : list[str]
        Paths to files created or modified by the worker.
    token_cost : int
        Total tokens consumed during the worker's execution.
    session_history : list[dict[str, Any]]
        Full conversation history from the worker's session.
    """

    summary: str
    artifacts: list[str] = field(default_factory=list)
    token_cost: int = 0
    session_history: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HermesWorkerManager
# ---------------------------------------------------------------------------


class HermesWorkerManager:
    """Hermes implementation of the WorkerManager protocol.

    Connects worker lifecycle events to the :class:`SubagentRegistry` (for
    persistent state tracking) and the :class:`ResultCollector` (for
    bookkeeping results on the active :class:`DelegationChain`).

    Parameters
    ----------
    registry : SubagentRegistry
        The subagent registry used to record and update worker state.
    result_collector : ResultCollector
        The result collector that stores worker results on the chain.
    chain : DelegationChain
        The delegation chain this manager is operating within.
    pm_profile : str
        Profile name of the project manager that owns the workers.
    """

    def __init__(
        self,
        registry: SubagentRegistry,
        result_collector: ResultCollector,
        chain: DelegationChain,
        pm_profile: str,
    ) -> None:
        self.registry = registry
        self.result_collector = result_collector
        self.chain = chain
        self.pm_profile = pm_profile

    def on_worker_complete(
        self,
        subagent_id: str,
        result: WorkerResultData,
    ) -> None:
        """Handle a worker completing its task.

        Performs two actions in order:

        1. Calls :meth:`SubagentRegistry.complete` to mark the subagent as
           completed in persistent storage and record its result summary.
        2. Calls :meth:`ResultCollector.collect_worker_result` to store the
           result on the active :class:`DelegationChain` for in-memory
           bookkeeping and later IPC propagation.

        Parameters
        ----------
        subagent_id : str
            The ID of the completed worker.
        result : WorkerResultData
            The worker's execution result containing at least a ``summary``.
        """
        self.registry.complete(
            subagent_id,
            result_summary=result.summary,
            project_manager=self.pm_profile,
        )
        self.result_collector.collect_worker_result(
            self.chain,
            subagent_id,
            result.summary,
        )
