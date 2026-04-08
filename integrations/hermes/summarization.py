"""SummarizationPipeline — upward information flow in the agent hierarchy.

Implements bottom-up summarization: workers report to PMs, PMs summarize
to department heads, department heads summarize to CEO.  Each level
aggregates data from its subordinates and sends a concise summary to
its parent via the IPC MessageProtocol.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.registry.profile_registry import ProfileRegistry
from core.ipc.message_bus import MessageBus
from core.ipc.protocol import MessageProtocol
from core.ipc.models import MessageType, MessagePriority
from core.memory.memory_store import MemoryStore
from core.memory.models import MemoryScope, ROLE_SCOPE_MAP
from core.workers.subagent_registry import SubagentRegistry
from core.workers.models import SubagentStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HIERARCHY_DIR = Path.home() / ".hermes" / "hierarchy"
REGISTRY_DB = HIERARCHY_DIR / "registry.db"
IPC_DB = HIERARCHY_DIR / "ipc.db"
MEMORY_DIR = HIERARCHY_DIR / "memory"
WORKERS_DIR = HIERARCHY_DIR / "workers"

# Profile-name to MemoryScope overrides (well-known profiles).
_SCOPE_OVERRIDES: dict[str, MemoryScope] = {
    "hermes": MemoryScope.strategic,
    "cto": MemoryScope.domain,
    "pm-hier-arch": MemoryScope.project,
}


# ---------------------------------------------------------------------------
# RegistryAdapter — duck-typed bridge so MessageBus/Protocol can call .get()
# ---------------------------------------------------------------------------


class RegistryAdapter:
    """Thin adapter exposing the subset of ProfileRegistry that MessageBus
    and MessageProtocol expect (duck-typed ``profile_registry`` parameter).
    """

    def __init__(self, registry: ProfileRegistry) -> None:
        self._registry = registry

    def get(self, name: str) -> Any:
        return self._registry.get_profile(name)

    def get_profile(self, name: str) -> Any:
        return self._registry.get_profile(name)

    def get_chain_of_command(self, name: str) -> list:
        return self._registry.get_chain_of_command(name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def _scope_for_profile(profile_name: str, role: str) -> MemoryScope:
    """Determine the MemoryScope for a profile, using overrides or role map."""
    if profile_name in _SCOPE_OVERRIDES:
        return _SCOPE_OVERRIDES[profile_name]
    return ROLE_SCOPE_MAP.get(role, MemoryScope.project)


def _format_datetime(dt: datetime) -> str:
    """Format a datetime into a human-friendly string."""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# SummarizationPipeline
# ---------------------------------------------------------------------------


class SummarizationPipeline:
    """Bottom-up summarization pipeline for the hierarchical agent org.

    Aggregates worker status, messages, and memory stats into concise
    summaries that flow upward from PMs → department heads → CEO.

    Parameters
    ----------
    hierarchy_dir : Path
        Root of the hierarchy data directory (``~/.hermes/hierarchy/``).
    """

    def __init__(self, hierarchy_dir: Path | None = None) -> None:
        self._dir = hierarchy_dir or HIERARCHY_DIR
        self._registry_db = self._dir / "registry.db"
        self._ipc_db = self._dir / "ipc.db"
        self._memory_dir = self._dir / "memory"
        self._workers_dir = self._dir / "workers"

        # Core components
        self._registry = ProfileRegistry(str(self._registry_db))
        self._adapter = RegistryAdapter(self._registry)
        self._bus = MessageBus(
            db_path=str(self._ipc_db),
            profile_registry=self._adapter,
        )
        self._protocol = MessageProtocol(
            bus=self._bus,
            profile_registry=self._adapter,
        )
        self._worker_registry = SubagentRegistry(
            base_path=str(self._workers_dir),
            profile_registry=self._registry,
        )

    # ------------------------------------------------------------------
    # Public: summarize_workers
    # ------------------------------------------------------------------

    def summarize_workers(self, pm_profile: str) -> str:
        """Summarize all workers owned by a project manager.

        Groups workers by status (running, completed, sleeping, archived)
        and includes the result_summary for completed workers.

        Parameters
        ----------
        pm_profile : str
            Profile name of the project manager.

        Returns
        -------
        str
            Formatted text summary of worker status.
        """
        try:
            workers = self._worker_registry.list(project_manager=pm_profile)
        except Exception as exc:
            logger.debug("Could not list workers for %s: %s", pm_profile, exc)
            workers = []

        if not workers:
            return f"No workers registered for {pm_profile}."

        # Group by status
        by_status: dict[str, list] = defaultdict(list)
        for w in workers:
            by_status[w.status].append(w)

        lines: list[str] = [f"Worker Summary for {pm_profile}"]
        lines.append("=" * 40)
        lines.append(f"Total workers: {len(workers)}")
        lines.append("")

        # Order: running, sleeping, completed, archived
        status_order = [
            SubagentStatus.RUNNING.value,
            SubagentStatus.SLEEPING.value,
            SubagentStatus.COMPLETED.value,
            SubagentStatus.ARCHIVED.value,
        ]

        for status in status_order:
            group = by_status.get(status, [])
            if not group:
                continue
            lines.append(f"[{status.upper()}] ({len(group)})")
            for w in group:
                lines.append(f"  - {w.subagent_id}: {w.task_goal}")
                if status == SubagentStatus.COMPLETED.value and w.result_summary:
                    lines.append(f"    Result: {w.result_summary}")
                lines.append(f"    Updated: {_format_datetime(w.updated_at)}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: summarize_messages
    # ------------------------------------------------------------------

    def summarize_messages(self, profile: str, hours: int = 24) -> str:
        """Summarize recent messages for a profile.

        Retrieves messages from the last *hours* hours, groups them by
        sender and message type, and returns a formatted text summary.

        Parameters
        ----------
        profile : str
            The profile to summarize messages for.
        hours : int
            How far back to look (default 24 hours).

        Returns
        -------
        str
            Formatted text summary of messages.
        """
        cutoff = _now_utc() - timedelta(hours=hours)

        # Fetch received messages
        received = self._bus.list_messages(
            profile_name=profile,
            direction="received",
            limit=200,
        )
        # Fetch sent messages
        sent = self._bus.list_messages(
            profile_name=profile,
            direction="sent",
            limit=200,
        )

        # Filter by time
        recent_received = [m for m in received if m.created_at >= cutoff]
        recent_sent = [m for m in sent if m.created_at >= cutoff]

        lines: list[str] = [f"Message Summary for {profile} (last {hours}h)"]
        lines.append("=" * 40)
        lines.append(
            f"Received: {len(recent_received)}  |  Sent: {len(recent_sent)}"
        )
        lines.append("")

        # Group received by from_profile
        if recent_received:
            by_sender: dict[str, list] = defaultdict(list)
            for m in recent_received:
                by_sender[m.from_profile].append(m)

            lines.append("Received Messages:")
            for sender, msgs in sorted(by_sender.items()):
                type_counts: dict[str, int] = defaultdict(int)
                for m in msgs:
                    mtype = (
                        m.message_type.value
                        if isinstance(m.message_type, MessageType)
                        else str(m.message_type)
                    )
                    type_counts[mtype] += 1
                type_str = ", ".join(
                    f"{t}: {c}" for t, c in sorted(type_counts.items())
                )
                lines.append(f"  From {sender}: {len(msgs)} msgs ({type_str})")
            lines.append("")

        # Group sent by to_profile
        if recent_sent:
            by_recipient: dict[str, list] = defaultdict(list)
            for m in recent_sent:
                by_recipient[m.to_profile].append(m)

            lines.append("Sent Messages:")
            for recipient, msgs in sorted(by_recipient.items()):
                type_counts_s: dict[str, int] = defaultdict(int)
                for m in msgs:
                    mtype = (
                        m.message_type.value
                        if isinstance(m.message_type, MessageType)
                        else str(m.message_type)
                    )
                    type_counts_s[mtype] += 1
                type_str = ", ".join(
                    f"{t}: {c}" for t, c in sorted(type_counts_s.items())
                )
                lines.append(
                    f"  To {recipient}: {len(msgs)} msgs ({type_str})"
                )
            lines.append("")

        if not recent_received and not recent_sent:
            lines.append("No messages in this period.")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: summarize_to_parent
    # ------------------------------------------------------------------

    def summarize_to_parent(self, profile: str) -> str:
        """Generate a role-appropriate summary and send it to the parent.

        - **PM**: worker status + recent messages + project status
        - **dept_head**: subordinate PM summaries + department messages + decisions
        - **CEO**: department summaries + org-wide status

        Parameters
        ----------
        profile : str
            The profile generating the summary.

        Returns
        -------
        str
            The ``message_id`` of the summary message sent to the parent.

        Raises
        ------
        ValueError
            If the profile has no parent (e.g. CEO calling this directly).
        """
        prof = self._registry.get_profile(profile)
        parent = prof.parent_profile
        if parent is None:
            raise ValueError(
                f"Profile '{profile}' has no parent to summarize to."
            )

        role = prof.role
        summary_text = self._generate_role_summary(profile, role)

        # Send via IPC protocol
        message_id, _corr_id = self._protocol.send_request(
            from_profile=profile,
            to_profile=parent,
            payload={
                "type": "status_summary",
                "from": profile,
                "role": role,
                "summary": summary_text,
                "generated_at": _now_utc().isoformat(),
            },
            priority=MessagePriority.NORMAL,
        )

        logger.info(
            "Sent summary from %s to %s (msg_id=%s)", profile, parent, message_id
        )
        return message_id

    # ------------------------------------------------------------------
    # Public: generate_daily_report
    # ------------------------------------------------------------------

    def generate_daily_report(self, profile: str) -> str:
        """Generate a comprehensive daily report for a profile.

        Combines message statistics, worker status, memory usage, and
        identified action items into a single markdown-like text document.

        Parameters
        ----------
        profile : str
            The profile to generate a report for.

        Returns
        -------
        str
            Formatted markdown-like report text.
        """
        prof = self._registry.get_profile(profile)
        now = _now_utc()

        lines: list[str] = [
            f"# Daily Report: {prof.display_name} ({profile})",
            f"**Role:** {prof.role}  |  **Department:** {prof.department or 'N/A'}",
            f"**Generated:** {_format_datetime(now)}",
            "",
        ]

        # --- Messages section ---
        lines.append("## Messages (Last 24h)")
        lines.append("")

        received = self._bus.list_messages(
            profile_name=profile, direction="received", limit=500
        )
        sent = self._bus.list_messages(
            profile_name=profile, direction="sent", limit=500
        )
        cutoff = now - timedelta(hours=24)
        recent_received = [m for m in received if m.created_at >= cutoff]
        recent_sent = [m for m in sent if m.created_at >= cutoff]

        lines.append(f"- **Received:** {len(recent_received)}")
        lines.append(f"- **Sent:** {len(recent_sent)}")

        # Pending count
        pending = self._bus.get_pending_count(profile)
        lines.append(f"- **Pending (unread):** {pending}")

        # Escalations received
        escalations = [
            m for m in recent_received
            if (
                m.message_type == MessageType.ESCALATION
                or (isinstance(m.message_type, str) and m.message_type == "escalation")
            )
        ]
        if escalations:
            lines.append(f"- **Escalations received:** {len(escalations)}")
        lines.append("")

        # --- Workers section (for PMs) ---
        if prof.role == "project_manager":
            lines.append("## Worker Status")
            lines.append("")
            worker_summary = self.summarize_workers(profile)
            # Indent the worker summary
            for wl in worker_summary.split("\n"):
                lines.append(f"  {wl}")
            lines.append("")

        # --- Subordinates section (for dept heads / CEO) ---
        if prof.role in ("department_head", "ceo"):
            lines.append("## Subordinate Status")
            lines.append("")
            reports = self._registry.list_reports(profile)
            if reports:
                for r in reports:
                    lines.append(
                        f"- **{r.display_name}** ({r.profile_name}): "
                        f"role={r.role}, status={r.status}"
                    )
            else:
                lines.append("- No direct reports.")
            lines.append("")

        # --- Memory Usage section ---
        lines.append("## Memory Usage")
        lines.append("")
        mem_stats = self._get_memory_stats(profile, prof.role)
        if mem_stats:
            lines.append(
                f"- **Total entries:** {mem_stats.get('total_entries', 0)}"
            )
            lines.append(
                f"- **Total bytes:** {mem_stats.get('total_bytes', 0):,}"
            )
            by_tier = mem_stats.get("by_tier", {})
            if by_tier:
                tier_str = ", ".join(
                    f"{t}: {c}" for t, c in sorted(by_tier.items())
                )
                lines.append(f"- **By tier:** {tier_str}")
        else:
            lines.append("- Memory stats unavailable.")
        lines.append("")

        # --- Action Items section ---
        lines.append("## Action Items")
        lines.append("")
        action_items = self._identify_action_items(
            profile, prof.role, recent_received, pending
        )
        if action_items:
            for item in action_items:
                lines.append(f"- [ ] {item}")
        else:
            lines.append("- No immediate action items.")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public: run_full_pipeline
    # ------------------------------------------------------------------

    def run_full_pipeline(self) -> dict[str, str]:
        """Run the full summarization pipeline bottom-up through the org.

        Execution order:
        1. All project managers summarize to their department heads.
        2. All department heads summarize to the CEO.

        The CEO does not summarize upward (no parent).

        Returns
        -------
        dict[str, str]
            Mapping of ``profile_name`` → ``message_id`` for each summary
            sent.  Profiles that failed are mapped to an error string
            prefixed with ``"ERROR:"``.
        """
        results: dict[str, str] = {}

        # Step 1: PMs summarize upward
        pms = self._registry.list_profiles(role="project_manager")
        for pm in pms:
            if pm.status == "archived" or pm.parent_profile is None:
                continue
            try:
                mid = self.summarize_to_parent(pm.profile_name)
                results[pm.profile_name] = mid
                logger.info("PM %s summarized → %s", pm.profile_name, mid)
            except Exception as exc:
                err = f"ERROR: {exc}"
                results[pm.profile_name] = err
                logger.warning(
                    "Failed to summarize PM %s: %s", pm.profile_name, exc
                )

        # Step 2: Department heads summarize upward
        dept_heads = self._registry.list_profiles(role="department_head")
        for dh in dept_heads:
            if dh.status == "archived" or dh.parent_profile is None:
                continue
            try:
                mid = self.summarize_to_parent(dh.profile_name)
                results[dh.profile_name] = mid
                logger.info("DeptHead %s summarized → %s", dh.profile_name, mid)
            except Exception as exc:
                err = f"ERROR: {exc}"
                results[dh.profile_name] = err
                logger.warning(
                    "Failed to summarize dept head %s: %s", dh.profile_name, exc
                )

        # Step 3: CEO gets a comprehensive daily report (no upward send)
        ceo_profiles = self._registry.list_profiles(role="ceo")
        for ceo in ceo_profiles:
            if ceo.status == "archived":
                continue
            try:
                report = self.generate_daily_report(ceo.profile_name)
                results[ceo.profile_name] = report
                logger.info(
                    "CEO %s daily report generated (%d chars)",
                    ceo.profile_name,
                    len(report),
                )
            except Exception as exc:
                err = f"ERROR: {exc}"
                results[ceo.profile_name] = err
                logger.warning(
                    "Failed to generate CEO report for %s: %s",
                    ceo.profile_name,
                    exc,
                )

        return results

    # ------------------------------------------------------------------
    # Internal: role-specific summary generation
    # ------------------------------------------------------------------

    def _generate_role_summary(self, profile: str, role: str) -> str:
        """Generate a summary appropriate to the profile's role.

        Parameters
        ----------
        profile : str
            The profile name.
        role : str
            The role string (``project_manager``, ``department_head``, ``ceo``).

        Returns
        -------
        str
            Formatted summary text.
        """
        if role == "project_manager":
            return self._pm_summary(profile)
        elif role == "department_head":
            return self._dept_head_summary(profile)
        elif role == "ceo":
            return self._ceo_summary(profile)
        else:
            return self._generic_summary(profile, role)

    def _pm_summary(self, profile: str) -> str:
        """Project manager summary: workers + messages + project status."""
        sections: list[str] = []

        # Worker status
        sections.append(self.summarize_workers(profile))

        # Recent messages
        sections.append(self.summarize_messages(profile, hours=24))

        # Project status overview
        prof = self._registry.get_profile(profile)
        sections.append(
            f"Project Status: {prof.display_name}\n"
            f"  Department: {prof.department or 'N/A'}\n"
            f"  Profile status: {prof.status}"
        )

        return "\n---\n".join(sections)

    def _dept_head_summary(self, profile: str) -> str:
        """Department head summary: PM summaries + dept messages + decisions."""
        sections: list[str] = []

        # Subordinate PM summaries
        reports = self._registry.list_reports(profile)
        pm_reports: list[str] = []
        for r in reports:
            if r.role == "project_manager" and r.status != "archived":
                pm_reports.append(
                    f"PM: {r.display_name} ({r.profile_name}) — status: {r.status}"
                )
                # Include brief worker count
                try:
                    workers = self._worker_registry.list(
                        project_manager=r.profile_name
                    )
                    running = sum(
                        1 for w in workers
                        if w.status == SubagentStatus.RUNNING.value
                    )
                    completed = sum(
                        1 for w in workers
                        if w.status == SubagentStatus.COMPLETED.value
                    )
                    pm_reports.append(
                        f"  Workers: {len(workers)} total, "
                        f"{running} running, {completed} completed"
                    )
                except Exception:
                    pm_reports.append("  Workers: unable to query")

        if pm_reports:
            sections.append(
                "Subordinate PMs\n" + "-" * 20 + "\n" + "\n".join(pm_reports)
            )
        else:
            sections.append("Subordinate PMs: none active")

        # Department-level messages
        sections.append(self.summarize_messages(profile, hours=24))

        # Decisions (from recent task_response messages)
        received = self._bus.list_messages(
            profile_name=profile,
            direction="received",
            message_type=MessageType.TASK_RESPONSE,
            limit=20,
        )
        cutoff = _now_utc() - timedelta(hours=24)
        recent_decisions = [m for m in received if m.created_at >= cutoff]
        if recent_decisions:
            dec_lines = ["Recent Decisions / Responses"]
            for m in recent_decisions:
                summary_text = m.payload.get("summary", str(m.payload)[:120])
                dec_lines.append(
                    f"  From {m.from_profile}: {summary_text}"
                )
            sections.append("\n".join(dec_lines))

        return "\n---\n".join(sections)

    def _ceo_summary(self, profile: str) -> str:
        """CEO summary: department summaries + org-wide status."""
        sections: list[str] = []

        # Department summaries
        reports = self._registry.list_reports(profile)
        dept_lines: list[str] = ["Department Overview"]
        for dh in reports:
            if dh.status == "archived":
                continue
            dept_lines.append(
                f"  {dh.display_name} ({dh.profile_name}): "
                f"dept={dh.department or 'N/A'}, status={dh.status}"
            )
            # Count the dept head's subordinates
            sub_reports = self._registry.list_reports(dh.profile_name)
            active_subs = [s for s in sub_reports if s.status != "archived"]
            dept_lines.append(f"    Active subordinates: {len(active_subs)}")

        sections.append("\n".join(dept_lines))

        # Org-wide message stats
        bus_stats = self._bus.get_stats()
        org_lines = [
            "Org-Wide IPC Status",
            f"  Total messages: {bus_stats.get('total', 0)}",
            f"  Archived: {bus_stats.get('archived', 0)}",
        ]
        by_status = bus_stats.get("by_status", {})
        if by_status:
            status_str = ", ".join(
                f"{k}: {v}" for k, v in sorted(by_status.items())
            )
            org_lines.append(f"  By status: {status_str}")
        sections.append("\n".join(org_lines))

        # Recent escalations
        escalations = self._bus.list_messages(
            profile_name=profile,
            direction="received",
            message_type=MessageType.ESCALATION,
            limit=10,
        )
        cutoff = _now_utc() - timedelta(hours=24)
        recent_esc = [m for m in escalations if m.created_at >= cutoff]
        if recent_esc:
            esc_lines = [f"Escalations ({len(recent_esc)} in last 24h)"]
            for m in recent_esc:
                esc_lines.append(
                    f"  From {m.from_profile} at "
                    f"{_format_datetime(m.created_at)}: "
                    f"{str(m.payload)[:100]}"
                )
            sections.append("\n".join(esc_lines))

        return "\n---\n".join(sections)

    def _generic_summary(self, profile: str, role: str) -> str:
        """Fallback summary for unknown roles."""
        return (
            f"Summary for {profile} (role: {role})\n"
            + self.summarize_messages(profile, hours=24)
        )

    # ------------------------------------------------------------------
    # Internal: memory stats
    # ------------------------------------------------------------------

    def _get_memory_stats(self, profile: str, role: str) -> dict | None:
        """Attempt to retrieve memory stats for a profile.

        Returns ``None`` if the memory database does not exist or
        cannot be opened.
        """
        scope = _scope_for_profile(profile, role)

        # Try profile-specific db, then shared memory db
        candidates = [
            self._memory_dir / profile / "memory.db",
            self._memory_dir / "memory.db",
        ]

        for db_path in candidates:
            if db_path.exists():
                try:
                    store = MemoryStore(
                        db_path=str(db_path),
                        profile_name=profile,
                        profile_scope=scope,
                    )
                    return store.get_stats()
                except Exception as exc:
                    logger.debug(
                        "Could not read memory stats from %s: %s",
                        db_path,
                        exc,
                    )

        return None

    # ------------------------------------------------------------------
    # Internal: action item identification
    # ------------------------------------------------------------------

    def _identify_action_items(
        self,
        profile: str,
        role: str,
        recent_received: list,
        pending_count: int,
    ) -> list[str]:
        """Heuristically identify action items from available data.

        Parameters
        ----------
        profile : str
            The profile name.
        role : str
            The profile role.
        recent_received : list
            Recently received messages.
        pending_count : int
            Count of pending (unread) messages.

        Returns
        -------
        list[str]
            Human-readable action item strings.
        """
        items: list[str] = []

        # Pending messages need attention
        if pending_count > 0:
            items.append(
                f"Review {pending_count} pending message(s)"
            )

        # Escalations need urgent attention
        escalations = [
            m for m in recent_received
            if (
                m.message_type == MessageType.ESCALATION
                or (isinstance(m.message_type, str) and m.message_type == "escalation")
            )
        ]
        if escalations:
            items.append(
                f"Address {len(escalations)} escalation(s) from subordinates"
            )

        # Unanswered task requests
        task_requests = [
            m for m in recent_received
            if (
                m.message_type == MessageType.TASK_REQUEST
                or (isinstance(m.message_type, str) and m.message_type == "task_request")
            )
        ]
        if task_requests:
            items.append(
                f"Respond to {len(task_requests)} task request(s)"
            )

        # For PMs: check for stuck workers
        if role == "project_manager":
            try:
                workers = self._worker_registry.list(project_manager=profile)
                sleeping = [
                    w for w in workers
                    if w.status == SubagentStatus.SLEEPING.value
                ]
                if sleeping:
                    items.append(
                        f"Resume or review {len(sleeping)} sleeping worker(s)"
                    )
                completed = [
                    w for w in workers
                    if w.status == SubagentStatus.COMPLETED.value
                ]
                if completed:
                    items.append(
                        f"Review results from {len(completed)} completed worker(s)"
                    )
            except Exception:
                pass

        return items
