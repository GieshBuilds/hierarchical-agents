#!/usr/bin/env python3
"""
Hierarchy Tools — Expose the Hierarchical Agent Architecture to Hermes agents.

Provides tools for inter-profile communication, org chart visualization,
worker management, and status queries. All tools register under the
'hierarchy' toolset and can be dropped into any Hermes profile's tools directory.

Tools:
  - send_to_profile: Route messages via IPC, optionally spawning target as subagent
  - check_inbox: Check pending IPC messages
  - org_chart: Display the organizational hierarchy
  - profile_status: Memory stats, worker counts, pending messages
  - spawn_tracked_worker: Spawn subagent with SubagentRegistry tracking
  - get_project_status: Worker status and recent completions for a PM
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path setup — ensure the hierarchy project is importable
# ---------------------------------------------------------------------------

_HIERARCHY_PROJECT_ROOT = os.environ.get(
    "HIERARCHY_PROJECT_ROOT",
    str(Path(__file__).resolve().parent.parent),
)

if _HIERARCHY_PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _HIERARCHY_PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

_DB_BASE_DIR = Path(os.environ.get(
    "HERMES_DB_BASE_DIR",
    str(Path.home() / ".hermes" / "hierarchy"),
))

_PROFILES_DIR = Path(os.environ.get(
    "HERMES_PROFILES_DIR",
    str(Path.home() / ".hermes" / "profiles"),
))

# ---------------------------------------------------------------------------
# Lazy singletons — initialized on first use
# ---------------------------------------------------------------------------

_profile_registry = None
_message_bus = None
_subagent_registry = None
_chain_orchestrator = None
_profile_activator = None
_memory_stores: Dict[str, Any] = {}
_knowledge_bases: Dict[str, Any] = {}


def _get_profile_registry():
    """Get or create the ProfileRegistry singleton."""
    global _profile_registry
    if _profile_registry is None:
        from core.registry.profile_registry import ProfileRegistry
        db_path = str(_DB_BASE_DIR / "registry.db")
        _profile_registry = ProfileRegistry(db_path)
    return _profile_registry


def _get_message_bus():
    """Get or create the MessageBus singleton.

    Note: The MessageBus expects a duck-typed registry with .get() but
    ProfileRegistry uses .get_profile(). We wrap it to bridge the gap.
    """
    global _message_bus
    if _message_bus is None:
        from core.ipc.message_bus import MessageBus
        db_path = str(_DB_BASE_DIR / "ipc.db")

        # Create a thin adapter so MessageBus._validate_recipient works
        # with our ProfileRegistry (which has .get_profile() not .get())
        reg = _get_profile_registry()

        class _RegistryAdapter:
            """Duck-typed adapter: bridges .get() to .get_profile()."""
            def __init__(self, registry):
                self._registry = registry
            def get(self, name):
                return self._registry.get_profile(name)

        _message_bus = MessageBus(
            db_path=db_path,
            profile_registry=_RegistryAdapter(reg),
        )
    return _message_bus


def _get_subagent_registry():
    """Get or create the SubagentRegistry singleton."""
    global _subagent_registry
    if _subagent_registry is None:
        from core.workers.subagent_registry import SubagentRegistry
        base_path = str(_DB_BASE_DIR / "workers")
        _subagent_registry = SubagentRegistry(
            base_path=base_path,
            profile_registry=_get_profile_registry(),
        )
    return _subagent_registry


def _get_chain_orchestrator():
    """Get or create the ChainOrchestrator singleton.

    Wires the orchestrator to the existing ProfileRegistry, MessageBus,
    SubagentRegistry, and ChainStore singletons so that delegation chains
    created here are persisted to SQLite and survive process restarts.
    """
    global _chain_orchestrator
    if _chain_orchestrator is None:
        from core.integration.chain_store import ChainStore
        from core.integration.orchestrator import ChainOrchestrator

        chain_db_path = str(_DB_BASE_DIR / "chains.db")
        store = ChainStore(db_path=chain_db_path)

        _chain_orchestrator = ChainOrchestrator(
            registry=_get_profile_registry(),
            bus=_get_message_bus(),
            worker_registry_factory=lambda pm_name: _get_subagent_registry(),
            chain_store=store,
        )
    return _chain_orchestrator


def _get_profile_activator():
    """Get or create the HermesProfileActivator singleton.

    The activator auto-launches ``hierarchy_gateway.py start <profile>``
    as a detached background process for any profile that receives a
    message. The gateway outlives the calling hermes process.
    """
    global _profile_activator
    if _profile_activator is None:
        from integrations.hermes.activation import HermesProfileActivator
        from integrations.hermes.config import HermesConfig

        _profile_activator = HermesProfileActivator(
            config=HermesConfig(db_base_dir=_DB_BASE_DIR),
            gateway_script=Path.home() / ".hermes" / "hierarchy" / "hierarchy_gateway.py",
        )
    return _profile_activator


def _get_knowledge_base(profile_name: str):
    """Get or create a KnowledgeBase for the given profile.

    All instances point at the shared ``knowledge.db`` file but are
    scoped to different ``profile_name`` values so that
    ``add_knowledge()`` correctly attributes entries to the caller.
    """
    if profile_name not in _knowledge_bases:
        from core.memory.knowledge_base import KnowledgeBase

        db_path = str(_DB_BASE_DIR / "memory" / "knowledge.db")
        _knowledge_bases[profile_name] = KnowledgeBase(
            db_path=db_path,
            profile_name=profile_name,
        )
    return _knowledge_bases[profile_name]


def _get_memory_store(profile_name: str):
    """Get or create a MemoryStore for the given profile."""
    if profile_name not in _memory_stores:
        from core.memory.memory_store import MemoryStore
        from core.memory.models import MemoryScope, ROLE_SCOPE_MAP

        db_path = str(_DB_BASE_DIR / "memory" / f"{profile_name}.db")

        # Determine scope from profile role
        try:
            reg = _get_profile_registry()
            profile = reg.get_profile(profile_name)
            scope = ROLE_SCOPE_MAP.get(profile.role, MemoryScope.task)
        except Exception:
            scope = MemoryScope.task

        # Only create if the DB file exists (don't create empty DBs)
        if Path(db_path).exists():
            _memory_stores[profile_name] = MemoryStore(
                db_path=db_path,
                profile_name=profile_name,
                profile_scope=scope,
            )
        else:
            _memory_stores[profile_name] = None

    return _memory_stores.get(profile_name)


def _get_current_profile() -> str:
    """Detect which profile the current agent is running as.

    Checks HERMES_PROFILE env var, falls back to 'hermes'.
    """
    return os.environ.get("HERMES_PROFILE", "hermes")


# ---------------------------------------------------------------------------
# Context building for send_to_profile with wait_for_response
# ---------------------------------------------------------------------------


def _build_profile_context(profile_name: str) -> str:
    """Build a rich context string for spawning a profile as a subagent.

    Combines:
    1. The profile's SOUL.md (identity/instructions)
    2. Recent scoped memory entries
    3. Pending IPC messages
    """
    parts: List[str] = []

    # 1. SOUL.md
    soul_path = _PROFILES_DIR / profile_name / "SOUL.md"
    if soul_path.exists():
        try:
            soul_content = soul_path.read_text(encoding="utf-8").strip()
            if soul_content:
                parts.append(f"=== YOUR IDENTITY (SOUL.md) ===\n{soul_content}")
        except Exception as e:
            logger.warning("Failed to read SOUL.md for %s: %s", profile_name, e)

    # 2. Recent scoped memory
    mem_store = _get_memory_store(profile_name)
    if mem_store is not None:
        try:
            entries = mem_store.list_entries(limit=20)
            if entries:
                memory_lines = []
                for entry in entries:
                    preview = entry.content[:200]
                    if len(entry.content) > 200:
                        preview += "..."
                    memory_lines.append(
                        f"  [{entry.tier.value}] ({entry.entry_type.value}) {preview}"
                    )
                parts.append(
                    f"=== YOUR SCOPED MEMORY ({len(entries)} recent entries) ===\n"
                    + "\n".join(memory_lines)
                )
        except Exception as e:
            logger.warning("Failed to load memory for %s: %s", profile_name, e)

    # 3. Pending IPC messages
    try:
        bus = _get_message_bus()
        pending = bus.poll(profile_name, limit=10)
        if pending:
            msg_lines = []
            for msg in pending:
                payload_preview = json.dumps(msg.payload)[:150]
                msg_lines.append(
                    f"  [{msg.priority.value}] From {msg.from_profile}: "
                    f"{msg.message_type.value} — {payload_preview}"
                )
            parts.append(
                f"=== PENDING MESSAGES ({len(pending)}) ===\n"
                + "\n".join(msg_lines)
            )
    except Exception as e:
        logger.warning("Failed to load IPC messages for %s: %s", profile_name, e)

    return "\n\n".join(parts) if parts else f"(No additional context for profile '{profile_name}')"


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def send_to_profile(args: dict, **kwargs) -> str:
    """Send a message to another profile via IPC.

    If wait_for_response=True, spawns the target profile as a subagent
    with their full context (SOUL.md + memory + IPC) and returns their response.

    If deliver_to is set (e.g., "telegram", "origin", "local"), schedules a
    one-shot Hermes cron job that runs the target profile's task and delivers
    the result to the specified destination. This bypasses the parent_agent
    requirement entirely and works from any context.
    """
    to = args.get("to", "").strip()
    message = args.get("message", "").strip()
    priority = args.get("priority", "normal").strip().lower()
    wait_for_response = args.get("wait_for_response", False)
    deliver_to = (args.get("deliver_to") or "").strip() or None
    track = args.get("track", False)

    if not to:
        return json.dumps({"error": "Parameter 'to' (target profile) is required."})
    if not message:
        return json.dumps({"error": "Parameter 'message' is required."})

    from_profile = _get_current_profile()
    direct = args.get("direct", False)

    # --- Specialist guard ---
    # When hermes (root) tries to send to a specialist, block the send and
    # redirect to the specialist's parent PM.  The owner can still reach
    # specialists directly when he explicitly names them — hermes sets
    # direct=True in that case.
    if from_profile == "hermes" and not direct:
        try:
            reg = _get_profile_registry()
            target_profile = reg.get_profile(to)
            if target_profile and target_profile.role == "specialist":
                parent_pm = target_profile.parent_profile or "unknown"
                return json.dumps({
                    "error": (
                        f"'{to}' is a specialist — send to their PM "
                        f"'{parent_pm}' instead.  The PM manages their "
                        f"own specialists.  Use direct=True only when "
                        f"the owner explicitly names this specialist."
                    ),
                    "redirect_to": parent_pm,
                })
        except Exception:
            pass  # registry unavailable — allow the send

    # Validate priority
    from core.ipc.models import MessagePriority, MessageType
    try:
        priority_enum = MessagePriority(priority)
    except ValueError:
        return json.dumps({"error": f"Invalid priority '{priority}'. Use: low, normal, urgent."})

    # --- Chain-tracked delegation (track=True) ---
    # Creates a DelegationChain and delegates through the hierarchy via
    # ChainOrchestrator.  The chain_id is included in the IPC payload so
    # downstream agents can reference it when spawning workers.
    #
    # DIRECT SEND OPTIMISATION: when the root profile (hermes) sends to
    # a profile that is NOT a direct report, skip the chain hops and send
    # via raw IPC instead.  This avoids waking every intermediate agent
    # (e.g. CTO) just to relay a message — saving tokens and latency.
    # Chain-hop delegation is still used when an intermediate profile
    # delegates to its own subordinates (CTO → PM).
    if track:
        # Check if we can send directly (hermes → any named profile)
        _direct_send = False
        if from_profile == "hermes":
            try:
                reg = _get_chain_orchestrator()._registry
                target = reg.get_profile(to)
                # Direct report of hermes → use chain (1 hop, no waste)
                # Deeper profile → skip chain, send direct via IPC
                if target and target.parent_profile != "hermes":
                    _direct_send = True
                    logger.info(
                        "Direct send from hermes to %s (skipping intermediate chain hops)",
                        to,
                    )
            except Exception:
                pass  # fall through to normal chain path

        if _direct_send:
            # Skip chain hops — fall through to raw IPC below
            pass
        else:
            try:
                orchestrator = _get_chain_orchestrator()
                chain = orchestrator.create_chain(task=message, originator=from_profile)
                chain_extra: dict = {}
                if from_profile == "hermes":
                    chain_extra["user_talk"] = True
                hops = orchestrator.delegate_down_chain(
                    chain=chain,
                    target_profile=to,
                    priority=priority_enum,
                    extra_payload=chain_extra or None,
                )

                # Auto-activate every profile in the chain so their
                # gateways start and they actually process the task.
                try:
                    activator = _get_profile_activator()
                    for hop in hops:
                        activator.activate_profile(hop.to_profile)
                except Exception as act_err:
                    logger.warning("Profile activation failed: %s", act_err)

                hop_summary = [
                    {"from": h.from_profile, "to": h.to_profile, "message_id": h.message_id}
                    for h in hops
                ]
                result = {
                    "chain_id": chain.chain_id,
                    "from": from_profile,
                    "to": to,
                    "priority": priority,
                    "status": "delegated",
                    "hops": hop_summary,
                    "hop_count": len(hops),
                }
                return json.dumps(result)
            except Exception as e:
                # If chain delegation fails (e.g. not a subordinate), fall back
                # to raw IPC with a warning so the message is still delivered.
                logger.warning(
                    "Chain-tracked delegation failed (%s), falling back to raw IPC", e
                )
                # Fall through to the raw IPC send below

    # Send message via IPC bus
    # Tag as user_talk when sent from the root profile (hermes) — any message
    # originating from hermes is user-initiated, so responses should be
    # delivered back to the user via the delivery hook.
    try:
        bus = _get_message_bus()
        ipc_payload: dict = {"message": message, "task": message}
        if from_profile == "hermes":
            ipc_payload["user_talk"] = True
        message_id = bus.send(
            from_profile=from_profile,
            to_profile=to,
            message_type=MessageType.TASK_REQUEST,
            payload=ipc_payload,
            priority=priority_enum,
        )
    except Exception as e:
        return json.dumps({"error": f"Failed to send message: {e}"})

    # Auto-activate the target profile's gateway
    try:
        activator = _get_profile_activator()
        activator.activate_profile(to)
    except Exception as act_err:
        logger.warning("Profile activation failed for '%s': %s", to, act_err)

    result = {
        "message_id": message_id,
        "from": from_profile,
        "to": to,
        "priority": priority,
        "status": "sent",
    }

    # --- Async cron-based delivery (deliver_to parameter) ---
    # When deliver_to is set, schedule a one-shot cron job that runs the target
    # profile's full context + task and delivers the result to the destination.
    # This works without parent_agent context and is the preferred async path.
    if deliver_to:
        try:
            # Build rich context for the target profile
            profile_context = _build_profile_context(to)

            # Compose the full self-contained prompt for the cron agent
            cron_prompt = (
                f"{profile_context}\n\n"
                f"=== INCOMING TASK (from {from_profile}) ===\n"
                f"{message}\n\n"
                f"Please complete the task above. Your response will be delivered to: {deliver_to}"
            )

            # Import and call the Hermes cron API directly
            import sys as _sys
            from pathlib import Path as _Path
            _hermes_agent = str(_Path.home() / ".hermes" / "hermes-agent")
            if _hermes_agent not in _sys.path:
                _sys.path.insert(0, _hermes_agent)

            from cron.jobs import create_job
            job = create_job(
                prompt=cron_prompt,
                schedule="1m",        # Run as soon as possible (next 1-minute tick)
                name=f"profile-{to}-task-{message_id[:8]}",
                repeat=1,             # One-shot
                deliver=deliver_to,
            )

            result["status"] = "scheduled"
            result["deliver_to"] = deliver_to
            result["cron_job_id"] = job["id"]
            result["cron_job_name"] = job["name"]
            result["scheduled_at"] = job.get("next_run_at")
            result["note"] = (
                f"Task scheduled as one-shot cron job '{job['name']}' "
                f"(id={job['id']}). Result will be delivered to: {deliver_to}"
            )

        except Exception as e:
            result["warning"] = f"Cron scheduling failed: {e}. Message queued in IPC bus only."
            logger.warning("Failed to schedule cron job for deliver_to=%s: %s", deliver_to, e)

        return json.dumps(result)

    # --- Synchronous subagent spawning (wait_for_response=True) ---
    # Requires parent_agent context injected by the Hermes tool handler chain.
    if wait_for_response:
        parent_agent = kwargs.get("parent_agent")
        if parent_agent is None:
            result["warning"] = (
                "wait_for_response=True requires parent_agent context. "
                "Message sent asynchronously instead. "
                "Tip: use deliver_to='telegram' for async cron-based delivery."
            )
            return json.dumps(result)

        # Build context from target profile's SOUL.md + memory + IPC
        context = _build_profile_context(to)

        # Use Hermes delegate_task to spawn the subagent
        try:
            from tools.delegate_tool import delegate_task
            delegation_result = delegate_task(
                goal=message,
                context=context,
                parent_agent=parent_agent,
            )

            # Parse the delegation result
            delegation_data = json.loads(delegation_result)
            results = delegation_data.get("results", [])

            if results and results[0].get("status") == "completed":
                response_text = results[0].get("summary", "(no response)")

                # Record the response in IPC
                try:
                    bus.send(
                        from_profile=to,
                        to_profile=from_profile,
                        message_type=MessageType.TASK_RESPONSE,
                        payload={"response": response_text},
                        priority=priority_enum,
                        correlation_id=message_id,
                    )
                except Exception as e:
                    logger.warning("Failed to record response in IPC: %s", e)

                result["response"] = response_text
                result["status"] = "completed"
            else:
                error = results[0].get("error", "Unknown error") if results else "No results"
                result["error"] = f"Subagent failed: {error}"
                result["status"] = "failed"

        except ImportError:
            result["warning"] = (
                "delegate_task not available. Message sent asynchronously. "
                "Install Hermes agent to use synchronous delegation."
            )
        except Exception as e:
            result["error"] = f"Delegation failed: {e}"
            result["status"] = "failed"

    return json.dumps(result)


def check_inbox(args: dict, **kwargs) -> str:
    """Check pending IPC messages for a profile."""
    profile = args.get("profile", "").strip() or _get_current_profile()

    try:
        bus = _get_message_bus()
        pending = bus.poll(profile, limit=50)

        messages = []
        for msg in pending:
            messages.append({
                "message_id": msg.message_id,
                "from": msg.from_profile,
                "type": msg.message_type.value,
                "priority": msg.priority.value,
                "payload": msg.payload,
                "created_at": msg.created_at.isoformat(),
            })
            bus.acknowledge(msg.message_id)

        return json.dumps({
            "profile": profile,
            "pending_count": len(messages),
            "messages": messages,
        })

    except Exception as e:
        return json.dumps({"error": f"Failed to check inbox: {e}"})


def org_chart_tool(args: dict, **kwargs) -> str:
    """Display the organizational hierarchy as a tree."""
    try:
        from core.registry.org_chart import render_org_chart
        registry = _get_profile_registry()
        chart = render_org_chart(registry, show_status=True, active_only=False)
        return json.dumps({"org_chart": chart})
    except Exception as e:
        return json.dumps({"error": f"Failed to render org chart: {e}"})


def profile_status(args: dict, **kwargs) -> str:
    """Get detailed status for a profile: memory stats, worker counts, pending messages."""
    profile = args.get("profile", "").strip()
    if not profile:
        return json.dumps({"error": "Parameter 'profile' is required."})

    result: Dict[str, Any] = {"profile": profile}

    # Profile info
    try:
        reg = _get_profile_registry()
        p = reg.get_profile(profile)
        result["info"] = {
            "display_name": p.display_name,
            "role": p.role,
            "status": p.status,
            "parent": p.parent_profile,
            "department": p.department,
        }

        # Direct reports
        reports = reg.list_reports(profile)
        result["direct_reports"] = [
            {"name": r.profile_name, "role": r.role, "status": r.status}
            for r in reports
        ]
    except Exception as e:
        result["info_error"] = str(e)

    # Memory stats
    mem_store = _get_memory_store(profile)
    if mem_store is not None:
        try:
            from core.memory.models import MemoryTier
            all_entries = mem_store.list_entries(limit=1000)
            tier_counts = {}
            for tier in MemoryTier:
                count = sum(1 for e in all_entries if e.tier == tier)
                if count > 0:
                    tier_counts[tier.value] = count

            result["memory"] = {
                "total_entries": len(all_entries),
                "tier_breakdown": tier_counts,
                "total_bytes": sum(e.byte_size for e in all_entries),
            }
        except Exception as e:
            result["memory_error"] = str(e)
    else:
        result["memory"] = {"status": "no memory database found"}

    # Pending IPC messages
    try:
        bus = _get_message_bus()
        pending_count = bus.get_pending_count(profile)
        result["pending_messages"] = pending_count
    except Exception as e:
        result["ipc_error"] = str(e)

    # Worker stats (only for project_manager profiles)
    try:
        reg = _get_profile_registry()
        p = reg.get_profile(profile)
        if p.role == "project_manager":
            sub_reg = _get_subagent_registry()
            workers = sub_reg.list(project_manager=profile, limit=100)
            status_counts = {}
            for w in workers:
                status_counts[w.status] = status_counts.get(w.status, 0) + 1
            result["workers"] = {
                "total": len(workers),
                "by_status": status_counts,
            }
    except Exception as e:
        result["worker_error"] = str(e)

    return json.dumps(result)


def spawn_tracked_worker(args: dict, **kwargs) -> str:
    """Spawn a subagent AND register it in the SubagentRegistry for tracking."""
    task = args.get("task", "").strip()
    model = args.get("model")

    if not task:
        return json.dumps({"error": "Parameter 'task' (task description) is required."})

    parent_agent = kwargs.get("parent_agent")
    if parent_agent is None:
        return json.dumps({"error": "spawn_tracked_worker requires parent_agent context."})

    pm_profile = _get_current_profile()

    # Enforce onboarding gate: a PM in onboarding status cannot spawn workers.
    try:
        reg = _get_profile_registry()
        reg.assert_profile_active(pm_profile)
    except Exception as e:
        error_msg = str(e)
        if "onboarding" in error_msg.lower():
            return json.dumps({
                "error": error_msg,
                "hint": (
                    "Your profile is still in onboarding status. "
                    "Ask your parent PM to run 'submit_onboarding_brief' with "
                    "role_definition, scope, success_criteria, and handoff_protocol "
                    "to activate your profile before spawning workers."
                ),
            })
        # For other errors (suspended, archived), still block.
        return json.dumps({"error": f"Profile not eligible to spawn workers: {e}"})

    # Register in SubagentRegistry
    try:
        sub_reg = _get_subagent_registry()
        subagent = sub_reg.register(
            project_manager=pm_profile,
            task_goal=task,
        )
        subagent_id = subagent.subagent_id
    except Exception as e:
        return json.dumps({"error": f"Failed to register worker: {e}"})

    # Spawn via delegate_task
    try:
        from tools.delegate_tool import delegate_task

        delegate_kwargs = {
            "goal": task,
            "context": f"You are a worker for project manager '{pm_profile}'. Worker ID: {subagent_id}",
            "parent_agent": parent_agent,
        }

        delegation_result = delegate_task(**delegate_kwargs)
        delegation_data = json.loads(delegation_result)
        results = delegation_data.get("results", [])

        if results and results[0].get("status") == "completed":
            summary = results[0].get("summary", "(no summary)")
            token_info = results[0].get("tokens", {})
            total_tokens = token_info.get("input", 0) + token_info.get("output", 0)

            # Update registry with completion
            try:
                sub_reg.complete(
                    subagent_id,
                    result_summary=summary,
                    token_cost=total_tokens if total_tokens > 0 else None,
                    project_manager=pm_profile,
                )
            except Exception as e:
                logger.warning("Failed to update worker completion: %s", e)

            return json.dumps({
                "subagent_id": subagent_id,
                "status": "completed",
                "summary": summary,
                "tokens": total_tokens,
            })
        else:
            error = results[0].get("error", "Unknown error") if results else "No results"

            # Mark as failed in registry
            try:
                from core.workers.models import SubagentStatus
                sub_reg.update_status(subagent_id, SubagentStatus.FAILED, project_manager=pm_profile)
            except Exception:
                pass

            return json.dumps({
                "subagent_id": subagent_id,
                "status": "failed",
                "error": error,
            })

    except ImportError:
        return json.dumps({
            "subagent_id": subagent_id,
            "error": "delegate_task not available. Worker registered but not spawned.",
            "status": "registered_only",
        })
    except Exception as e:
        return json.dumps({
            "subagent_id": subagent_id,
            "error": f"Delegation failed: {e}",
            "status": "failed",
        })


def get_project_status(args: dict, **kwargs) -> str:
    """Get worker status and recent completions for a PM profile."""
    pm = args.get("pm", "").strip()
    if not pm:
        return json.dumps({"error": "Parameter 'pm' (project manager profile) is required."})

    try:
        sub_reg = _get_subagent_registry()
        workers = sub_reg.list(project_manager=pm, limit=100)

        status_counts: Dict[str, int] = {}
        recent_completions: List[Dict[str, Any]] = []
        running_workers: List[Dict[str, Any]] = []

        for w in workers:
            status_counts[w.status] = status_counts.get(w.status, 0) + 1

            if w.status == "completed":
                recent_completions.append({
                    "subagent_id": w.subagent_id,
                    "task": w.task_goal[:100],
                    "summary": (w.result_summary or "")[:200],
                    "completed_at": w.updated_at.isoformat(),
                    "tokens": w.token_cost,
                })
            elif w.status == "running":
                running_workers.append({
                    "subagent_id": w.subagent_id,
                    "task": w.task_goal[:100],
                    "started_at": w.created_at.isoformat(),
                })

        # Sort completions by date (most recent first)
        recent_completions.sort(
            key=lambda x: x.get("completed_at", ""), reverse=True
        )

        return json.dumps({
            "pm": pm,
            "total_workers": len(workers),
            "by_status": status_counts,
            "running": running_workers,
            "recent_completions": recent_completions[:10],  # Last 10
        })

    except Exception as e:
        return json.dumps({"error": f"Failed to get project status: {e}"})


def share_knowledge(args: dict, **kwargs) -> str:
    """Share a finding, decision, or learning to the shared knowledge base."""
    title = args.get("title", "").strip()
    content = args.get("content", "").strip()
    category = args.get("category", "").strip()
    tags = args.get("tags", [])
    source_context = args.get("source_context", "").strip() or ""

    if not title:
        return json.dumps({"error": "Parameter 'title' is required."})
    if not content:
        return json.dumps({"error": "Parameter 'content' is required."})
    if not category:
        return json.dumps({"error": "Parameter 'category' is required."})

    from_profile = _get_current_profile()

    try:
        from core.memory.models import KnowledgeEntry, generate_knowledge_id
        kb = _get_knowledge_base(from_profile)

        entry = KnowledgeEntry(
            entry_id=generate_knowledge_id(),
            profile_name=from_profile,
            category=category,
            title=title,
            content=content,
            source_profile=from_profile,
            source_context=source_context,
            tags=tags if isinstance(tags, list) else [],
        )
        stored = kb.add_knowledge(entry)

        return json.dumps({
            "status": "shared",
            "entry_id": stored.entry_id,
            "profile_name": stored.profile_name,
            "category": stored.category,
            "title": stored.title,
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to share knowledge: {e}"})


def search_knowledge_tool(args: dict, **kwargs) -> str:
    """Search the shared knowledge base across all profiles."""
    query = args.get("query", "").strip()
    category = args.get("category") or None
    tags = args.get("tags") or None
    source = args.get("source") or None
    limit = args.get("limit", 20)

    if not query and not category and not source:
        return json.dumps({"error": "At least one of 'query', 'category', or 'source' is required."})

    from_profile = _get_current_profile()

    try:
        kb = _get_knowledge_base(from_profile)
        entries = kb.search_all_profiles(
            query=query or "",
            category=category,
            tags=tags,
            source_profile=source,
            limit=limit,
        )

        return json.dumps({
            "query": query,
            "result_count": len(entries),
            "entries": [e.to_dict() for e in entries],
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to search knowledge: {e}"})


def read_ancestor_memory(args: dict, **kwargs) -> str:
    """Read memory entries from an ancestor profile in the chain of command."""
    from core.memory.models import MemoryEntryType as _MET, MemoryTier as _MT

    ancestor = args.get("ancestor", "").strip()
    query = args.get("query", "").strip() or None
    entry_type_str = args.get("entry_type") or None
    tier_str = args.get("tier") or None
    limit = args.get("limit", 10)

    entry_type = _MET(entry_type_str) if entry_type_str else None
    tier = _MT(tier_str) if tier_str else None

    if not ancestor:
        return json.dumps({"error": "Parameter 'ancestor' is required."})

    from_profile = _get_current_profile()

    try:
        reg = _get_profile_registry()
        chain = reg.get_chain_of_command(from_profile)
        chain_names = [p.profile_name for p in chain]

        if ancestor not in chain_names:
            return json.dumps({
                "error": (
                    f"Profile '{ancestor}' is not in your chain of command. "
                    f"You can only read memory from ancestors: {chain_names}"
                ),
            })

        mem_store = _get_memory_store(ancestor)
        if mem_store is None:
            return json.dumps({
                "ancestor": ancestor,
                "entries": [],
                "note": f"No memory database found for '{ancestor}'",
            })

        if query:
            entries = mem_store.search(
                query=query,
                entry_type=entry_type,
                tier=tier,
                limit=limit,
            )
        else:
            entries = mem_store.list_entries(
                entry_type=entry_type,
                tier=tier,
                limit=limit,
            )

        return json.dumps({
            "ancestor": ancestor,
            "from_profile": from_profile,
            "chain_of_command": chain_names,
            "result_count": len(entries),
            "entries": [e.to_dict() for e in entries],
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to read ancestor memory: {e}"})


def get_chain_context(args: dict, **kwargs) -> str:
    """Get contextual knowledge and decisions from the chain of command."""
    from core.memory.models import MemoryTier as _MT

    topic = args.get("topic", "").strip() or None
    include_memory = args.get("include_memory", True)
    include_knowledge = args.get("include_knowledge", True)

    from_profile = _get_current_profile()

    try:
        reg = _get_profile_registry()
        chain = reg.get_chain_of_command(from_profile)
        chain_names = [p.profile_name for p in chain]

        result: Dict[str, Any] = {
            "profile": from_profile,
            "chain_of_command": chain_names,
        }

        # Ancestor memory (hot tier, 5 per ancestor, excluding self)
        if include_memory:
            ancestor_memory: Dict[str, List[Dict[str, Any]]] = {}
            for profile in chain:
                if profile.profile_name == from_profile:
                    continue
                mem_store = _get_memory_store(profile.profile_name)
                if mem_store is None:
                    continue
                if topic:
                    entries = mem_store.search(query=topic, tier=_MT.hot, limit=5)
                else:
                    entries = mem_store.list_entries(tier=_MT.hot, limit=5)
                if entries:
                    ancestor_memory[profile.profile_name] = [
                        {"entry_type": e.entry_type, "content": e.content, "tier": e.tier}
                        for e in entries
                    ]
            result["ancestor_memory"] = ancestor_memory

        # Shared knowledge base
        if include_knowledge:
            kb = _get_knowledge_base(from_profile)
            kb_entries = kb.search_all_profiles(
                query=topic or "",
                limit=15,
            )
            result["shared_knowledge"] = [
                {
                    "title": e.title,
                    "content": e.content,
                    "category": e.category,
                    "source_profile": e.source_profile,
                    "tags": e.tags,
                }
                for e in kb_entries
            ]

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"Failed to get chain context: {e}"})


def save_memory(args: dict, **kwargs) -> str:
    """Save a memory entry to the calling agent's personal memory store."""
    content = args.get("content", "").strip()
    entry_type = args.get("entry_type", "learning").strip()
    metadata = args.get("metadata") or {}

    if not content:
        return json.dumps({"error": "Parameter 'content' is required."})

    valid_types = ["preference", "decision", "learning", "context", "summary", "artifact"]
    if entry_type not in valid_types:
        return json.dumps({"error": f"Invalid entry_type '{entry_type}'. Use: {valid_types}"})

    from_profile = _get_current_profile()

    try:
        from core.memory.models import (
            MemoryEntry, MemoryEntryType, MemoryScope, MemoryTier,
            ROLE_SCOPE_MAP, generate_memory_id,
        )

        mem_store = _get_memory_store(from_profile)
        if mem_store is None:
            return json.dumps({"error": f"No memory store available for '{from_profile}'. Memory DB may not exist yet."})

        entry = MemoryEntry(
            entry_id=generate_memory_id(),
            profile_name=from_profile,
            scope=mem_store.profile_scope,
            tier=MemoryTier.hot,
            entry_type=MemoryEntryType(entry_type),
            content=content,
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        stored = mem_store.store(entry)

        return json.dumps({
            "status": "saved",
            "entry_id": stored.entry_id,
            "profile_name": from_profile,
            "scope": stored.scope.value if hasattr(stored.scope, 'value') else str(stored.scope),
            "tier": "hot",
            "entry_type": entry_type,
            "byte_size": stored.byte_size,
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to save memory: {e}"})


def create_profile_tool(args: dict, **kwargs) -> str:
    """Create a new profile in the hierarchy.

    Supports all roles: ceo, department_head, project_manager, specialist.
    Validates hierarchy constraints (e.g. specialists must be under a PM).
    """
    name = args.get("name", "").strip()
    role = args.get("role", "").strip()
    parent = args.get("parent", "").strip() or None
    display_name = args.get("display_name", "").strip() or None
    department = args.get("department", "").strip() or None
    description = args.get("description", "").strip() or None

    if not name:
        return json.dumps({"error": "Parameter 'name' is required."})
    if not role:
        return json.dumps({"error": "Parameter 'role' is required."})

    try:
        reg = _get_profile_registry()
        profile = reg.create_profile(
            name=name,
            display_name=display_name,
            role=role,
            parent=parent,
            department=department,
            description=description,
        )

        result: Dict[str, Any] = {
            "profile_name": profile.profile_name,
            "display_name": profile.display_name,
            "role": profile.role,
            "parent": profile.parent_profile,
            "department": profile.department,
            "status": profile.status,
        }

        # If the profile is in onboarding, provide clear next-step instructions.
        if profile.status == "onboarding":
            result["status"] = "onboarding"
            result["next_step"] = (
                f"Profile '{name}' created in ONBOARDING status. "
                "It is not yet active and cannot spawn workers. "
                "As the parent PM, you MUST now run 'submit_onboarding_brief' "
                f"for profile '{name}' with: role_definition, scope, "
                "success_criteria, and handoff_protocol. "
                "This will activate the profile and log the discovery answers."
            )
            result["required_action"] = "submit_onboarding_brief"
            result["onboarding_fields_required"] = [
                "role_definition",
                "scope",
                "success_criteria",
                "handoff_protocol",
            ]
        else:
            result["status"] = "created"

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"Failed to create profile: {e}"})


def submit_onboarding_brief_tool(args: dict, **kwargs) -> str:
    """Submit a discovery/onboarding brief for a newly created profile.

    The calling agent should be the parent PM of the profile being onboarded.
    Submitting a complete brief activates the profile (onboarding → active)
    and logs all answers in the onboarding_briefs table for future reference.
    """
    profile_name = args.get("profile_name", "").strip()
    role_definition = args.get("role_definition", "").strip()
    scope = args.get("scope", "").strip()
    success_criteria = args.get("success_criteria", "").strip()
    handoff_protocol = args.get("handoff_protocol", "").strip()
    discovery_answers = args.get("discovery_answers", "").strip()
    dependencies = args.get("dependencies", "").strip()
    first_task = args.get("first_task", "").strip()
    extra = args.get("extra") or {}

    if not profile_name:
        return json.dumps({"error": "Parameter 'profile_name' is required."})

    from_profile = _get_current_profile()

    missing = []
    if not role_definition:
        missing.append("role_definition")
    if not scope:
        missing.append("scope")
    if not success_criteria:
        missing.append("success_criteria")
    if not handoff_protocol:
        missing.append("handoff_protocol")
    if missing:
        return json.dumps({
            "error": f"Missing required onboarding fields: {missing}",
            "hint": (
                "All four fields are required to activate the profile: "
                "role_definition (what they do), scope (what is/isn't in scope), "
                "success_criteria (measurable outcomes), "
                "handoff_protocol (how work is returned upstream)."
            ),
        })

    try:
        reg = _get_profile_registry()
        brief = reg.submit_onboarding_brief(
            profile_name=profile_name,
            parent_pm=from_profile,
            role_definition=role_definition,
            scope=scope,
            success_criteria=success_criteria,
            handoff_protocol=handoff_protocol,
            discovery_answers=discovery_answers,
            dependencies=dependencies,
            first_task=first_task,
            extra=extra if isinstance(extra, dict) else {},
        )
        activated_profile = reg.get_profile(profile_name)
        return json.dumps({
            "status": "onboarding_complete",
            "profile_name": profile_name,
            "profile_status": activated_profile.status,
            "parent_pm": from_profile,
            "brief_submitted_at": brief.submitted_at.isoformat(),
            "brief": brief.to_dict(),
            "message": (
                f"Profile '{profile_name}' onboarded successfully. "
                f"Status is now '{activated_profile.status}'. "
                "The discovery brief has been logged."
            ),
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to submit onboarding brief: {e}"})


def get_onboarding_status_tool(args: dict, **kwargs) -> str:
    """Get the onboarding status of a profile.

    Returns whether the profile is in onboarding or active status,
    who is pending to provide the brief, and the brief if it exists.
    """
    profile_name = args.get("profile_name", "").strip()

    if not profile_name:
        # Default: list all profiles currently in onboarding status
        try:
            reg = _get_profile_registry()
            pending = reg.list_onboarding_pending()
            return json.dumps({
                "pending_onboarding_count": len(pending),
                "pending_profiles": [
                    {
                        "profile_name": p.profile_name,
                        "display_name": p.display_name,
                        "role": p.role,
                        "parent": p.parent_profile,
                        "created_at": p.created_at.isoformat(),
                    }
                    for p in pending
                ],
                "action_required": (
                    "Each profile in onboarding needs its parent PM to run "
                    "'submit_onboarding_brief' with role_definition, scope, "
                    "success_criteria, and handoff_protocol."
                ) if pending else "No profiles pending onboarding.",
            })
        except Exception as e:
            return json.dumps({"error": f"Failed to list onboarding profiles: {e}"})

    try:
        reg = _get_profile_registry()
        profile = reg.get_profile(profile_name)
        result: Dict[str, Any] = {
            "profile_name": profile_name,
            "status": profile.status,
            "role": profile.role,
            "parent": profile.parent_profile,
        }

        if profile.status == "onboarding":
            result["onboarding_complete"] = False
            result["action_required"] = (
                f"Parent PM '{profile.parent_profile}' must run 'submit_onboarding_brief' "
                f"for '{profile_name}' with: role_definition, scope, "
                "success_criteria, handoff_protocol."
            )
        else:
            result["onboarding_complete"] = True
            # Try to load the brief
            try:
                brief = reg.get_onboarding_brief(profile_name)
                result["brief"] = brief.to_dict()
            except Exception:
                result["brief"] = None
                result["note"] = "No onboarding brief on record (profile may have been activated without one)."

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"Failed to get onboarding status: {e}"})


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------


def check_hierarchy_requirements() -> bool:
    """Check if the hierarchy system is available.

    Verifies the project is importable and the registry DB exists.
    """
    try:
        db_path = _DB_BASE_DIR / "registry.db"
        if not db_path.exists():
            return False

        # Quick import check
        from core.registry.profile_registry import ProfileRegistry  # noqa: F401
        return True
    except ImportError:
        return False
    except Exception:
        return False


# =============================================================================
# OpenAI Function-Calling Schemas
# =============================================================================

SEND_TO_PROFILE_SCHEMA = {
    "name": "send_to_profile",
    "description": (
        "Send a message to another profile in the organizational hierarchy via IPC. "
        "Use this to communicate between agents (CEO, CTO, PMs, etc.).\n\n"
        "If deliver_to is set (e.g., 'telegram', 'origin', 'local'), a one-shot Hermes "
        "cron job is scheduled immediately. The target profile is spawned with their full "
        "context (SOUL.md, scoped memory, pending messages) and their response is "
        "delivered to the specified destination. This is the RECOMMENDED async path — "
        "it works from any context without requiring parent_agent.\n\n"
        "If wait_for_response=true (legacy), the target profile is spawned as a subagent "
        "synchronously. Requires parent_agent context from the Hermes tool handler chain; "
        "falls back to async IPC if unavailable.\n\n"
        "If neither deliver_to nor wait_for_response is set, the message is queued in "
        "the IPC bus for the target to pick up later (fire-and-forget)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Target profile name (e.g., 'cto', 'pm-hier-arch', 'hermes')",
            },
            "message": {
                "type": "string",
                "description": "Message content / task to send to the target profile",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "urgent"],
                "description": "Message priority. Default: normal.",
            },
            "deliver_to": {
                "type": "string",
                "description": (
                    "Delivery target for the async cron job response. "
                    "Examples: 'telegram', 'origin', 'local', "
                    "'telegram:-1001234567890:17585'. "
                    "When set, a one-shot cron job is scheduled immediately — "
                    "the target profile runs autonomously and its response is "
                    "delivered here. Preferred over wait_for_response."
                ),
            },
            "wait_for_response": {
                "type": "boolean",
                "description": (
                    "Legacy synchronous mode. If true, spawn the target profile as a "
                    "subagent and wait for their response. Requires parent_agent context "
                    "in the Hermes tool handler chain; falls back to async if unavailable. "
                    "Use deliver_to instead for reliable async execution."
                ),
            },
            "track": {
                "type": "boolean",
                "description": (
                    "When true, create a DelegationChain and delegate through the "
                    "hierarchy via ChainOrchestrator instead of raw IPC. The response "
                    "includes a chain_id that downstream agents can pass to "
                    "'worker_wrapper.py spawn --chain-id' for automatic result "
                    "propagation back up the hierarchy. Falls back to raw IPC if "
                    "the target is not a subordinate in the hierarchy."
                ),
            },
            "direct": {
                "type": "boolean",
                "description": (
                    "Set to true ONLY when the owner explicitly names a specialist "
                    "agent (dev-*, sec-*) as the target. Without this flag, sends "
                    "from hermes to specialists are blocked — route through their "
                    "parent PM instead."
                ),
            },
        },
        "required": ["to", "message"],
    },
}

CHECK_INBOX_SCHEMA = {
    "name": "check_inbox",
    "description": (
        "Check pending IPC messages for a profile. Shows unread messages from other "
        "profiles in the hierarchy. Defaults to the current profile's inbox."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile": {
                "type": "string",
                "description": "Profile name to check inbox for. Defaults to current profile.",
            },
        },
        "required": [],
    },
}

ORG_CHART_SCHEMA = {
    "name": "org_chart",
    "description": (
        "Display the organizational hierarchy as a tree. Shows all profiles, "
        "their roles, and reporting relationships. Use this to understand "
        "who reports to whom and the overall team structure."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

PROFILE_STATUS_SCHEMA = {
    "name": "profile_status",
    "description": (
        "Get detailed status for a profile in the hierarchy. Returns:\n"
        "- Profile info (role, status, department, parent)\n"
        "- Direct reports list\n"
        "- Scoped memory statistics (entry counts, tier breakdown, bytes)\n"
        "- Pending IPC message count\n"
        "- Worker statistics (for project manager profiles)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile": {
                "type": "string",
                "description": "Profile name to query status for",
            },
        },
        "required": ["profile"],
    },
}

SPAWN_TRACKED_WORKER_SCHEMA = {
    "name": "spawn_tracked_worker",
    "description": (
        "Spawn a worker subagent AND register it in the SubagentRegistry for tracking. "
        "The worker is automatically tracked with status (running/completed/failed), "
        "token costs, and result summaries. Use this instead of raw delegate_task "
        "when you want persistent worker tracking.\n\n"
        "Only available to project manager profiles."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Task description for the worker subagent",
            },
            "model": {
                "type": "string",
                "description": "Optional model override (e.g., 'claude-sonnet-4-20250514')",
            },
        },
        "required": ["task"],
    },
}

GET_PROJECT_STATUS_SCHEMA = {
    "name": "get_project_status",
    "description": (
        "Get worker status and recent completions for a project manager. Shows:\n"
        "- Total workers and status breakdown (running, completed, failed)\n"
        "- Currently running workers with task descriptions\n"
        "- Recent completions with summaries and token costs\n\n"
        "Use this to track the progress of delegated work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pm": {
                "type": "string",
                "description": "Project manager profile name to get status for",
            },
        },
        "required": ["pm"],
    },
}


SHARE_KNOWLEDGE_SCHEMA = {
    "name": "share_knowledge",
    "description": (
        "Share a finding, decision, or learning to the shared knowledge base. "
        "Other agents in the hierarchy can search and retrieve this knowledge. "
        "Use this to record important decisions, architectural choices, "
        "lessons learned, or any information that other agents should know."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title for the knowledge entry",
            },
            "content": {
                "type": "string",
                "description": "Full content of the finding, decision, or learning",
            },
            "category": {
                "type": "string",
                "description": "Category (e.g., 'architecture', 'decision', 'learning', 'process', 'domain')",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags for filtering and discovery",
            },
            "source_context": {
                "type": "string",
                "description": "Where this knowledge came from (e.g., 'sprint-3 review', 'worker SA-47 result')",
            },
        },
        "required": ["title", "content", "category"],
    },
}

SEARCH_KNOWLEDGE_SCHEMA = {
    "name": "search_knowledge",
    "description": (
        "Search the shared knowledge base across all profiles in the hierarchy. "
        "Find decisions, learnings, and findings from any agent. "
        "Filter by category, tags, or source profile."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search text (matched against title and content)",
            },
            "category": {
                "type": "string",
                "description": "Filter by category (e.g., 'architecture', 'decision')",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by tags — entries must contain ALL listed tags",
            },
            "source": {
                "type": "string",
                "description": "Filter by source profile name (who contributed this knowledge)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results. Default: 20",
            },
        },
        "required": ["query"],
    },
}

READ_ANCESTOR_MEMORY_SCHEMA = {
    "name": "read_ancestor_memory",
    "description": (
        "Read memory entries from a profile in your chain of command. "
        "A specialist can read their PM's project memory, their department "
        "head's domain memory, or the CEO's strategic memory. "
        "Access is restricted to ancestors only — you cannot read sibling "
        "or unrelated profiles' memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ancestor": {
                "type": "string",
                "description": "Profile name of the ancestor to read from (must be in your chain of command)",
            },
            "query": {
                "type": "string",
                "description": "Search text to filter entries. If omitted, returns most recent entries.",
            },
            "entry_type": {
                "type": "string",
                "enum": ["preference", "decision", "learning", "context", "summary", "artifact"],
                "description": "Filter by entry type",
            },
            "tier": {
                "type": "string",
                "enum": ["hot", "warm", "cool", "cold"],
                "description": "Filter by memory tier. 'hot' = most active/recent.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results. Default: 10",
            },
        },
        "required": ["ancestor"],
    },
}

GET_CHAIN_CONTEXT_SCHEMA = {
    "name": "get_chain_context",
    "description": (
        "Get a summary of relevant decisions, knowledge, and context from "
        "your chain of command. Useful when starting a new task to understand "
        "strategic direction (from CEO), domain standards (from dept head), "
        "and project decisions (from PM). Optionally filter by topic."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Focus topic to filter relevant context (e.g., 'API design', 'deployment')",
            },
            "include_memory": {
                "type": "boolean",
                "description": "Include hot-tier memory from ancestor profiles. Default: true",
            },
            "include_knowledge": {
                "type": "boolean",
                "description": "Include entries from the shared knowledge base. Default: true",
            },
        },
        "required": [],
    },
}

SAVE_MEMORY_SCHEMA = {
    "name": "save_memory",
    "description": (
        "Save a memory entry to your own personal memory store. Use this to "
        "persist important decisions, learnings, context, or preferences that "
        "you want to remember across sessions.\n\n"
        "Memory entries start in the 'hot' tier and age over time "
        "(hot → warm → cool → cold). Hot memories are included in your "
        "context when you activate.\n\n"
        "Types:\n"
        "- decision: a choice you made and why\n"
        "- learning: something you discovered or figured out\n"
        "- context: background information about your current situation\n"
        "- preference: how you or the user prefers things done\n"
        "- summary: a condensed summary of completed work\n"
        "- artifact: reference to a file, URL, or resource"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The memory content to save. Be specific and concise.",
            },
            "entry_type": {
                "type": "string",
                "enum": ["decision", "learning", "context", "preference", "summary", "artifact"],
                "description": "Type of memory. Default: learning",
            },
            "metadata": {
                "type": "object",
                "description": "Optional key-value metadata (e.g., {\"related_to\": \"api-design\"})",
            },
        },
        "required": ["content"],
    },
}

SUBMIT_ONBOARDING_BRIEF_SCHEMA = {
    "name": "submit_onboarding_brief",
    "description": (
        "Submit a discovery/onboarding brief for a newly created profile. "
        "This is REQUIRED after creating any new profile. "
        "You (the parent PM) must define the role, scope, success criteria, "
        "and handoff protocol before the profile can become active or spawn workers.\n\n"
        "Submitting a complete brief automatically activates the profile "
        "(transitions it from 'onboarding' → 'active') and logs all "
        "discovery answers for future reference.\n\n"
        "Required fields: role_definition, scope, success_criteria, handoff_protocol.\n"
        "Optional but recommended: discovery_answers, dependencies, first_task."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile_name": {
                "type": "string",
                "description": "Name of the profile being onboarded (must be in 'onboarding' status)",
            },
            "role_definition": {
                "type": "string",
                "description": (
                    "Clear statement of what this agent does. "
                    "E.g., 'Manages all frontend development tasks including React components, "
                    "CSS, and integration tests.'"
                ),
            },
            "scope": {
                "type": "string",
                "description": (
                    "What IS and IS NOT in scope for this agent. "
                    "E.g., 'In scope: React/JS/CSS. Out of scope: backend APIs, infrastructure, DB schema.'"
                ),
            },
            "success_criteria": {
                "type": "string",
                "description": (
                    "Measurable definition of success. "
                    "E.g., 'Deliverables are PRs with passing CI. "
                    "Tasks complete within agreed timeframe. Code reviewed before merge.'"
                ),
            },
            "handoff_protocol": {
                "type": "string",
                "description": (
                    "How finished work is returned upstream. "
                    "E.g., 'Send completion summary via send_to_profile to parent PM with "
                    "PR link, test results, and any blockers identified.'"
                ),
            },
            "discovery_answers": {
                "type": "string",
                "description": (
                    "Free-form answers from the discovery interview. "
                    "E.g., 'Q: Any blockers? A: Need access to staging env. "
                    "Q: Preferred stack? A: React 18, TypeScript.'"
                ),
            },
            "dependencies": {
                "type": "string",
                "description": (
                    "Other profiles or systems this agent depends on. "
                    "E.g., 'Depends on pm-backend for API contracts, ci-agent for test runs.'"
                ),
            },
            "first_task": {
                "type": "string",
                "description": (
                    "Concrete first task to confirm the agent is ready. "
                    "E.g., 'Implement the login form component per the Figma spec in /designs/auth.fig'"
                ),
            },
            "extra": {
                "type": "object",
                "description": "Any additional context to store with the brief (key-value pairs)",
            },
        },
        "required": ["profile_name", "role_definition", "scope", "success_criteria", "handoff_protocol"],
    },
}

GET_ONBOARDING_STATUS_SCHEMA = {
    "name": "get_onboarding_status",
    "description": (
        "Get the onboarding status for a specific profile, or list all profiles "
        "currently waiting for their onboarding brief to be completed.\n\n"
        "If profile_name is omitted, returns all profiles in 'onboarding' status "
        "with their parent PM info (so you know who needs to act).\n\n"
        "If profile_name is provided, returns the profile's current status and "
        "the full onboarding brief (if one has been submitted)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile_name": {
                "type": "string",
                "description": (
                    "Profile to check. If omitted, lists all onboarding-pending profiles."
                ),
            },
        },
        "required": [],
    },
}

CREATE_PROFILE_SCHEMA = {
    "name": "create_profile",
    "description": (
        "Create a new persistent agent profile in the organizational hierarchy.\n\n"
        "Use this to add new agents at any level:\n"
        "- department_head: reports to CEO (hermes)\n"
        "- project_manager: reports to a department head\n"
        "- specialist: reports to a CEO, department head, or project manager — "
        "a persistent agent dedicated to a specific ongoing responsibility "
        "(unlike disposable workers)\n\n"
        "The new profile gets its own IPC inbox, scoped memory, and can "
        "participate in delegation chains across the org. After creation, "
        "add a SOUL.md at ~/.hermes/profiles/<name>/SOUL.md to give the "
        "agent its identity and instructions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Unique profile name (lowercase letters, digits, and hyphens only). "
                    "E.g., 'api-agent', 'test-specialist', 'pm-new-project'"
                ),
            },
            "role": {
                "type": "string",
                "enum": ["department_head", "project_manager", "specialist"],
                "description": "Role in the hierarchy. Determines parent constraints and memory scope.",
            },
            "parent": {
                "type": "string",
                "description": (
                    "Parent profile name. Must match role constraints: "
                    "department_head → parent must be CEO, "
                    "project_manager → parent must be a department_head, "
                    "specialist → parent must be a CEO, department_head, or project_manager."
                ),
            },
            "display_name": {
                "type": "string",
                "description": "Human-readable display name (e.g., 'API Agent', 'Test Automation Specialist')",
            },
            "department": {
                "type": "string",
                "description": "Department name (e.g., 'engineering', 'marketing')",
            },
            "description": {
                "type": "string",
                "description": "Brief description of the agent's purpose and responsibilities",
            },
        },
        "required": ["name", "role", "parent"],
    },
}


# =============================================================================
# Registry Registration
# =============================================================================

# Try to register with Hermes tool registry if available.
# If not running inside Hermes, tools can still be used directly.

try:
    from tools.registry import registry

    registry.register(
        name="send_to_profile",
        toolset="hierarchy",
        schema=SEND_TO_PROFILE_SCHEMA,
        handler=send_to_profile,
        check_fn=check_hierarchy_requirements,
        emoji="📨",
    )

    registry.register(
        name="check_inbox",
        toolset="hierarchy",
        schema=CHECK_INBOX_SCHEMA,
        handler=check_inbox,
        check_fn=check_hierarchy_requirements,
        emoji="📬",
    )

    registry.register(
        name="org_chart",
        toolset="hierarchy",
        schema=ORG_CHART_SCHEMA,
        handler=org_chart_tool,
        check_fn=check_hierarchy_requirements,
        emoji="🏢",
    )

    registry.register(
        name="profile_status",
        toolset="hierarchy",
        schema=PROFILE_STATUS_SCHEMA,
        handler=profile_status,
        check_fn=check_hierarchy_requirements,
        emoji="📊",
    )

    registry.register(
        name="spawn_tracked_worker",
        toolset="hierarchy",
        schema=SPAWN_TRACKED_WORKER_SCHEMA,
        handler=spawn_tracked_worker,
        check_fn=check_hierarchy_requirements,
        emoji="👷",
    )

    registry.register(
        name="get_project_status",
        toolset="hierarchy",
        schema=GET_PROJECT_STATUS_SCHEMA,
        handler=get_project_status,
        check_fn=check_hierarchy_requirements,
        emoji="📋",
    )

    registry.register(
        name="create_profile",
        toolset="hierarchy",
        schema=CREATE_PROFILE_SCHEMA,
        handler=create_profile_tool,
        check_fn=check_hierarchy_requirements,
        emoji="👤",
    )

    registry.register(
        name="submit_onboarding_brief",
        toolset="hierarchy",
        schema=SUBMIT_ONBOARDING_BRIEF_SCHEMA,
        handler=submit_onboarding_brief_tool,
        check_fn=check_hierarchy_requirements,
        emoji="📋",
    )

    registry.register(
        name="get_onboarding_status",
        toolset="hierarchy",
        schema=GET_ONBOARDING_STATUS_SCHEMA,
        handler=get_onboarding_status_tool,
        check_fn=check_hierarchy_requirements,
        emoji="🔍",
    )

    registry.register(
        name="share_knowledge",
        toolset="hierarchy",
        schema=SHARE_KNOWLEDGE_SCHEMA,
        handler=share_knowledge,
        check_fn=check_hierarchy_requirements,
        emoji="💡",
    )

    registry.register(
        name="search_knowledge",
        toolset="hierarchy",
        schema=SEARCH_KNOWLEDGE_SCHEMA,
        handler=search_knowledge_tool,
        check_fn=check_hierarchy_requirements,
        emoji="🔍",
    )

    registry.register(
        name="read_ancestor_memory",
        toolset="hierarchy",
        schema=READ_ANCESTOR_MEMORY_SCHEMA,
        handler=read_ancestor_memory,
        check_fn=check_hierarchy_requirements,
        emoji="📖",
    )

    registry.register(
        name="get_chain_context",
        toolset="hierarchy",
        schema=GET_CHAIN_CONTEXT_SCHEMA,
        handler=get_chain_context,
        check_fn=check_hierarchy_requirements,
        emoji="🔗",
    )

    registry.register(
        name="save_memory",
        toolset="hierarchy",
        schema=SAVE_MEMORY_SCHEMA,
        handler=save_memory,
        check_fn=check_hierarchy_requirements,
        emoji="🧠",
    )

    logger.info("Hierarchy tools registered successfully (14 tools in 'hierarchy' toolset)")

except ImportError:
    # Not running inside Hermes — tools are still usable directly
    logger.debug("Hermes tool registry not available; hierarchy tools loaded as standalone")
