"""Result collection and upward propagation through the delegation chain.

Handles sending IPC responses from the bottom of a delegation chain
back up to the originator, hop by hop.
"""
from __future__ import annotations

from typing import Optional

from ..ipc.models import MessagePriority
from ..ipc.message_bus import MessageBus
from ..ipc.protocol import MessageProtocol

from .delegation import DelegationChain, HopStatus

__all__ = ["ResultCollector"]


class ResultCollector:
    """Collects worker results and propagates them up the delegation chain.

    For each completed hop, sends an IPC TASK_RESPONSE from to_profile
    back to from_profile, walking from the bottom of the chain upward.

    Parameters
    ----------
    bus : MessageBus
        The IPC message bus.
    protocol : MessageProtocol
        The IPC protocol layer for sending responses.
    """

    def __init__(self, bus: MessageBus, protocol: MessageProtocol) -> None:
        self._bus = bus
        self._protocol = protocol

    def propagate_up(
        self,
        chain: DelegationChain,
        result: str,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> list[str]:
        """Send result responses back up the chain, hop by hop.

        Starting from the last hop, sends an IPC TASK_RESPONSE from
        each hop's to_profile back to its from_profile.

        Parameters
        ----------
        chain : DelegationChain
            The delegation chain to propagate through.
        result : str
            The result to send upward.
        priority : MessagePriority
            Priority for result messages.

        Returns
        -------
        list[str]
            Message IDs for each response sent.
        """
        if not chain.hops:
            return []

        message_ids: list[str] = []

        # Walk hops in reverse (bottom → top)
        for hop in reversed(chain.hops):
            payload = {
                "result": result,
                "chain_id": chain.chain_id,
                "from_hop": hop.to_profile,
            }

            msg_id = self._protocol.send_response(
                correlation_id=chain.chain_id,
                from_profile=hop.to_profile,
                to_profile=hop.from_profile,
                payload=payload,
                priority=priority,
            )
            message_ids.append(msg_id)

            # Mark this hop as completed
            hop.mark_completed()

        return message_ids

    def propagate_error(
        self,
        chain: DelegationChain,
        error: str,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> list[str]:
        """Send error responses back up the chain, hop by hop.

        Similar to propagate_up but with error payload. Marks hops as failed.

        Parameters
        ----------
        chain : DelegationChain
            The delegation chain.
        error : str
            Error description.
        priority : MessagePriority
            Priority for error messages.

        Returns
        -------
        list[str]
            Message IDs for each error response sent.
        """
        if not chain.hops:
            return []

        message_ids: list[str] = []

        for hop in reversed(chain.hops):
            payload = {
                "error": error,
                "chain_id": chain.chain_id,
                "from_hop": hop.to_profile,
            }

            msg_id = self._protocol.send_response(
                correlation_id=chain.chain_id,
                from_profile=hop.to_profile,
                to_profile=hop.from_profile,
                payload=payload,
                priority=priority,
            )
            message_ids.append(msg_id)

            # Mark failed hops
            if hop.status not in (HopStatus.COMPLETED, HopStatus.FAILED):
                hop.mark_failed()

        return message_ids

    def collect_worker_result(
        self,
        chain: DelegationChain,
        subagent_id: str,
        result: str,
        auto_propagate: bool = False,
    ) -> bool:
        """Record a worker's result and optionally auto-propagate when all workers done.

        Stores the result string in ``chain.worker_results`` keyed by
        *subagent_id*.  If *auto_propagate* is ``True`` and every worker
        registered in ``chain.workers`` has now reported a result, calls
        :meth:`propagate_up` automatically so the caller does not need to
        poll or trigger propagation manually.

        If the same *subagent_id* is recorded more than once the value
        is overwritten with the latest result.

        Parameters
        ----------
        chain : DelegationChain
            The chain the worker belongs to.
        subagent_id : str
            The worker's subagent ID.
        result : str
            The worker's result summary string.
        auto_propagate : bool
            When ``True``, fire :meth:`propagate_up` once all workers in
            ``chain.workers`` have reported results.  Defaults to ``False``
            for backward compatibility.

        Returns
        -------
        bool
            ``True`` if :meth:`propagate_up` was triggered, ``False``
            otherwise.
        """
        chain.worker_results[subagent_id] = result

        if auto_propagate and chain.workers:
            # Check whether every registered worker has reported a result.
            all_done = all(
                wid in chain.worker_results
                for wid in chain.workers
            )
            if all_done:
                # Aggregate results for upward propagation.
                if len(chain.worker_results) == 1:
                    aggregated = result
                else:
                    parts = [
                        f"Worker {wid}: {res}"
                        for wid, res in chain.worker_results.items()
                    ]
                    aggregated = " | ".join(parts)
                self.propagate_up(chain, aggregated)
                return True

        return False
