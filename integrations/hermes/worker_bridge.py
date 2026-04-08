"""WorkerBridge — maps delegate-task-style calls to SubagentRegistry operations.

Provides a simplified interface for spawning, completing, and querying
worker subagents, wrapping the core SubagentRegistry.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import io
import logging
import os
import select
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.workers.subagent_registry import SubagentRegistry
from core.workers.interface import WorkerManager
from core.workers.resume import ResumeContext
from core.workers.serialization import WorkerConfig

logger = logging.getLogger(__name__)


class WorkerBridge:
    """Bridge between Hermes delegation logic and the SubagentRegistry.

    Implements the :class:`~core.workers.interface.WorkerManager` protocol so
    it can be used wherever a ``WorkerManager`` is expected.

    Parameters
    ----------
    worker_registry_factory : Callable
        A callable that returns a :class:`SubagentRegistry` instance.
        This allows lazy or per-call construction of registries.
    workspace_dir : Path
        Root workspace directory for worker sessions.
    chain_orchestrator : object, optional
        An optional :class:`~core.integration.orchestrator.ChainOrchestrator`
        instance.  When provided, :meth:`on_worker_complete` will call
        ``chain_orchestrator.propagate_result(chain, result)`` after marking
        the worker as completed.
    pm_profile : str, optional
        Default project manager profile name used by the ``WorkerManager``
        protocol methods (:meth:`spawn_worker`, :meth:`resume_worker`, etc.)
        that do not accept an explicit ``pm_profile`` argument.
    """

    def __init__(
        self,
        worker_registry_factory: Callable[[], SubagentRegistry],
        workspace_dir: Path,
        chain_orchestrator: Optional[Any] = None,
        pm_profile: str = "",
    ) -> None:
        self._factory = worker_registry_factory
        self._workspace_dir = workspace_dir
        self._registry: Optional[SubagentRegistry] = None
        self._chain_orchestrator: Optional[Any] = chain_orchestrator
        self._default_pm_profile: str = pm_profile

    @property
    def registry(self) -> SubagentRegistry:
        """Lazily create and cache the SubagentRegistry."""
        if self._registry is None:
            self._registry = self._factory()
        return self._registry

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_task_goal(
        self,
        task: str,
        toolsets: Optional[List[str]] = None,
        context: Optional[str] = None,
    ) -> str:
        """Build a rich task goal string from parts."""
        goal_parts = [task]
        if toolsets:
            goal_parts.append(f"[tools: {', '.join(toolsets)}]")
        if context:
            goal_parts.append(f"[context: {context}]")
        return " ".join(goal_parts)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def spawn(
        self,
        pm_profile: str,
        task: str,
        toolsets: Optional[List[str]] = None,
        context: Optional[str] = None,
    ) -> str:
        """Spawn a new worker subagent for a project manager.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager that owns this worker.
        task : str
            Human-readable description of the task.
        toolsets : list[str] | None
            Optional list of tool names the worker should use.
            Stored in the task_goal for reference.
        context : str | None
            Optional additional context to include in the task goal.

        Returns
        -------
        str
            The ``subagent_id`` of the newly created worker.
        """
        task_goal = self._build_task_goal(task, toolsets, context)

        subagent = self.registry.register(
            project_manager=pm_profile,
            task_goal=task_goal,
        )

        return subagent.subagent_id

    def spawn_with_chain(
        self,
        pm_profile: str,
        task: str,
        chain: Any,
        toolsets: Optional[List[str]] = None,
        context: Optional[str] = None,
    ) -> str:
        """Spawn a worker and wire up auto-propagation for the given chain.

        Convenience wrapper that calls :meth:`spawn` followed by
        :meth:`setup_auto_propagation`.  Use this instead of calling both
        methods manually when you want event-driven result delivery.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager that owns this worker.
        task : str
            Human-readable description of the task.
        chain : DelegationChain
            The :class:`~core.integration.delegation.DelegationChain` to bind
            this worker to.  Auto-propagation is set up so completions flow
            back up the chain automatically.
        toolsets : list[str] | None
            Optional list of tool names the worker should use.
        context : str | None
            Optional additional context to include in the task goal.

        Returns
        -------
        str
            The ``subagent_id`` of the newly created worker.

        Notes
        -----
        This method calls :meth:`setup_auto_propagation` which registers an
        event-driven callback on the :class:`SubagentRegistry`.  A
        ``chain_orchestrator`` must have been provided at construction time,
        otherwise a warning is logged and auto-propagation is skipped (the
        worker is still spawned successfully).
        """
        subagent_id = self.spawn(
            pm_profile=pm_profile,
            task=task,
            toolsets=toolsets,
            context=context,
        )
        self.setup_auto_propagation(chain)
        return subagent_id

    # ------------------------------------------------------------------
    # WorkerManager Protocol implementation (Fix 4)
    # ------------------------------------------------------------------

    def spawn_worker(
        self,
        goal: str,
        context: Optional[str] = None,
        config: Optional["WorkerConfig"] = None,
    ) -> str:
        """Implement :class:`~core.workers.interface.WorkerManager` ``spawn_worker``.

        Spawns a worker under :attr:`_default_pm_profile`.  Set
        ``pm_profile`` in the constructor to configure which PM owns
        workers created through this protocol method.

        Parameters
        ----------
        goal : str
            Description of the task for the worker.
        context : str | None
            Optional additional context (forwarded to :meth:`spawn`).
        config : WorkerConfig | None
            Unused by this implementation (reserved for future use).

        Returns
        -------
        str
            The ``subagent_id`` of the spawned worker.
        """
        return self.spawn(
            pm_profile=self._default_pm_profile,
            task=goal,
            context=context,
        )

    def on_worker_error(
        self,
        subagent_id: str,
        error: Exception,
    ) -> None:
        """Implement :class:`~core.workers.interface.WorkerManager` ``on_worker_error``.

        Records the error by calling :meth:`fail` using
        :attr:`_default_pm_profile`.

        Parameters
        ----------
        subagent_id : str
            The ID of the errored worker.
        error : Exception
            The exception that occurred.
        """
        self.fail(
            pm_profile=self._default_pm_profile,
            subagent_id=subagent_id,
            error_message=str(error),
        )

    def resume_worker(
        self,
        subagent_id: str,
        new_message: Optional[str] = None,
    ) -> "ResumeContext":
        """Implement :class:`~core.workers.interface.WorkerManager` ``resume_worker``.

        Delegates to :func:`~core.workers.resume.resume` to build the
        :class:`~core.workers.resume.ResumeContext` for the given worker.

        Parameters
        ----------
        subagent_id : str
            The ID of the worker to resume.
        new_message : str | None
            Reserved for future use; not forwarded to the current resume
            implementation.

        Returns
        -------
        ResumeContext
            The loaded state used for reconstruction.
        """
        from core.workers.resume import resume as _resume

        return _resume(
            subagent_id,
            base_path=self._workspace_dir,
            project_manager=self._default_pm_profile,
            registry=self.registry,
        )

    def complete(
        self,
        pm_profile: str,
        subagent_id: str,
        result: str,
    ) -> None:
        """Mark a worker subagent as completed with a result summary.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager that owns this worker.
        subagent_id : str
            The subagent to complete.
        result : str
            Summary of the work accomplished.
        """
        self.registry.complete(
            subagent_id,
            result_summary=result,
            project_manager=pm_profile,
        )

    def on_worker_complete(
        self,
        pm_profile: str,
        subagent_id: str,
        result: str,
        chain: Optional[Any] = None,
    ) -> None:
        """Mark a worker as completed and optionally propagate the result up the chain.

        This is the chain-aware completion hook.  It always calls
        :meth:`complete` to record the result in the SubagentRegistry.
        Additionally, when both *chain* and a ``chain_orchestrator`` were
        supplied at construction time, it calls
        ``chain_orchestrator.propagate_result(chain, result)`` to send the
        result back up the delegation hierarchy.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager that owns this worker.
        subagent_id : str
            The subagent to mark as completed.
        result : str
            Summary of the work accomplished.
        chain : DelegationChain | None, optional
            The :class:`~core.integration.delegation.DelegationChain` that
            this worker belongs to.  If ``None``, or if no
            ``chain_orchestrator`` was supplied at construction time, result
            propagation is skipped and only the registry is updated.
        """
        # Always record the completion in the registry.
        self.complete(pm_profile=pm_profile, subagent_id=subagent_id, result=result)

        # Propagate up the chain only when both chain and orchestrator are set.
        if chain is not None and self._chain_orchestrator is not None:
            self._chain_orchestrator.propagate_result(chain, result)

    # ------------------------------------------------------------------
    # Auto-propagation (Stream C — event-driven result delivery)
    # ------------------------------------------------------------------

    def setup_auto_propagation(self, chain: Any) -> None:
        """Register an event-driven callback so results flow up the chain automatically.

        When a worker completes (via :meth:`complete` or any path that calls
        :meth:`SubagentRegistry.complete`), the result is immediately forwarded
        to ``self._chain_orchestrator.propagate_result(chain, result)`` — no
        manual polling or cron calls required.

        Steps performed:
        1. Ensures the :attr:`registry` has been created (lazy init).
        2. Registers a :meth:`SubagentRegistry.register_completion_callback`
           that closes over *chain* and the orchestrator.

        This method is idempotent in the sense that repeated calls simply add
        additional (equivalent) callbacks; callers should typically call it once
        per chain.

        Parameters
        ----------
        chain : DelegationChain
            The delegation chain to propagate results into.  Captured by
            closure so that all subsequent completions are forwarded to the
            same chain.

        Notes
        -----
        If no ``chain_orchestrator`` was supplied at construction time a
        warning is logged and no callback is registered.
        """
        if self._chain_orchestrator is None:
            logger.warning(
                "setup_auto_propagation called but no chain_orchestrator is set; "
                "results will NOT be propagated automatically."
            )
            return

        orchestrator = self._chain_orchestrator

        def _on_worker_complete(subagent_id: str, result_summary: str) -> None:
            """Propagate a completed worker's result up the delegation chain.

            Parameters
            ----------
            subagent_id:
                ID of the worker that just finished.
            result_summary:
                Result text from the worker.
            """
            logger.debug(
                "Auto-propagating result for subagent_id=%s chain_id=%s",
                subagent_id,
                getattr(chain, "chain_id", "<unknown>"),
            )
            orchestrator.propagate_result(chain, result_summary)

        self.registry.register_completion_callback(_on_worker_complete)

    def get_status(
        self,
        pm_profile: str,
        subagent_id: str,
    ) -> str:
        """Get the current status of a worker subagent.

        Parameters
        ----------
        pm_profile : str
            Profile name of the owning project manager.
        subagent_id : str
            The subagent to query.

        Returns
        -------
        str
            The status string (e.g. ``'running'``, ``'completed'``).
        """
        subagent = self.registry.get(
            subagent_id,
            project_manager=pm_profile,
        )
        return subagent.status

    def list_workers(
        self,
        pm_profile: str,
        status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return all workers for a project manager, optionally filtered by status.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager.
        status_filter : str | None
            If provided, only return workers with this status
            (e.g. ``'running'``, ``'completed'``, ``'sleeping'``, ``'archived'``).

        Returns
        -------
        list[dict]
            List of worker dicts with keys: subagent_id, task_goal, status,
            created_at, updated_at, result_summary.
        """
        workers = self.registry.list(
            project_manager=pm_profile,
            status=status_filter,
        )
        return [
            {
                "subagent_id": w.subagent_id,
                "project_manager": w.project_manager,
                "task_goal": w.task_goal,
                "status": w.status,
                "created_at": w.created_at.isoformat() if isinstance(w.created_at, datetime) else str(w.created_at),
                "updated_at": w.updated_at.isoformat() if isinstance(w.updated_at, datetime) else str(w.updated_at),
                "result_summary": w.result_summary,
            }
            for w in workers
        ]

    def fail(
        self,
        pm_profile: str,
        subagent_id: str,
        error_message: str,
    ) -> None:
        """Mark a worker subagent as completed with an error result.

        This is a convenience wrapper around :meth:`complete` that
        prefixes the result summary with ``[ERROR]``.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager that owns this worker.
        subagent_id : str
            The subagent to mark as failed.
        error_message : str
            Description of the error that caused the failure.
        """
        self.registry.complete(
            subagent_id,
            result_summary=f"[ERROR] {error_message}",
            project_manager=pm_profile,
        )

    def get_dashboard(
        self,
        pm_profile: str,
    ) -> Dict[str, Any]:
        """Return a dashboard summary for a project manager's workers.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager.

        Returns
        -------
        dict
            A dict with:
            - ``counts``: dict mapping status -> count of workers in that status.
            - ``active``: list of worker dicts for running and sleeping workers.
            - ``total``: total number of workers.
        """
        all_workers = self.registry.list(project_manager=pm_profile)

        counts: Dict[str, int] = {}
        active: List[Dict[str, Any]] = []

        for w in all_workers:
            status = w.status
            counts[status] = counts.get(status, 0) + 1

            if status in ("running", "sleeping"):
                active.append({
                    "subagent_id": w.subagent_id,
                    "task_goal": w.task_goal,
                    "status": w.status,
                    "created_at": w.created_at.isoformat() if isinstance(w.created_at, datetime) else str(w.created_at),
                    "updated_at": w.updated_at.isoformat() if isinstance(w.updated_at, datetime) else str(w.updated_at),
                })

        return {
            "counts": counts,
            "active": active,
            "total": len(all_workers),
        }

    # ------------------------------------------------------------------
    # Real Claude / Hermes subprocess invocation
    # ------------------------------------------------------------------

    @staticmethod
    def _find_agent_cli(preferred: str = "hermes") -> Optional[str]:
        """Return the absolute path to the preferred agent CLI.

        Tries *preferred* first, then falls back to the other known CLI.
        Returns ``None`` if neither is found on PATH.
        """
        candidates = [preferred] + (["claude"] if preferred != "claude" else ["hermes"])
        for name in candidates:
            path = shutil.which(name)
            if path:
                return path
        return None

    def _build_agent_cmd(
        self,
        cli_path: str,
        task: str,
        toolsets: Optional[List[str]],
        context: Optional[str],
        subagent_id: str,
        pm_profile: str,
    ) -> List[str]:
        """Build the subprocess command list for invoking an agent worker.

        Supports both the ``hermes`` CLI and the ``claude`` CLI (Claude Code)
        transparently.

        Parameters
        ----------
        cli_path : str
            Absolute path to the CLI binary.
        task : str
            The task description to pass as the prompt.
        toolsets : list[str] | None
            Toolsets to activate (hermes only; ignored for claude).
        context : str | None
            Additional context prepended to the prompt.
        subagent_id : str
            Worker ID injected into the prompt for traceability.
        pm_profile : str
            PM profile name injected into the prompt for traceability.

        Returns
        -------
        list[str]
            argv-style command list ready for :func:`subprocess.run`.
        """
        cli_name = Path(cli_path).name  # "claude" or "hermes"

        # Build the full prompt text
        prompt_parts = [
            f"[Worker ID: {subagent_id}]",
            f"[PM: {pm_profile}]",
        ]
        if context:
            prompt_parts.append(f"[Context: {context}]")
        prompt_parts.append(task)
        prompt = "\n".join(prompt_parts)

        if cli_name == "hermes":
            cmd = [cli_path]
            # Run as the profile so it gets its own SOUL.md, model,
            # skills, and toolsets — a real profile session, not a subagent.
            if pm_profile:
                cmd += ["-p", pm_profile]
            cmd += ["chat", "--quiet", "--query", prompt]
            if toolsets:
                cmd += ["--toolsets", ",".join(toolsets)]
        else:
            # claude CLI: non-interactive print mode
            cmd = [cli_path, "--print", prompt]

        return cmd

    def invoke_agent(
        self,
        task: str,
        toolsets: Optional[List[str]] = None,
        context: Optional[str] = None,
        subagent_id: str = "",
        pm_profile: str = "",
        timeout: int = 900,
        cli: str = "hermes",
    ) -> str:
        """Invoke a real agent via subprocess and return output.

        This is the low-level worker execution primitive used by
        :meth:`spawn_and_track`.  It blocks until the subprocess exits
        or goes idle for *timeout* seconds. Active agents that keep
        producing output are never interrupted.

        Parameters
        ----------
        task : str
            The task/prompt to pass to the agent.
        toolsets : list[str] | None
            Tool sets to activate (hermes-cli only).
        context : str | None
            Additional context prepended to the prompt.
        subagent_id : str
            Worker registry ID (injected into prompt for traceability).
        pm_profile : str
            PM profile name (injected into prompt for traceability).
        timeout : int
            Idle timeout — seconds of no output before the agent is
            considered stuck and killed. Default: 900 (15 min).
        cli : str
            Preferred CLI binary name — ``"claude"`` (default) or ``"hermes"``.

        Returns
        -------
        str
            Captured stdout from the agent subprocess.

        Raises
        ------
        RuntimeError
            If neither ``hermes`` nor ``claude`` CLI is found on PATH, or if
            the subprocess exits with a non-zero return code.
        subprocess.TimeoutExpired
            If the agent does not finish within *timeout* seconds.
        """
        cli_path = self._find_agent_cli(cli)
        if cli_path is None:
            raise RuntimeError(
                "No supported agent CLI found on PATH. "
                "Install 'hermes' or 'claude' to enable real agent invocations."
            )

        cmd = self._build_agent_cmd(
            cli_path=cli_path,
            task=task,
            toolsets=toolsets,
            context=context,
            subagent_id=subagent_id,
            pm_profile=pm_profile,
        )

        logger.info(
            "Invoking worker subprocess: subagent_id=%s pm=%s cli=%s",
            subagent_id,
            pm_profile,
            cli_path,
        )

        # Use a temp dir as the working directory so workers don't share CWD
        work_dir = self._workspace_dir / subagent_id if subagent_id else self._workspace_dir
        work_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()

        # Use Popen with an idle watchdog instead of a hard timeout.
        # The agent is only killed if it stops producing output for
        # `timeout` seconds — active agents are never interrupted.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            cwd=str(work_dir),
            env=env,
        )

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        last_activity = time.time()
        idle_timeout = timeout  # seconds of no output before killing

        try:
            while proc.poll() is None:
                # Wait for output with a short poll interval
                ready, _, _ = select.select(
                    [proc.stdout, proc.stderr], [], [], 5.0
                )
                for stream in ready:
                    chunk = stream.read(4096)
                    if chunk:
                        last_activity = time.time()
                        if stream is proc.stdout:
                            stdout_buf.write(chunk)
                        else:
                            stderr_buf.write(chunk)

                # Check idle timeout
                idle_seconds = time.time() - last_activity
                if idle_seconds > idle_timeout:
                    proc.kill()
                    raise subprocess.TimeoutExpired(
                        cmd, idle_timeout,
                        output=stdout_buf.getvalue(),
                        stderr=stderr_buf.getvalue(),
                    )

            # Process finished — drain remaining output
            remaining_out, remaining_err = proc.communicate(timeout=10)
            if remaining_out:
                stdout_buf.write(remaining_out)
            if remaining_err:
                stderr_buf.write(remaining_err)

        except subprocess.TimeoutExpired:
            proc.kill()
            raise

        stdout_result = stdout_buf.getvalue()
        stderr_result = stderr_buf.getvalue()

        if proc.returncode != 0:
            stderr_snippet = (stderr_result or "")[:500]
            raise RuntimeError(
                f"Worker subprocess exited with code {proc.returncode}. "
                f"stderr: {stderr_snippet}"
            )

        # Strip hermes metadata (session_id line) from quiet-mode output
        result_lines = stdout_result.strip().splitlines()
        while result_lines and (
            result_lines[-1].startswith("session_id:")
            or not result_lines[-1].strip()
        ):
            result_lines.pop()
        return "\n".join(result_lines).strip()

    def spawn_and_track(
        self,
        pm_profile: str,
        task: str,
        toolsets: Optional[List[str]] = None,
        context: Optional[str] = None,
        delegate_fn: Optional[Callable] = None,
        use_real_agent: bool = False,
        agent_cli: str = "claude",
        agent_timeout: int = 900,
    ) -> Tuple[str, Any]:
        """Spawn a worker, invoke a real Claude agent or delegate function, and track it.

        Registers the worker in SubagentRegistry via :meth:`spawn`, then runs
        the actual work in one of three modes (in priority order):

        1. **delegate_fn** — if provided, called with ``(task, toolsets, context)``
           (legacy / test mode, no real subprocess).
        2. **use_real_agent=True** — invokes a real ``claude`` or ``hermes``
           subprocess via :meth:`invoke_agent` and captures its output.
        3. **No-op** — worker is registered as ``running`` and left for an
           external process to complete (e.g. a cron job or webhook).

        On success the result is stored via :meth:`complete`.
        On failure the worker is marked via :meth:`fail`.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager.
        task : str
            Human-readable description of the task.
        toolsets : list[str] | None
            Optional list of tool names the worker should use.
        context : str | None
            Optional additional context.
        delegate_fn : Callable | None
            If provided, called with ``(task, toolsets, context)``.
            On success the result is stored as the worker's result_summary.
            On failure the worker is marked as failed.
        use_real_agent : bool
            If ``True`` and *delegate_fn* is ``None``, invoke a real Claude
            subprocess via :meth:`invoke_agent`. Default: ``False``.
        agent_cli : str
            CLI binary to prefer for real invocations — ``"hermes"`` (default)
            or ``"claude"``.
        agent_timeout : int
            Idle timeout in seconds. The agent is only killed if it stops
            producing output for this many seconds. Active agents are
            never interrupted. Default: 900.

        Returns
        -------
        tuple[str, Any]
            A ``(subagent_id, delegate_result)`` tuple.
            ``delegate_result`` is ``None`` if neither *delegate_fn* nor
            *use_real_agent* produced output.
        """
        subagent_id = self.spawn(pm_profile, task, toolsets, context)
        delegate_result = None

        if delegate_fn is not None:
            # --- Mode 1: caller-supplied delegate function (legacy / test) ---
            try:
                delegate_result = delegate_fn(task, toolsets, context)
                result_str = str(delegate_result) if delegate_result is not None else "Completed successfully"
                self.complete(pm_profile, subagent_id, result_str)
            except Exception as exc:
                self.fail(pm_profile, subagent_id, str(exc))
                delegate_result = None

        elif use_real_agent:
            # --- Mode 2: real Claude / Hermes subprocess invocation ---
            try:
                output = self.invoke_agent(
                    task=task,
                    toolsets=toolsets,
                    context=context,
                    subagent_id=subagent_id,
                    pm_profile=pm_profile,
                    timeout=agent_timeout,
                    cli=agent_cli,
                )
                delegate_result = output
                result_str = output if output else "Completed successfully (no output)"
                self.complete(pm_profile, subagent_id, result_str)
            except subprocess.TimeoutExpired:
                msg = f"Worker idle for {agent_timeout}s with no output — killed"
                logger.error("subagent_id=%s %s", subagent_id, msg)
                self.fail(pm_profile, subagent_id, msg)
            except Exception as exc:
                logger.error("subagent_id=%s invocation error: %s", subagent_id, exc)
                self.fail(pm_profile, subagent_id, str(exc))

        # --- Mode 3: no-op — worker stays 'running' for external completion ---

        return (subagent_id, delegate_result)

    def check_timeouts(
        self,
        pm_profile: str,
        max_hours: float = 24,
    ) -> List[Dict[str, Any]]:
        """Return workers that have been running longer than *max_hours*.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager.
        max_hours : float
            Maximum allowed runtime in hours. Workers running longer
            than this are considered timed-out.

        Returns
        -------
        list[dict]
            List of worker dicts for timed-out workers, each including
            an ``hours_running`` field with the elapsed time.
        """
        now = datetime.now(timezone.utc)
        running = self.registry.list(project_manager=pm_profile, status="running")

        timed_out: List[Dict[str, Any]] = []
        for w in running:
            created = w.created_at
            if not isinstance(created, datetime):
                try:
                    created = datetime.fromisoformat(str(created))
                except (ValueError, TypeError):
                    continue

            # Ensure timezone-aware comparison
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            elapsed_hours = (now - created).total_seconds() / 3600.0
            if elapsed_hours > max_hours:
                timed_out.append({
                    "subagent_id": w.subagent_id,
                    "task_goal": w.task_goal,
                    "status": w.status,
                    "created_at": created.isoformat(),
                    "hours_running": round(elapsed_hours, 2),
                })

        return timed_out


# ---------------------------------------------------------------------------
# Fix 4 — Protocol compliance assertion
# ---------------------------------------------------------------------------
# Verify at import time that WorkerBridge satisfies the WorkerManager Protocol.
# WorkerManager is @runtime_checkable so isinstance() inspects method names.
# This catches regressions where a required protocol method is accidentally
# removed from WorkerBridge.
assert isinstance(WorkerBridge, type), "WorkerBridge must remain a class"
# Instantiate a minimal sentinel object solely to run the structural check.
_sentinel_bridge = WorkerBridge(
    worker_registry_factory=lambda: None,  # type: ignore[arg-type]
    workspace_dir=Path("."),
)
assert isinstance(_sentinel_bridge, WorkerManager), (
    "WorkerBridge does not satisfy the WorkerManager protocol — "
    "ensure spawn_worker(), on_worker_complete(), on_worker_error(), "
    "and resume_worker() are all defined."
)
del _sentinel_bridge
