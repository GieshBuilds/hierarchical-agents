"""Abstract interface for framework integration with the worker lifecycle.

Defines the :class:`WorkerManager` protocol that any agent framework
(e.g., Hermes, LangChain, AutoGen) can implement to hook into the
subagent registry and resumable worker system.

This module does NOT implement framework-specific logic — it only
defines the contract.

Example integration sketch::

    class HermesWorkerManager:
        \"\"\"Hermes-specific implementation of WorkerManager.\"\"\"

        def __init__(self, registry, serializer_base_path):
            self.registry = registry
            self.base_path = serializer_base_path

        def spawn_worker(self, goal, context, config=None):
            # 1. Register in SubagentRegistry
            sa = self.registry.register(
                project_manager=self.pm_name,
                task_goal=goal,
            )
            # 2. Create Hermes subagent with delegate_task
            # 3. Wire up on_complete callback
            return sa.subagent_id

        def on_worker_complete(self, subagent_id, result):
            # 1. Serialize state to disk
            # 2. Update registry entry
            self.registry.complete(subagent_id, result.summary)

        def resume_worker(self, subagent_id, new_message=None):
            # 1. Call resume() to get ResumeContext
            # 2. Create new Hermes agent with context
            # 3. Inject conversation history
            # 4. Send new_message (if any)
            pass
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.workers.resume import ResumeContext
from core.workers.serialization import WorkerConfig


@runtime_checkable
class WorkerManager(Protocol):
    """Protocol defining the integration contract for worker lifecycle management.

    Any framework that wants to use the hierarchical agent architecture's
    worker system should implement this protocol.  The methods correspond
    to the key lifecycle events of a worker subagent.
    """

    def spawn_worker(
        self,
        goal: str,
        context: str | None = None,
        config: WorkerConfig | None = None,
    ) -> str:
        """Spawn a new worker subagent for a task.

        This should:
        1. Register the subagent in the SubagentRegistry
        2. Create the actual agent in the framework
        3. Start the agent executing the task

        Parameters
        ----------
        goal:
            Description of what the worker should accomplish.
        context:
            Optional context to provide to the worker (project knowledge,
            relevant files, etc.).
        config:
            Optional worker configuration (model, tools, etc.).
            If ``None``, use framework defaults.

        Returns
        -------
        str
            The subagent_id of the newly spawned worker.
        """
        ...

    def on_worker_complete(
        self,
        pm_profile: str,
        subagent_id: str,
        result: str,
        chain: Any = None,
    ) -> None:
        """Handle a worker completing its task and propagate results upward.

        This should:
        1. Update the SubagentRegistry entry with the result summary
        2. If *chain* is provided and a ChainOrchestrator is available,
           call ``chain_orchestrator.propagate_result(chain, result)`` to
           send the result back up the delegation hierarchy automatically.
        3. Clean up any framework-specific resources

        Parameters
        ----------
        pm_profile:
            Profile name of the project manager that owns this worker.
        subagent_id:
            The ID of the completed worker.
        result:
            Plain-text summary of what the worker accomplished.
        chain:
            The :class:`~core.integration.delegation.DelegationChain` this
            worker belongs to.  When provided and a ``chain_orchestrator``
            is configured on the implementation, the result is propagated
            upward through the hierarchy automatically.  Pass ``None`` to
            skip propagation (registry-only update).
        """
        ...

    def on_worker_error(
        self,
        subagent_id: str,
        error: Exception,
    ) -> None:
        """Handle a worker encountering an error.

        This should:
        1. Serialize the worker's current state (for potential debugging/resume)
        2. Update the SubagentRegistry entry
        3. Optionally escalate the error

        Parameters
        ----------
        subagent_id:
            The ID of the errored worker.
        error:
            The exception that occurred.
        """
        ...

    def resume_worker(
        self,
        subagent_id: str,
        new_message: str | None = None,
    ) -> ResumeContext:
        """Resume a sleeping worker subagent.

        This should:
        1. Call :func:`~core.workers.resume.resume` to get the ResumeContext
        2. Create a new agent instance in the framework
        3. Inject the conversation history
        4. Optionally send a new message to continue work

        Parameters
        ----------
        subagent_id:
            The ID of the worker to resume.
        new_message:
            Optional new message to send after resumption.

        Returns
        -------
        ResumeContext
            The loaded state used for reconstruction.
        """
        ...


@runtime_checkable
class WorkerResult(Protocol):
    """Protocol for worker execution results.

    Frameworks provide their own result type that satisfies this protocol.
    """

    @property
    def summary(self) -> str:
        """Human-readable summary of what the worker accomplished."""
        ...

    @property
    def artifacts(self) -> list[str]:
        """List of file paths created or modified by the worker."""
        ...

    @property
    def token_cost(self) -> int:
        """Total tokens consumed during execution."""
        ...

    @property
    def session_history(self) -> list[dict[str, Any]]:
        """Full conversation history from the worker's execution."""
        ...
