#!/usr/bin/env python3
"""GatewayHook — bridges the Hermes hierarchy into a message-handling gateway.

Initializes ProfileRegistry, MessageBus (via RegistryAdapter), and IPCListener,
implementing the MessageHandler protocol to process incoming IPC messages.

When a TASK_REQUEST arrives, the gateway spawns a worker via WorkerBridge
and links it to the delegation chain for result propagation.

Thread-safe. Suitable for both long-running daemons and one-shot cron use.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.integration.chain_store import ChainStore
from core.integration.exceptions import ChainNotFound
from core.ipc.interface import MessageHandler
from core.ipc.message_bus import MessageBus
from core.ipc.models import Message, MessageStatus, MessageType
from core.registry.profile_registry import ProfileRegistry
from integrations.hermes.config import HermesConfig
from integrations.hermes.ipc_listener import IPCListener

logger = logging.getLogger(__name__)


def _strip_tool_traces(text: str) -> str:
    """Strip worker tool-trace output, returning only the final prose summary.

    Worker output looks like a sequence of tool calls followed by a final
    prose conclusion.  We want only the conclusion — the last contiguous
    block of non-trace text after all tool calls have finished.

    Tool-trace lines include:
        ┊ 🔍 recall ...
        ┊ 💻 $ command ...
        ┊ 🔧 patch ...
        ┊ review diff
        a//path → b//path
        @@ hunk headers
        +added / -removed diff lines
        ┊ 💬 intermediate thought ...
        (command continuation lines like "cat ..." after a $)

    Strategy: find the LAST block of text that follows all tool traces.
    If no clean trailing block exists, fall back to joining all 💬 thoughts.
    """
    import re

    lines = text.splitlines()

    # Pass 1: mark which lines are "trace" lines
    in_diff = False
    after_tool_dollar = False  # multi-line shell command continuation
    trace = []  # True = trace line, False = prose line
    for line in lines:
        stripped = line.strip()

        # Diff block: starts with a//... → b//... or @@ or review diff
        if re.match(r'^[a-z]?//.*\s→\s', stripped) or re.match(r'^@@', stripped):
            in_diff = True
        if in_diff:
            # Exit diff when we hit a ┊ line or a truly blank line followed by non-diff
            if re.match(r'^\s*┊', line) or (not stripped and not in_diff):
                in_diff = False
            else:
                trace.append(True)
                continue

        # Any ┊ line is a trace
        if re.match(r'^\s*┊', line):
            # Check if it's a shell command (we expect continuation lines after)
            after_tool_dollar = bool(re.match(r'^\s*┊\s+💻\s+\$', line))
            in_diff = bool(re.match(r'^\s*┊\s+review diff', stripped))
            trace.append(True)
            continue

        # Continuation of a multi-line shell command (no ┊ prefix)
        if after_tool_dollar and stripped and not stripped.startswith('┊'):
            # Heuristic: if it ends with a timing like "0.5s", it's still trace
            if re.search(r'\s+\d+\.\d+s\s*$', stripped) or re.match(r'^[a-z/~]', stripped):
                trace.append(True)
                continue
        after_tool_dollar = False

        # Diff content lines (+/-/ )
        if in_diff or re.match(r'^[+\- ]', line) and len(lines) > 5:
            # Only treat as diff if we're in a diff context
            pass

        trace.append(False)

    # Pass 2: find the last contiguous prose block after all traces
    # Work backwards from end of file
    last_prose_end = len(lines)
    first_prose_start = last_prose_end
    i = len(lines) - 1
    while i >= 0:
        if not trace[i] and lines[i].strip():
            first_prose_start = i
        elif trace[i] and first_prose_start < last_prose_end:
            break  # hit a trace line below the prose block — stop
        i -= 1

    trailing_prose = [lines[j] for j in range(first_prose_start, last_prose_end) if not trace[j] or not lines[j].strip()]
    result = '\n'.join(trailing_prose).strip()

    # If the trailing block is tiny (<50 chars), collect all 💬 thoughts instead
    if len(result) < 50:
        thoughts = []
        for line in lines:
            if re.match(r'^\s*┊\s+💬', line):
                prose = re.sub(r'^\s*┊\s+💬\s*', '', line).strip()
                if prose:
                    thoughts.append(prose)
        result = '\n\n'.join(thoughts).strip()

    # Final fallback
    if not result:
        return text

    return re.sub(r'\n{3,}', '\n\n', result)


# ---------------------------------------------------------------------------
# Paths (mirrors hierarchy_manager.py)
# ---------------------------------------------------------------------------

HIERARCHY_DIR = Path.home() / ".hermes" / "hierarchy"
REGISTRY_DB = HIERARCHY_DIR / "registry.db"
IPC_DB = HIERARCHY_DIR / "ipc.db"
LOGS_DIR = HIERARCHY_DIR / "logs"


class RegistryAdapter:
    """Adapts ProfileRegistry to the duck-typed interface expected by MessageBus.

    MessageBus calls ``.get(name)`` for recipient validation, while
    ProfileRegistry exposes ``.get_profile(name)``.  This thin wrapper
    bridges the gap and also forwards ``get_profile`` and
    ``get_chain_of_command`` for MessageProtocol compatibility.
    """

    def __init__(self, registry: ProfileRegistry) -> None:
        self._registry = registry

    def get(self, name: str) -> Any:
        return self._registry.get_profile(name)

    def get_profile(self, name: str) -> Any:
        return self._registry.get_profile(name)

    def get_chain_of_command(self, name: str) -> Any:
        return self._registry.get_chain_of_command(name)


# ---------------------------------------------------------------------------
# Processing stats (dataclass-like, but plain dict for zero-dep simplicity)
# ---------------------------------------------------------------------------

class GatewayStats:
    """Thread-safe processing statistics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processed: int = 0
        self._errors: int = 0
        self._last_processed: Optional[datetime] = None

    @property
    def processed(self) -> int:
        with self._lock:
            return self._processed

    @property
    def errors(self) -> int:
        with self._lock:
            return self._errors

    @property
    def last_processed(self) -> Optional[datetime]:
        with self._lock:
            return self._last_processed

    def record_success(self) -> None:
        with self._lock:
            self._processed += 1
            self._last_processed = datetime.now(timezone.utc)

    def record_error(self) -> None:
        with self._lock:
            self._errors += 1
            self._last_processed = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "processed": self._processed,
                "errors": self._errors,
                "last_processed": (
                    self._last_processed.isoformat()
                    if self._last_processed
                    else None
                ),
            }


# ---------------------------------------------------------------------------
# GatewayHook
# ---------------------------------------------------------------------------

class GatewayHook:
    """Message-handling gateway for a single Hermes profile.

    Implements the ``MessageHandler`` protocol so it can be plugged directly
    into an ``IPCListener``.

    When a ``TASK_REQUEST`` message arrives and a ``worker_bridge`` is
    configured, the gateway automatically spawns a worker to execute
    the task and wires up chain-based result propagation.

    Parameters
    ----------
    profile_name : str
        The hierarchy profile this gateway serves (e.g. ``"hermes"``,
        ``"cto"``, ``"pm-hier-arch"``).
    config : HermesConfig | None
        Optional configuration override.  Defaults are used when *None*.
    on_message : callable | None
        Optional callback ``(Message) -> None`` invoked for every received
        message (useful for CLI display).
    worker_bridge : object | None
        A :class:`~integrations.hermes.worker_bridge.WorkerBridge` instance
        used to spawn and track worker subagents. When ``None``, task
        execution is skipped (log-only mode, legacy behaviour).
    chain_store : ChainStore | None
        Persistent store for looking up delegation chains referenced in
        incoming task messages. Required for chain-linked execution.
    chain_orchestrator : object | None
        A :class:`~core.integration.orchestrator.ChainOrchestrator` instance.
        When provided alongside ``chain_store``, workers are spawned via the
        orchestrator so that ``chain.workers`` and ``chain.worker_results``
        are properly tracked and results propagate back up the chain.
    task_executor : callable | None
        Optional callable ``(task: str, subagent_id: str, pm_profile: str) -> str``
        that performs the actual work. When ``None`` and ``auto_execute``
        is ``True``, the gateway launches a real claude/hermes subprocess
        with the profile's full context. When ``None`` and ``auto_execute``
        is ``False``, the worker stays ``running`` for external completion.
    auto_execute : bool
        When ``True`` (default), the gateway automatically launches a real
        claude/hermes subprocess to execute tasks when no ``task_executor``
        is provided. The profile's SOUL.md and memory are injected as
        context so the subprocess acts as this profile.
        Set to ``False`` in tests or when workers should be completed
        externally.
    """

    def __init__(
        self,
        profile_name: str,
        config: Optional[HermesConfig] = None,
        on_message: Optional[Any] = None,
        worker_bridge: Optional[Any] = None,
        chain_store: Optional[ChainStore] = None,
        chain_orchestrator: Optional[Any] = None,
        task_executor: Optional[Callable[..., str]] = None,
        auto_execute: bool = False,
        message_bus: Optional[MessageBus] = None,
        delivery_hook: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._profile_name = profile_name
        self._config = config or HermesConfig()
        self._on_message = on_message
        self._worker_bridge = worker_bridge
        self._chain_store = chain_store
        self._chain_orchestrator = chain_orchestrator
        self._task_executor = task_executor
        self._auto_execute = auto_execute
        self._delivery_hook = delivery_hook
        self._delivered_worker_ids: set[str] = set()
        self._delivered_content_hashes: set[str] = set()
        self._delivered_task_keys: set[str] = set()
        # Track chain/correlation IDs that originated from user interactions
        # so responses propagated up through intermediate hops still fire
        # the delivery hook even without an explicit user_talk flag.
        self._user_talk_chain_ids: set[str] = set()
        self._lock = threading.Lock()

        # Stats
        self._stats = GatewayStats()

        # Handler results accumulated during processing
        self._results: List[Optional[Message]] = []
        self._results_lock = threading.Lock()

        # Initialise infrastructure — use injected bus or create one
        if message_bus is not None:
            self._bus = message_bus
            self._registry = None
            self._adapter = None
        else:
            self._registry = ProfileRegistry(str(REGISTRY_DB))
            self._adapter = RegistryAdapter(self._registry)
            self._bus = MessageBus(str(IPC_DB), profile_registry=self._adapter)
        self._listener = IPCListener(
            self._bus,
            self,  # self implements MessageHandler
            poll_interval=self._config.poll_interval_seconds,
            profile_name=self._profile_name,
        )

        self._running = False
        logger.info(
            "GatewayHook initialised for profile '%s' (poll=%.1fs)",
            self._profile_name,
            self._config.poll_interval_seconds,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def profile_name(self) -> str:
        """The profile this gateway is attached to."""
        return self._profile_name

    @property
    def stats(self) -> GatewayStats:
        """Processing statistics."""
        return self._stats

    @property
    def is_running(self) -> bool:
        """Whether the background listener is active."""
        with self._lock:
            return self._running

    @property
    def results(self) -> List[Optional[Message]]:
        """Accumulated handler results (response messages or None)."""
        with self._results_lock:
            return list(self._results)

    # ------------------------------------------------------------------
    # MessageHandler protocol implementation
    # ------------------------------------------------------------------

    def handle_message(self, message: Message) -> Optional[Message]:
        """Process an incoming message.

        For ``TASK_REQUEST`` messages (when a worker_bridge is configured):
        1. Extracts the task and chain_id from the payload.
        2. Spawns a worker via ``WorkerBridge.spawn()``.
        3. If a chain_store is available, loads the chain and sets up
           auto-propagation so results flow back up automatically.
        4. If a task_executor is provided, runs it synchronously and
           marks the worker as complete.

        For all other message types (or when no worker_bridge is configured):
        logs, acknowledges, and fires the on_message callback.

        Returns
        -------
        Optional[Message]
            Always returns ``None`` (fire-and-forget acknowledgement model).
        """
        try:
            logger.info(
                "[%s] Received %s from '%s': %s (id=%s, priority=%s)",
                self._profile_name,
                message.message_type.value,
                message.from_profile,
                message.payload,
                message.message_id,
                message.priority.value,
            )

            # Fire user callback if provided
            if self._on_message is not None:
                try:
                    self._on_message(message)
                except Exception as cb_err:
                    logger.warning(
                        "[%s] on_message callback error: %s",
                        self._profile_name,
                        cb_err,
                    )

            # --- Task execution for TASK_REQUEST messages ---
            if (
                message.message_type == MessageType.TASK_REQUEST
                and self._worker_bridge is not None
            ):
                self._execute_task(message)

            # --- Forward TASK_RESPONSE messages up the hierarchy ---
            elif message.message_type == MessageType.TASK_RESPONSE:
                self._forward_response_upstream(message)

            # Store result (None for fire-and-forget)
            result: Optional[Message] = None
            with self._results_lock:
                self._results.append(result)

            self._stats.record_success()
            return result

        except Exception as exc:
            logger.error(
                "[%s] Error handling message %s: %s",
                self._profile_name,
                message.message_id,
                exc,
            )
            self._stats.record_error()
            return None

    def _forward_response_upstream(self, message: Message) -> bool:
        """Forward a TASK_RESPONSE to this profile's parent in the hierarchy.

        When a downstream profile sends a response (e.g. PM → CTO), the
        CTO gateway auto-forwards it to its parent (hermes) so results
        propagate up without requiring each intermediate agent to be
        actively running and calling ``check_inbox``.

        If no parent can be determined (CEO / root), the message is
        delivered to the owner via the delivery hook.

        Returns
        -------
        bool
            True if the message should NOT be acknowledged (root profile
            keeps it pending for ``check_inbox``), False otherwise.
        """
        payload = message.payload or {}
        parent = None

        # Try to resolve parent from the profile registry
        if self._registry is not None:
            try:
                profile = self._registry.get_profile(self._profile_name)
                parent = profile.parent_profile if profile else None
            except Exception as exc:
                logger.debug(
                    "[%s] Could not resolve parent profile: %s",
                    self._profile_name,
                    exc,
                )

        if not parent:
            logger.info(
                "[%s] Root profile — delivering TASK_RESPONSE to owner (from %s)",
                self._profile_name,
                message.from_profile,
            )
            self._deliver_to_owner(message)
            # Acknowledge normally — the hook delivers to the user directly.
            # The delivery file in ~/.hermes/hierarchy/delivery/ serves as
            # the audit trail for past responses.
            return False

        # Forward the response payload to the parent, preserving origin info
        forwarded_payload = dict(payload)
        forwarded_payload["forwarded_by"] = self._profile_name

        # Propagate user_talk flag if this chain was originally user-initiated.
        # Without this, PM responses forwarded through CTO lose the flag and
        # the delivery hook silently drops them.
        if not forwarded_payload.get("user_talk"):
            chain_id = payload.get("chain_id") or message.correlation_id
            if chain_id and chain_id in self._user_talk_chain_ids:
                forwarded_payload["user_talk"] = True

        try:
            msg_id = self._bus.send(
                from_profile=self._profile_name,
                to_profile=parent,
                message_type=MessageType.TASK_RESPONSE,
                payload=forwarded_payload,
                correlation_id=message.correlation_id,
            )
            logger.info(
                "[%s] Forwarded TASK_RESPONSE upstream to '%s' (msg=%s, originally from '%s')",
                self._profile_name,
                parent,
                msg_id,
                message.from_profile,
            )
            self._write_notification(parent, msg_id,
                                     payload.get("task", ""),
                                     payload.get("worker_id", ""))
        except Exception as fwd_err:
            logger.warning(
                "[%s] Failed to forward TASK_RESPONSE to '%s': %s",
                self._profile_name,
                parent,
                fwd_err,
            )

        # Non-root: acknowledge normally (forwarded copy is the canonical one)
        return False

    def _execute_task(self, message: Message) -> None:
        """Spawn a worker for a TASK_REQUEST and execute it.

        Execution modes (in priority order):

        1. **task_executor** — caller-supplied callable (tests, custom logic)
        2. **invoke_agent** — launches a real claude/hermes subprocess as
           this profile, with the profile's SOUL.md + memory as context.
           This is the default production path: the profile "wakes up."
        3. **no-op** — worker stays ``running`` for external completion
           (only if the bridge has no ``invoke_agent`` method)

        When a ``chain_orchestrator`` is available and the message includes
        a ``chain_id``, the orchestrator is used to spawn and complete
        workers so that ``chain.workers`` and ``chain.worker_results`` are
        properly updated and results propagate back up the hierarchy.

        Parameters
        ----------
        message : Message
            The incoming TASK_REQUEST message.
        """
        payload = message.payload or {}
        task = payload.get("task", "")
        chain_id = payload.get("chain_id")
        user_talk = bool(payload.get("user_talk", False))

        # Remember this chain as user-initiated so forwarded responses
        # from downstream agents also get delivered to the owner.
        if user_talk and chain_id:
            self._user_talk_chain_ids.add(chain_id)
        if user_talk and message.correlation_id:
            self._user_talk_chain_ids.add(message.correlation_id)

        if not task:
            logger.warning(
                "[%s] TASK_REQUEST with empty task payload, skipping",
                self._profile_name,
            )
            return

        bridge = self._worker_bridge
        orchestrator = self._chain_orchestrator

        # Load the delegation chain if we have a store and chain_id
        chain = None
        if chain_id and self._chain_store is not None:
            try:
                chain = self._chain_store.get(chain_id)
            except ChainNotFound:
                logger.warning(
                    "[%s] Chain %s not found in store, spawning without chain link",
                    self._profile_name,
                    chain_id,
                )

        # Spawn the worker — prefer orchestrator path for proper chain tracking
        if chain is not None and orchestrator is not None:
            subagent_id = orchestrator.spawn_worker(
                chain=chain,
                pm_profile=self._profile_name,
                task=task,
            )
        elif chain is not None and hasattr(bridge, "spawn_with_chain"):
            subagent_id = bridge.spawn_with_chain(
                pm_profile=self._profile_name,
                task=task,
                chain=chain,
            )
        else:
            subagent_id = bridge.spawn(
                pm_profile=self._profile_name,
                task=task,
            )

        logger.info(
            "[%s] Spawned worker %s for task: %.100s (chain=%s)",
            self._profile_name,
            subagent_id,
            task,
            chain_id or "none",
        )

        # --- Determine the executor ---
        # Priority: explicit task_executor > invoke_agent (if auto_execute) > no-op
        executor_fn = self._task_executor

        if executor_fn is None and self._auto_execute and hasattr(bridge, "invoke_agent"):
            # Production path: launch hermes as this profile.
            # No need to inject SOUL.md/memory as context — hermes -p <profile>
            # loads the profile's own SOUL.md, model, skills, and toolsets natively.
            executor_fn = lambda t, sid, pm: bridge.invoke_agent(
                task=t,
                subagent_id=sid,
                pm_profile=pm,
                cli="hermes",
            )

        if executor_fn is not None:
            self._run_and_complete(
                executor_fn, task, subagent_id, chain, orchestrator, bridge,
                originator=message.from_profile,
                correlation_id=chain_id or message.correlation_id,
                user_talk=user_talk,
            )

    def _build_profile_context(self) -> str:
        """Build the profile's full context for prompting.

        Reads SOUL.md (identity) and recent memory entries to give the
        launched agent subprocess the profile's full persona and knowledge.

        Returns
        -------
        str
            Context string to prepend to the task prompt.
        """
        parts: list[str] = []
        profile_name = self._profile_name

        # 1. SOUL.md — the profile's identity
        soul_path = Path.home() / ".hermes" / "profiles" / profile_name / "SOUL.md"
        if soul_path.exists():
            try:
                content = soul_path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(content)
            except Exception as exc:
                logger.warning("[%s] Failed to read SOUL.md: %s", profile_name, exc)

        # 2. Recent memory
        try:
            from core.memory.memory_store import MemoryStore
            from core.memory.models import MemoryScope

            mem_path = Path.home() / ".hermes" / "hierarchy" / "memory" / f"{profile_name}.db"
            if mem_path.exists():
                store = MemoryStore(
                    str(mem_path),
                    profile_name=profile_name,
                    profile_scope=MemoryScope.project,
                )
                entries = store.list_entries(limit=15)
                if entries:
                    lines = []
                    for e in entries:
                        preview = e.content[:200]
                        lines.append(f"  [{e.tier.value}] {preview}")
                    parts.append(
                        f"=== YOUR MEMORY ({len(entries)} entries) ===\n"
                        + "\n".join(lines)
                    )
                store.close()
        except Exception as exc:
            logger.debug("[%s] Memory load skipped: %s", profile_name, exc)

        return "\n\n".join(parts) if parts else ""

    def _run_and_complete(
        self,
        executor_fn: Callable,
        task: str,
        subagent_id: str,
        chain: Any,
        orchestrator: Any,
        bridge: Any,
        originator: str,
        correlation_id: Optional[str] = None,
        user_talk: bool = False,
    ) -> None:
        """Run the executor, handle completion/failure, and send result via IPC.

        After the worker finishes (success or failure), a TASK_RESPONSE is
        always sent back to *originator* via IPC so they receive the result
        in their inbox without polling.

        Parameters
        ----------
        executor_fn : callable
            ``(task, subagent_id, pm_profile) -> str``
        task : str
            The task description.
        subagent_id : str
            The worker's subagent ID.
        chain : DelegationChain | None
            The delegation chain (if any).
        orchestrator : ChainOrchestrator | None
            The orchestrator (if any).
        bridge : WorkerBridge
            The worker bridge for fallback completion.
        originator : str
            Profile name to send the TASK_RESPONSE to.
        correlation_id : str | None
            Correlation ID linking this response to the original request.
        """
        try:
            result = executor_fn(task, subagent_id, self._profile_name)

            # Complete via orchestrator if available (updates chain state)
            if chain is not None and orchestrator is not None:
                all_done = orchestrator.complete_worker(
                    chain=chain,
                    pm_profile=self._profile_name,
                    subagent_id=subagent_id,
                    result=result,
                )
                if all_done:
                    orchestrator.propagate_result(chain, result)
            else:
                bridge.complete(
                    pm_profile=self._profile_name,
                    subagent_id=subagent_id,
                    result=result,
                )

            # Always send TASK_RESPONSE back to originator via IPC
            self._send_response(originator, correlation_id, result=result,
                                task=task, worker_id=subagent_id, user_talk=user_talk)

            logger.info(
                "[%s] Worker %s completed: %.100s",
                self._profile_name,
                subagent_id,
                result,
            )
        except Exception as exc:
            if chain is not None and orchestrator is not None:
                orchestrator.fail_chain(chain, str(exc))
            else:
                bridge.fail(
                    pm_profile=self._profile_name,
                    subagent_id=subagent_id,
                    error_message=str(exc),
                )

            # Always send error TASK_RESPONSE back to originator via IPC
            self._send_response(originator, correlation_id, error=str(exc),
                                task=task, worker_id=subagent_id, user_talk=user_talk)

            logger.error(
                "[%s] Worker %s failed: %s",
                self._profile_name,
                subagent_id,
                exc,
            )

    def _send_response(
        self,
        to_profile: str,
        correlation_id: Optional[str],
        *,
        result: Optional[str] = None,
        error: Optional[str] = None,
        task: str = "",
        worker_id: str = "",
        user_talk: bool = False,
    ) -> None:
        """Send a TASK_RESPONSE to a profile via IPC and write a notification file.

        Best-effort — logs a warning on failure but never raises.

        The notification file at ``~/.hermes/hierarchy/notifications/<to_profile>.json``
        allows agents or the CLI to detect incoming responses without polling
        the message bus.
        """
        payload: Dict[str, Any] = {
            "task": task,
            "worker_id": worker_id,
            "from_profile": self._profile_name,
        }
        if result is not None:
            payload["result"] = result
        if error is not None:
            payload["error"] = error
        if user_talk:
            payload["user_talk"] = True

        try:
            msg_id = self._bus.send(
                from_profile=self._profile_name,
                to_profile=to_profile,
                message_type=MessageType.TASK_RESPONSE,
                payload=payload,
                correlation_id=correlation_id,
            )
            logger.info(
                "[%s] Sent TASK_RESPONSE to '%s' (msg=%s)",
                self._profile_name,
                to_profile,
                msg_id,
            )

            # Write notification file so the recipient can detect the
            # response without polling check_inbox.
            self._write_notification(to_profile, msg_id, task, worker_id)

        except Exception as send_err:
            logger.warning(
                "[%s] Failed to send TASK_RESPONSE to '%s': %s",
                self._profile_name,
                to_profile,
                send_err,
            )

    def _write_notification(
        self,
        to_profile: str,
        message_id: str,
        task: str,
        worker_id: str,
    ) -> None:
        """Write a notification file for the recipient profile.

        Creates/overwrites ``~/.hermes/hierarchy/notifications/<profile>.json``
        with minimal metadata about the latest response.  Best-effort.
        """
        import json as _json

        notify_dir = HIERARCHY_DIR / "notifications"
        try:
            notify_dir.mkdir(parents=True, exist_ok=True)
            notify_path = notify_dir / f"{to_profile}.json"
            data = {
                "message_id": message_id,
                "from_profile": self._profile_name,
                "task_preview": task[:200],
                "worker_id": worker_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            notify_path.write_text(_json.dumps(data), encoding="utf-8")
        except Exception as exc:
            logger.debug(
                "[%s] Failed to write notification for '%s': %s",
                self._profile_name,
                to_profile,
                exc,
            )

    def _deliver_to_owner(self, message: Message) -> None:
        """Deliver a TASK_RESPONSE to the owner when this is the root profile.

        Two delivery paths:

        1. **File queue** — writes to ``~/.hermes/hierarchy/delivery/`` as a
           JSON file (audit trail, always runs).
        2. **Delivery hook** — if a ``delivery_hook`` callable was provided
           at init, calls it with the formatted message text in a background
           thread.  The hook is a simple ``(text: str) -> bool`` function
           that sends the message via the appropriate platform (Telegram,
           Discord, webhook, etc.) and returns True on success.

        The hook keeps the gateway lightweight — no agent session, no model
        invocation, just a direct API call.
        """
        import json as _json
        import uuid

        payload = message.payload or {}
        result = payload.get("result", "")
        error = payload.get("error", "")
        task = payload.get("task", "")
        from_profile = payload.get("from_profile", message.from_profile)
        forwarded_by = payload.get("forwarded_by")

        # --- Specialist bubble-up filter ---
        # When a specialist (dev-*, sec-*) responds to their PM, the PM
        # gateway auto-forwards it up to hermes.  The owner doesn't need
        # these — they'll hear from the PM when the PM is ready.
        # Only suppress forwarded specialist responses; if hermes sent
        # directly to a specialist (no forwarded_by), deliver it.
        if forwarded_by and self._registry is not None:
            try:
                sender = self._registry.get_profile(from_profile)
                if sender and sender.role == "specialist":
                    logger.info(
                        "[%s] Suppressing forwarded specialist response from "
                        "'%s' (forwarded by '%s') — owner hears from PM only.",
                        self._profile_name, from_profile, forwarded_by,
                    )
                    return
            except Exception:
                pass

        # --- 1. Write to file queue (audit trail) ---
        delivery_dir = HIERARCHY_DIR / "delivery"
        delivery_path = None
        try:
            delivery_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc)
            filename = f"{ts.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.json"
            delivery_path = delivery_dir / filename
            data = {
                "from_profile": from_profile,
                "forwarded_by": forwarded_by,
                "task": task[:500],
                "result": result,
                "error": error,
                "message_id": message.message_id,
                "correlation_id": message.correlation_id,
                "timestamp": ts.isoformat(),
                "delivered": False,
            }
            delivery_path.write_text(
                _json.dumps(data, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(
                "[%s] Failed to write delivery file: %s",
                self._profile_name,
                exc,
            )

        # --- 2. Call delivery hook (background thread) ---
        if self._delivery_hook is None:
            return

        # Deliver all responses that reach the root gateway.  The
        # user_talk flag was meant to distinguish user-initiated work from
        # autonomous background work, but it doesn't survive sub-delegations
        # (CTO→PM loses the flag because hierarchy_tools only sets it for
        # from_profile=="hermes").  The remaining filters (trivial content,
        # worker_id dedup, content-hash dedup) already prevent spam, so the
        # root gateway can safely deliver everything with real content.

        content = error if error else result
        if not content or len(content.strip()) < 10:
            logger.info(
                "[%s] Skipping delivery hook — empty or trivial result",
                self._profile_name,
            )
            return

        # Skip test/placeholder results that aren't real work
        _trivial = content.strip().lower()
        if _trivial.startswith("executed:") or _trivial in (
            "ok", "done", "acknowledged", "received",
        ):
            logger.info(
                "[%s] Skipping delivery hook — trivial/test result: %.50s",
                self._profile_name,
                _trivial,
            )
            return

        # Strip tool traces early — we need the cleaned content for
        # both the "mostly noise" check and the final message.
        display = _strip_tool_traces(content)

        # If stripping tool traces reduced the content to almost nothing,
        # the response was a raw agent output dump (tool calls, diffs,
        # command output) with no meaningful conclusion.  Skip it — the
        # useful result will arrive in a separate PM-forwarded response.
        if len(display.strip()) < 30 and len(content) > 200:
            logger.info(
                "[%s] Skipping delivery hook — raw tool output with no prose (%d→%d chars, from %s)",
                self._profile_name,
                len(content),
                len(display.strip()),
                from_profile,
            )
            return

        # --- Deduplication (three layers) ---

        # 1. Task-based dedup: same originating task = same user request.
        #    When CTO delegates to PM, the task field propagates.  Multiple
        #    responses (CTO raw, PM forwarded, CTO synthesis) all share the
        #    same task text.  Deliver only the first one per task.
        task_key = task.strip()[:120] if task else ""
        if task_key:
            if task_key in self._delivered_task_keys:
                logger.info(
                    "[%s] Skipping delivery hook — already delivered for this task (from %s): %.60s",
                    self._profile_name,
                    from_profile,
                    task_key,
                )
                return
            self._delivered_task_keys.add(task_key)

        # 2. Worker-id dedup: same worker sending results via different paths.
        worker_id = payload.get("worker_id", "")
        if worker_id and worker_id in self._delivered_worker_ids:
            logger.info(
                "[%s] Skipping delivery hook — duplicate worker_id: %s",
                self._profile_name,
                worker_id,
            )
            return
        if worker_id:
            self._delivered_worker_ids.add(worker_id)

        # 3. Content-hash dedup: identical text from different sources.
        content_hash = hashlib.sha256(display.encode("utf-8", errors="replace")).hexdigest()[:16]
        if content_hash in self._delivered_content_hashes:
            logger.info(
                "[%s] Skipping delivery hook — duplicate content (hash=%s)",
                self._profile_name,
                content_hash,
            )
            return
        self._delivered_content_hashes.add(content_hash)

        # Use the original sender, not the forwarding hop — user shouldn't
        # see CTO as the sender when PM did the work.
        origin = from_profile
        owner_message = f"[{origin}]\n\n{display}"

        hook = self._delivery_hook
        fpath = delivery_path

        def _deliver_async() -> None:
            try:
                success = hook(owner_message)
                if success:
                    logger.info(
                        "[%s] Delivery hook succeeded (from %s)",
                        self._profile_name,
                        from_profile,
                    )
                    if fpath and fpath.exists():
                        try:
                            fdata = _json.loads(
                                fpath.read_text(encoding="utf-8")
                            )
                            fdata["delivered"] = True
                            fdata["delivered_at"] = datetime.now(
                                timezone.utc
                            ).isoformat()
                            fdata["delivered_via"] = "hook"
                            fpath.write_text(
                                _json.dumps(fdata, indent=2), encoding="utf-8"
                            )
                        except Exception:
                            pass
                else:
                    logger.warning(
                        "[%s] Delivery hook returned False (from %s)",
                        self._profile_name,
                        from_profile,
                    )
            except Exception as exc:
                logger.warning(
                    "[%s] Delivery hook error: %s", self._profile_name, exc
                )

        thread = threading.Thread(
            target=_deliver_async,
            name=f"delivery-{from_profile}",
            daemon=True,
        )
        thread.start()

    # ------------------------------------------------------------------
    # Lifecycle: start / stop (long-running daemon)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background IPCListener for continuous polling.

        Does nothing if already running.
        """
        with self._lock:
            if self._running:
                logger.warning(
                    "[%s] Gateway already running", self._profile_name
                )
                return
            self._running = True

        logger.info("[%s] Starting gateway listener…", self._profile_name)
        self._listener.start()

    def stop(self) -> None:
        """Stop the background listener and release resources.

        Safe to call multiple times.
        """
        with self._lock:
            if not self._running:
                return
            self._running = False

        logger.info("[%s] Stopping gateway listener…", self._profile_name)
        self._listener.stop()

    # ------------------------------------------------------------------
    # One-shot processing (for cron / manual invocation)
    # ------------------------------------------------------------------

    def process_once(self, limit: int = 50) -> List[Message]:
        """Poll the bus once and process all pending messages.

        Does **not** start the background listener — suitable for cron jobs
        or CLI ``process`` subcommands.

        Parameters
        ----------
        limit : int
            Maximum number of messages to process in this batch.

        Returns
        -------
        list[Message]
            The messages that were processed.
        """
        logger.info(
            "[%s] One-shot processing (limit=%d)…",
            self._profile_name,
            limit,
        )

        messages = self._bus.poll(self._profile_name, limit=limit)
        logger.info(
            "[%s] Found %d pending message(s)", self._profile_name, len(messages)
        )

        for msg in messages:
            self.handle_message(msg)

        return messages

    # ------------------------------------------------------------------
    # Resource cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Stop the listener (if running) and close database connections."""
        self.stop()
        try:
            self._bus.close()
        except Exception:
            pass
        if self._registry is not None:
            try:
                self._registry.close()
            except Exception:
                pass
        logger.info("[%s] Gateway closed", self._profile_name)

    def __enter__(self) -> GatewayHook:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return a status dictionary for this gateway.

        Returns
        -------
        dict
            Keys: profile, running, pending, stats.
        """
        try:
            pending = self._bus.get_pending_count(self._profile_name)
        except Exception:
            pending = -1

        return {
            "profile": self._profile_name,
            "running": self.is_running,
            "pending": pending,
            "stats": self._stats.to_dict(),
        }
