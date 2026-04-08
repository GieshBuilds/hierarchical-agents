"""Chain orchestrator for end-to-end task delegation through the hierarchy.

Coordinates the full delegation flow: CEO → Department Head → PM → Worker,
and result propagation back up the chain. Uses only stdlib.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from ..ipc.models import MessagePriority, MessageType
from ..ipc.message_bus import MessageBus
from ..ipc.protocol import MessageProtocol
from ..registry.profile_registry import ProfileRegistry
from ..workers.subagent_registry import SubagentRegistry
from ..memory.context_manager import ContextManager

from .chain_store import ChainStore
from .delegation import ChainStatus, DelegationChain, DelegationHop, HopStatus
from .exceptions import (
    ChainAlreadyComplete,
    ChainNotFound,
    CircularDelegation,
    InvalidDelegation,
)
from .result_propagation import ResultCollector

__all__ = ["ChainOrchestrator"]


class ChainOrchestrator:
    """Coordinates end-to-end task delegation through the hierarchy.

    Manages the lifecycle of delegation chains — from initial task
    assignment through worker execution and result propagation.

    Parameters
    ----------
    registry : ProfileRegistry
        Profile registry for hierarchy lookups.
    bus : MessageBus
        IPC message bus for inter-profile communication.
    worker_registry_factory : callable
        Factory ``(pm_name: str) -> SubagentRegistry`` for per-PM registries.
    context_manager_factory : callable, optional
        Factory ``(profile_name: str) -> ContextManager`` for memory scoping.
    chain_store : ChainStore, optional
        Persistent store for delegation chains. When provided, all chain
        mutations are persisted to SQLite so chains survive process restarts.
        When ``None``, chains are kept in-memory only (legacy behaviour).
    """

    def __init__(
        self,
        registry: ProfileRegistry,
        bus: MessageBus,
        worker_registry_factory: Callable[[str], SubagentRegistry],
        context_manager_factory: Optional[Callable[[str], ContextManager]] = None,
        chain_store: Optional[ChainStore] = None,
    ) -> None:
        self._registry = registry
        self._bus = bus
        self._protocol = MessageProtocol(bus, profile_registry=registry)
        self._worker_factory = worker_registry_factory
        self._context_factory = context_manager_factory
        self._store = chain_store
        self._chains: dict[str, DelegationChain] = {}
        self._lock = threading.Lock()
        self._result_collector = ResultCollector(bus, self._protocol)

    def _persist(self, chain: DelegationChain) -> None:
        """Save chain to persistent store if one is configured."""
        if self._store is not None:
            self._store.save(chain)

    # ------------------------------------------------------------------
    # Chain management
    # ------------------------------------------------------------------

    def create_chain(
        self,
        task: str,
        originator: str,
    ) -> DelegationChain:
        """Create a new delegation chain for a task.

        Parameters
        ----------
        task : str
            Description of the task to delegate.
        originator : str
            Profile name of the task originator.

        Returns
        -------
        DelegationChain
            The newly created chain in PENDING status.
        """
        chain = DelegationChain(task_description=task, originator=originator)
        with self._lock:
            self._chains[chain.chain_id] = chain
        self._persist(chain)
        return chain

    def get_chain(self, chain_id: str) -> DelegationChain:
        """Retrieve a chain by ID.

        Checks the in-memory cache first, then falls back to the
        persistent store. Chains loaded from the store are cached
        in-memory for subsequent access.

        Raises
        ------
        ChainNotFound
            If no chain exists with the given ID.
        """
        with self._lock:
            chain = self._chains.get(chain_id)
        if chain is not None:
            return chain

        # Fall back to persistent store
        if self._store is not None:
            chain = self._store.get(chain_id)  # raises ChainNotFound
            with self._lock:
                self._chains[chain.chain_id] = chain
            return chain

        raise ChainNotFound(chain_id)

    def list_chains(
        self,
        status: Optional[ChainStatus] = None,
        originator: Optional[str] = None,
    ) -> list[DelegationChain]:
        """List chains with optional filters.

        When a persistent store is configured, queries the store
        for a complete picture (including chains from prior processes).
        """
        if self._store is not None:
            return self._store.list(status=status, originator=originator)

        with self._lock:
            chains = list(self._chains.values())
        if status is not None:
            chains = [c for c in chains if c.status == status]
        if originator is not None:
            chains = [c for c in chains if c.originator == originator]
        return chains

    # ------------------------------------------------------------------
    # Delegation
    # ------------------------------------------------------------------

    def delegate(
        self,
        chain: DelegationChain,
        from_profile: str,
        to_profile: str,
        priority: MessagePriority = MessagePriority.NORMAL,
        extra_payload: Optional[dict] = None,
    ) -> DelegationHop:
        """Delegate a task from one profile to another via IPC.

        Validates the delegation is valid (to_profile must be a direct
        report of from_profile), sends an IPC TASK_REQUEST, and records
        the hop in the chain.

        Parameters
        ----------
        chain : DelegationChain
            The chain to add this delegation to.
        from_profile : str
            Profile delegating the task.
        to_profile : str
            Profile receiving the delegation.
        priority : MessagePriority
            IPC message priority.

        Returns
        -------
        DelegationHop
            The recorded delegation hop.

        Raises
        ------
        ChainAlreadyComplete
            If the chain is in a terminal state.
        InvalidDelegation
            If to_profile is not a direct report of from_profile.
        CircularDelegation
            If to_profile already appears in the chain's hops as a target.
        """
        if chain.is_terminal:
            raise ChainAlreadyComplete(
                f"Chain {chain.chain_id} is {chain.status.value}"
            )

        # Validate hierarchy: to_profile must be a subordinate of from_profile
        self._validate_delegation(from_profile, to_profile)

        # Check for circular delegation
        existing_targets = {h.to_profile for h in chain.hops}
        if to_profile in existing_targets:
            raise CircularDelegation(
                f"Profile '{to_profile}' already targeted in chain: "
                + " → ".join(h.to_profile for h in chain.hops)
            )

        # Send IPC message
        payload = {
            "task": chain.task_description,
            "chain_id": chain.chain_id,
            "from": from_profile,
        }
        if extra_payload:
            payload.update(extra_payload)
        message_id, correlation_id = self._protocol.send_request(
            from_profile=from_profile,
            to_profile=to_profile,
            payload=payload,
            priority=priority,
        )

        # Record the hop
        hop = chain.add_hop(
            from_profile=from_profile,
            to_profile=to_profile,
        )
        hop.mark_delegated(message_id)

        # Activate chain if still pending
        if chain.status == ChainStatus.PENDING:
            chain.activate()

        self._persist(chain)
        return hop

    def delegate_down_chain(
        self,
        chain: DelegationChain,
        target_profile: str,
        priority: MessagePriority = MessagePriority.NORMAL,
        extra_payload: Optional[dict] = None,
    ) -> list[DelegationHop]:
        """Delegate a task down the hierarchy to a target profile.

        Resolves the path from the originator to the target and delegates
        through each intermediate profile.

        Parameters
        ----------
        chain : DelegationChain
            The chain to delegate.
        target_profile : str
            The ultimate recipient.
        priority : MessagePriority
            IPC message priority for all hops.

        Returns
        -------
        list[DelegationHop]
            All hops created during delegation.
        """
        # Build the delegation path
        path = self._resolve_delegation_path(chain.originator, target_profile)

        hops = []
        for i in range(len(path) - 1):
            hop = self.delegate(chain, path[i], path[i + 1], priority, extra_payload=extra_payload)
            hops.append(hop)

        return hops

    # ------------------------------------------------------------------
    # Worker management
    # ------------------------------------------------------------------

    def spawn_worker(
        self,
        chain: DelegationChain,
        pm_profile: str,
        task: str,
        toolsets: Optional[list[str]] = None,
    ) -> str:
        """Spawn a worker subagent under a PM.

        Creates a SubagentRegistry record and tracks the worker in the
        chain. Optionally uses ContextManager to build a scoped task brief.

        Parameters
        ----------
        chain : DelegationChain
            The delegation chain this worker belongs to.
        pm_profile : str
            The PM profile spawning the worker.
        task : str
            Worker's task description.
        toolsets : list[str], optional
            Toolsets for the worker.

        Returns
        -------
        str
            The subagent ID.
        """
        if chain.is_terminal:
            raise ChainAlreadyComplete(
                f"Chain {chain.chain_id} is {chain.status.value}"
            )

        worker_reg = self._worker_factory(pm_profile)
        subagent = worker_reg.register(
            project_manager=pm_profile,
            task_goal=task,
            parent_request_id=chain.chain_id,
        )

        chain.add_worker(subagent.subagent_id)

        # Mark the PM's hop as working (find the hop where PM is the target)
        for hop in chain.hops:
            if hop.to_profile == pm_profile and hop.status == HopStatus.DELEGATED:
                hop.mark_working()
                break

        self._persist(chain)
        return subagent.subagent_id

    def complete_worker(
        self,
        chain: DelegationChain,
        pm_profile: str,
        subagent_id: str,
        result: str,
    ) -> bool:
        """Mark a worker as completed, record results, and auto-propagate if all workers done.

        Parameters
        ----------
        chain : DelegationChain
            The chain containing this worker.
        pm_profile : str
            The PM profile that owns the worker.
        subagent_id : str
            The subagent to complete.
        result : str
            The worker's result summary.

        Returns
        -------
        bool
            ``True`` if result propagation was triggered (all workers in the
            chain have now reported), ``False`` otherwise.
        """
        worker_reg = self._worker_factory(pm_profile)
        worker_reg.complete(
            subagent_id=subagent_id,
            result_summary=result,
            project_manager=pm_profile,
        )

        # Bookkeeping: record the result on the chain.
        # auto_propagate=False here — explicit propagation is done by the
        # caller via propagate_result() so IPC messages are only sent once.
        self._result_collector.collect_worker_result(
            chain=chain,
            subagent_id=subagent_id,
            result=result,
            auto_propagate=False,
        )

        self._persist(chain)

        # Report whether all workers have now reported (useful for callers
        # that want to decide whether to call propagate_result immediately).
        all_done = bool(chain.workers) and all(
            wid in chain.worker_results for wid in chain.workers
        )
        return all_done

    def setup_event_driven_propagation(
        self,
        chain: DelegationChain,
        pm_profile: str,
    ) -> None:
        """Wire event-driven result propagation for a chain.

        Registers a completion callback on the SubagentRegistry so that
        when any worker completes, results are automatically propagated
        upward without manual polling or explicit ``complete_worker`` calls.

        Parameters
        ----------
        chain : DelegationChain
            The chain to wire up.
        pm_profile : str
            The PM profile whose :class:`~core.workers.subagent_registry.SubagentRegistry`
            to monitor for completions.
        """
        worker_reg = self._worker_factory(pm_profile)
        result_collector = self._result_collector

        def _on_complete(subagent_id: str, result_summary: str) -> None:
            """Propagate a completed worker's result up the delegation chain."""
            result_collector.collect_worker_result(
                chain=chain,
                subagent_id=subagent_id,
                result=result_summary,
                auto_propagate=True,
            )

        worker_reg.register_completion_callback(_on_complete)

    # ------------------------------------------------------------------
    # Result propagation
    # ------------------------------------------------------------------

    def propagate_result(
        self,
        chain: DelegationChain,
        result: str,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> None:
        """Propagate a result back up the delegation chain.

        Sends IPC TASK_RESPONSE messages from the bottom of the chain
        upward, then marks the chain as completed.

        Parameters
        ----------
        chain : DelegationChain
            The chain to propagate results through.
        result : str
            The result to propagate.
        priority : MessagePriority
            Priority for result messages.
        """
        self._result_collector.propagate_up(
            chain=chain,
            result=result,
            priority=priority,
        )
        chain.complete()
        self._persist(chain)

    def fail_chain(
        self,
        chain: DelegationChain,
        error: str,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> None:
        """Mark a chain as failed and notify upstream.

        Sends error responses back up the chain.

        Parameters
        ----------
        chain : DelegationChain
            The chain that failed.
        error : str
            Error description.
        priority : MessagePriority
            Priority for error messages.
        """
        self._result_collector.propagate_error(
            chain=chain,
            error=error,
            priority=priority,
        )
        chain.fail()
        self._persist(chain)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_delegation(self, from_profile: str, to_profile: str) -> None:
        """Validate that to_profile is a direct report of from_profile.

        All profiles — including root (CEO) — must delegate only to their
        immediate direct reports so that every intermediate hop in the
        hierarchy is traversed and receives a TASK_REQUEST.  Skipping
        intermediaries breaks the return path: TASK_RESPONSE messages
        propagate back up the recorded hops, so if CTO is not in the hop
        list, CTO never receives the result and the delivery chain is broken.
        """
        reports = self._registry.list_reports(from_profile)
        report_names = {r.profile_name for r in reports}
        if to_profile not in report_names:
            raise InvalidDelegation(
                f"'{to_profile}' is not a direct report of '{from_profile}'",
                reason=f"Direct reports of '{from_profile}': {sorted(report_names)}",
            )

    def _resolve_delegation_path(
        self, from_profile: str, to_profile: str
    ) -> list[str]:
        """Resolve the hierarchical path from one profile to another.

        Uses chain-of-command to find the path downward through the
        org chart.  Every intermediate profile in the chain is included
        so that each hop receives a TASK_REQUEST and, critically, so that
        TASK_RESPONSE messages propagate back through every intermediate
        profile on the return path.

        Returns
        -------
        list[str]
            Ordered list of profile names from source to target.

        Raises
        ------
        InvalidDelegation
            If no delegation path exists.
        """
        # Get chain of command for the target (target → ... → CEO)
        chain_of_command = self._registry.get_chain_of_command(to_profile)
        chain_names = [p.profile_name for p in chain_of_command]

        if from_profile not in chain_names:
            raise InvalidDelegation(
                f"No delegation path from '{from_profile}' to '{to_profile}'",
                reason=f"'{from_profile}' is not an ancestor of '{to_profile}'",
            )

        # Extract the subpath from from_profile down to to_profile
        from_idx = chain_names.index(from_profile)
        # chain_of_command goes target → ... → from_profile → ... → CEO
        # We want from_profile → ... → target, so reverse the slice
        path = list(reversed(chain_names[: from_idx + 1]))
        return path

    def _get_role(self, profile_name: str) -> str:
        """Get the role string for a profile."""
        try:
            profile = self._registry.get_profile(profile_name)
            return profile.role if hasattr(profile, 'role') else "unknown"
        except Exception:
            return "unknown"
