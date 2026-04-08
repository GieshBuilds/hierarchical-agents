"""Context injection engine for profile activation and task handoff.

Assembles, formats, and manages context briefs that are injected into agent
profiles when they wake up or receive delegated tasks.  Integrates across
memory, knowledge, IPC, and registry subsystems — gracefully degrading when
optional components are unavailable.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.memory.exceptions import ContextInjectionError
from core.memory.models import (
    ContextBrief,
    MemoryTier,
    StatusSummary,
    estimate_tokens,
)

if TYPE_CHECKING:
    from core.memory.knowledge_base import KnowledgeBase
    from core.memory.memory_store import MemoryStore

logger = logging.getLogger(__name__)

__all__ = [
    "ContextManager",
]


# --- Priority ordering ---

#: Section priority from highest (0) to lowest.  Used by
#: :meth:`ContextManager._truncate_to_budget` to decide which sections
#: to shrink first when the assembled context exceeds the token budget.
_SECTION_PRIORITY: dict[str, int] = {
    "identity": 0,
    "task": 1,
    "active_memory": 2,
    "pending_messages": 3,
    "ancestor_context": 4,
    "knowledge": 5,
    "active_workers": 6,
    "shared_knowledge": 7,
    "project_context": 8,
    "constraints": 9,
}

#: Default priority value for sections not listed above.
_DEFAULT_PRIORITY = 99


# --- ContextManager ---


class ContextManager:
    """Context injection engine for hierarchical agent profiles.

    Assembles :class:`ContextBrief` objects from multiple subsystems and
    formats them into injectable text.  Works gracefully when some
    backing components (memory store, knowledge base, message bus, etc.)
    are not available — the corresponding context sections are simply
    omitted with a logged warning.

    Parameters
    ----------
    memory_store : MemoryStore | None
        Optional per-profile memory store for retrieving HOT tier entries.
    knowledge_base : KnowledgeBase | None
        Optional knowledge base for relevant knowledge retrieval.
    profile_registry : object | None
        Optional profile registry (duck-typed).  Expected to expose a
        ``get(profile_name) -> object`` method that returns a profile
        with ``name``, ``role``, and ``description`` attributes.
    message_bus : object | None
        Optional IPC message bus (duck-typed).  Expected to expose a
        ``poll(profile_name, limit=...) -> list[Message]`` method.
    subagent_registry : object | None
        Optional subagent registry (duck-typed).  Expected to expose a
        ``list_active(parent_profile=...) -> list`` method returning
        worker objects with ``worker_id``, ``status``, and
        ``task_description`` attributes.
    max_context_tokens : int
        Maximum token budget for assembled context briefs.
    """

    def __init__(
        self,
        memory_store: MemoryStore | None = None,
        knowledge_base: KnowledgeBase | None = None,
        profile_registry: object | None = None,
        message_bus: object | None = None,
        subagent_registry: object | None = None,
        max_context_tokens: int = 4000,
        memory_store_factory: object | None = None,
    ) -> None:
        self._memory_store = memory_store
        self._knowledge_base = knowledge_base
        self._profile_registry = profile_registry
        self._message_bus = message_bus
        self._subagent_registry = subagent_registry
        self._max_context_tokens = max_context_tokens
        self._memory_store_factory = memory_store_factory

    # --- Public methods ---

    def build_activation_context(self, profile_name: str) -> ContextBrief:
        """Assemble a context brief for when a profile wakes up.

        Gathers identity information, active memory entries, relevant
        knowledge, pending IPC messages, and active worker status into
        named sections.  Each section is allocated a proportional share
        of :attr:`max_context_tokens` and truncated if the total exceeds
        the budget.

        Parameters
        ----------
        profile_name : str
            The profile being activated.

        Returns
        -------
        ContextBrief
            The assembled context brief with ``context_type='activation'``.

        Raises
        ------
        ContextInjectionError
            If the context assembly fails unexpectedly.
        """
        try:
            sections: dict[str, str] = {}

            # --- identity ---
            sections["identity"] = self._build_identity_section(profile_name)

            # --- active_memory ---
            sections["active_memory"] = self._build_active_memory_section(
                profile_name,
            )

            # --- knowledge ---
            sections["knowledge"] = self._build_knowledge_section(
                profile_name,
            )

            # --- pending_messages ---
            sections["pending_messages"] = self._build_pending_messages_section(
                profile_name,
            )

            # --- active_workers ---
            sections["active_workers"] = self._build_active_workers_section(
                profile_name,
            )

            # --- ancestor_context ---
            sections["ancestor_context"] = self._build_ancestor_context_section(
                profile_name,
            )

            # --- shared_knowledge ---
            sections["shared_knowledge"] = self._build_shared_knowledge_section(
                profile_name,
            )

            # Remove empty sections
            sections = {k: v for k, v in sections.items() if v}

            # Truncate to budget
            sections = self._truncate_to_budget(
                sections, self._max_context_tokens,
            )

            brief = ContextBrief(
                profile_name=profile_name,
                context_type="activation",
                sections=sections,
            )
            brief.token_estimate = self.estimate_context_size(brief)
            return brief

        except ContextInjectionError:
            raise
        except Exception as exc:
            raise ContextInjectionError(
                profile_name,
                f"Failed to build activation context: {exc}",
            ) from exc

    def build_task_brief(
        self,
        pm_profile: str,
        task_description: str,
        relevant_context: list[str] | None = None,
    ) -> ContextBrief:
        """Build a focused context brief for a worker from a PM.

        Produces a clean, minimal brief containing the task description,
        project context, and any relevant constraints cherry-picked from
        the PM's knowledge base.

        Parameters
        ----------
        pm_profile : str
            The project manager profile name delegating the task.
        task_description : str
            Free-text description of the task.
        relevant_context : list[str] | None
            Optional list of contextual notes from the PM.

        Returns
        -------
        ContextBrief
            The assembled brief with ``context_type='task_brief'``.

        Raises
        ------
        ContextInjectionError
            If the context assembly fails unexpectedly.
        """
        try:
            sections: dict[str, str] = {}

            # --- task ---
            sections["task"] = task_description

            # --- project_context ---
            if relevant_context:
                sections["project_context"] = "\n".join(relevant_context)
            else:
                sections["project_context"] = ""

            # --- constraints (cherry-pick from PM knowledge base) ---
            constraints_text = self._cherry_pick_knowledge(
                pm_profile, task_description,
            )
            sections["constraints"] = constraints_text

            # Remove empty sections
            sections = {k: v for k, v in sections.items() if v}

            # Truncate to budget
            sections = self._truncate_to_budget(
                sections, self._max_context_tokens,
            )

            brief = ContextBrief(
                profile_name=pm_profile,
                context_type="task_brief",
                sections=sections,
                metadata={"task_description": task_description},
            )
            brief.token_estimate = self.estimate_context_size(brief)
            return brief

        except ContextInjectionError:
            raise
        except Exception as exc:
            raise ContextInjectionError(
                pm_profile,
                f"Failed to build task brief: {exc}",
            ) from exc

    def build_upward_summary(
        self,
        profile_name: str,
        decisions: list[str] | None = None,
        deliverables: list[str] | None = None,
        blockers: list[str] | None = None,
        metrics: dict | None = None,
    ) -> StatusSummary:
        """Compress interaction results into an upward status summary.

        Packages decisions, deliverables, blockers, and metrics into a
        :class:`StatusSummary` suitable for reporting to a parent profile.

        Parameters
        ----------
        profile_name : str
            The reporting profile name.
        decisions : list[str] | None
            Decisions made during the interaction.
        deliverables : list[str] | None
            Deliverables produced.
        blockers : list[str] | None
            Blockers or issues encountered.
        metrics : dict | None
            Quantitative metrics from the interaction.

        Returns
        -------
        StatusSummary
            A summary with ``summary_type='interaction'``.
        """
        return StatusSummary(
            profile_name=profile_name,
            summary_type="interaction",
            decisions=list(decisions) if decisions else [],
            deliverables=list(deliverables) if deliverables else [],
            blockers=list(blockers) if blockers else [],
            metrics=dict(metrics) if metrics else {},
        )

    def inject_context(self, context: ContextBrief) -> str:
        """Format a :class:`ContextBrief` into injectable text.

        Each section is rendered as a Markdown level-2 heading followed
        by its content, separated by blank lines.

        Parameters
        ----------
        context : ContextBrief
            The context brief to format.

        Returns
        -------
        str
            The formatted context string ready for injection.
        """
        parts: list[str] = []
        for section_name, section_content in context.sections.items():
            parts.append(f"## {section_name}\n{section_content}\n")
        return "\n".join(parts)

    def estimate_context_size(self, context: ContextBrief) -> int:
        """Estimate total token count across all sections.

        Uses :func:`estimate_tokens` (≈ 4 chars per token) from the
        memory models module.

        Parameters
        ----------
        context : ContextBrief
            The context brief to measure.

        Returns
        -------
        int
            Estimated token count.
        """
        total = 0
        for section_content in context.sections.values():
            total += estimate_tokens(section_content)
        return total

    # --- Internal helpers ---

    def _truncate_to_budget(
        self,
        sections: dict[str, str],
        max_tokens: int,
    ) -> dict[str, str]:
        """Truncate sections to fit within the token budget.

        Higher-priority sections (lower numeric priority value) are
        preserved first.  When the total exceeds *max_tokens*, the
        lowest-priority sections are truncated from the end.

        Parameters
        ----------
        sections : dict[str, str]
            Section name to content mapping.
        max_tokens : int
            Maximum allowed token budget.

        Returns
        -------
        dict[str, str]
            The (possibly truncated) sections.
        """
        total = sum(estimate_tokens(v) for v in sections.values())
        if total <= max_tokens:
            return sections

        # Sort section names by priority (lowest number = highest priority)
        sorted_names = sorted(
            sections.keys(),
            key=lambda name: _SECTION_PRIORITY.get(name, _DEFAULT_PRIORITY),
        )

        result: dict[str, str] = {}
        remaining_budget = max_tokens

        for name in sorted_names:
            content = sections[name]
            section_tokens = estimate_tokens(content)

            if remaining_budget <= 0:
                # No budget left — drop this section entirely
                logger.debug(
                    "Dropping section %r — no remaining token budget", name,
                )
                continue

            if section_tokens <= remaining_budget:
                result[name] = content
                remaining_budget -= section_tokens
            else:
                # Truncate this section to fit the remaining budget
                char_budget = remaining_budget * 4  # inverse of estimate_tokens
                truncated = content[:char_budget]
                if truncated and not truncated.endswith("\n"):
                    truncated += "\n…(truncated)"
                result[name] = truncated
                remaining_budget = 0
                logger.debug(
                    "Truncated section %r from %d to %d tokens",
                    name, section_tokens, estimate_tokens(truncated),
                )

        return result

    def _build_identity_section(self, profile_name: str) -> str:
        """Build the identity section from the profile registry.

        Parameters
        ----------
        profile_name : str
            The profile to look up.

        Returns
        -------
        str
            Formatted identity text, or a fallback string.
        """
        if self._profile_registry is None:
            logger.debug(
                "No profile_registry available — using fallback identity",
            )
            return f"Profile: {profile_name}"

        try:
            get_fn = getattr(self._profile_registry, "get", None)
            if get_fn is None:
                logger.warning(
                    "profile_registry does not expose a 'get' method",
                )
                return f"Profile: {profile_name}"

            profile = get_fn(profile_name)
            name = getattr(profile, "name", profile_name)
            role = getattr(profile, "role", "unknown")
            description = getattr(profile, "description", "")
            lines = [
                f"Profile: {name}",
                f"Role: {role}",
            ]
            if description:
                lines.append(f"Description: {description}")
            return "\n".join(lines)

        except Exception:
            logger.warning(
                "Failed to retrieve profile %r from registry — "
                "using fallback identity",
                profile_name,
                exc_info=True,
            )
            return f"Profile: {profile_name}"

    def _build_active_memory_section(self, profile_name: str) -> str:
        """Build the active memory section from HOT tier entries.

        Parameters
        ----------
        profile_name : str
            The profile whose memory to retrieve.

        Returns
        -------
        str
            Formatted memory entries, or empty string.
        """
        if self._memory_store is None:
            logger.debug("No memory_store available — skipping active_memory")
            return ""

        try:
            entries = self._memory_store.list_entries(
                tier=MemoryTier.hot,
                limit=50,
            )
            if not entries:
                return ""
            return self._format_memory_entries(entries)

        except Exception:
            logger.warning(
                "Failed to retrieve HOT memory for %r",
                profile_name,
                exc_info=True,
            )
            return ""

    def _build_knowledge_section(self, profile_name: str) -> str:
        """Build the knowledge section from the knowledge base.

        Retrieves the most recently updated entries for the profile.

        Parameters
        ----------
        profile_name : str
            The profile whose knowledge to retrieve.

        Returns
        -------
        str
            Formatted knowledge entries, or empty string.
        """
        if self._knowledge_base is None:
            logger.debug("No knowledge_base available — skipping knowledge")
            return ""

        try:
            # Search with a broad query to get top entries
            entries = self._knowledge_base.search_knowledge(
                query="",
                limit=10,
            )
            if not entries:
                return ""
            return self._format_knowledge_entries(entries)

        except Exception:
            logger.warning(
                "Failed to retrieve knowledge for %r",
                profile_name,
                exc_info=True,
            )
            return ""

    def _build_pending_messages_section(self, profile_name: str) -> str:
        """Build the pending messages section from the message bus.

        Parameters
        ----------
        profile_name : str
            The profile to poll messages for.

        Returns
        -------
        str
            Formatted pending messages, or empty string.
        """
        if self._message_bus is None:
            logger.debug("No message_bus available — skipping pending_messages")
            return ""

        try:
            poll_fn = getattr(self._message_bus, "poll", None)
            if poll_fn is None:
                logger.warning(
                    "message_bus does not expose a 'poll' method",
                )
                return ""

            messages = poll_fn(profile_name, limit=20)
            if not messages:
                return ""
            return self._format_messages(messages)

        except Exception:
            logger.warning(
                "Failed to poll messages for %r",
                profile_name,
                exc_info=True,
            )
            return ""

    def _build_active_workers_section(self, profile_name: str) -> str:
        """Build the active workers section from the subagent registry.

        Only relevant for PM-level profiles that manage workers.

        Parameters
        ----------
        profile_name : str
            The parent profile whose workers to list.

        Returns
        -------
        str
            Formatted worker status list, or empty string.
        """
        if self._subagent_registry is None:
            logger.debug(
                "No subagent_registry available — skipping active_workers",
            )
            return ""

        try:
            list_fn = getattr(self._subagent_registry, "list_active", None)
            if list_fn is None:
                logger.warning(
                    "subagent_registry does not expose a 'list_active' method",
                )
                return ""

            workers = list_fn(parent_profile=profile_name)
            if not workers:
                return ""
            return self._format_workers(workers)

        except Exception:
            logger.warning(
                "Failed to list active workers for %r",
                profile_name,
                exc_info=True,
            )
            return ""

    def _build_ancestor_context_section(self, profile_name: str) -> str:
        """Build a section with hot-tier decisions from ancestor profiles.

        Walks the chain of command (via ``profile_registry``) and pulls
        up to 3 hot-tier entries from each ancestor's memory store (via
        ``memory_store_factory``).  Gracefully returns empty string if
        either dependency is unavailable.

        Parameters
        ----------
        profile_name : str
            The profile being activated.

        Returns
        -------
        str
            Formatted ancestor context, or empty string.
        """
        if self._profile_registry is None or self._memory_store_factory is None:
            return ""

        try:
            get_chain = getattr(self._profile_registry, "get_chain_of_command", None)
            if get_chain is None:
                return ""

            chain = get_chain(profile_name)
            parts: list[str] = []

            for profile in chain:
                ancestor_name = getattr(profile, "profile_name", None) or getattr(profile, "name", None)
                if ancestor_name is None or ancestor_name == profile_name:
                    continue

                ancestor_store = self._memory_store_factory(ancestor_name)
                if ancestor_store is None:
                    continue

                try:
                    entries = ancestor_store.list_entries(tier=MemoryTier.hot, limit=3)
                except Exception:
                    continue

                if not entries:
                    continue

                role = getattr(profile, "role", "unknown")
                lines = [f"### {ancestor_name} ({role})"]
                for entry in entries:
                    lines.append(f"[{entry.entry_type}] {entry.content}")
                parts.append("\n".join(lines))

            return "\n\n".join(parts)

        except Exception:
            logger.warning(
                "Failed to build ancestor context for %r",
                profile_name,
                exc_info=True,
            )
            return ""

    def _build_shared_knowledge_section(self, profile_name: str) -> str:
        """Build a section with recent cross-profile knowledge entries.

        Uses ``KnowledgeBase.search_all_profiles()`` to fetch the most
        recent shared knowledge regardless of which profile contributed it.

        Parameters
        ----------
        profile_name : str
            The profile being activated (used for attribution context).

        Returns
        -------
        str
            Formatted shared knowledge entries, or empty string.
        """
        if self._knowledge_base is None:
            return ""

        try:
            search_all = getattr(self._knowledge_base, "search_all_profiles", None)
            if search_all is None:
                return ""

            entries = search_all(query="", limit=10)
            if not entries:
                return ""

            lines: list[str] = []
            for entry in entries:
                source = entry.source_profile or entry.profile_name
                lines.append(f"### {entry.title} (from {source})")
                lines.append(entry.content)
                if entry.tags:
                    lines.append(f"Tags: {', '.join(entry.tags)}")
                lines.append("")

            return "\n".join(lines).strip()

        except Exception:
            logger.warning(
                "Failed to build shared knowledge section for %r",
                profile_name,
                exc_info=True,
            )
            return ""

    def _cherry_pick_knowledge(
        self,
        pm_profile: str,
        task_description: str,
    ) -> str:
        """Cherry-pick relevant knowledge entries based on task keywords.

        Extracts significant words from the task description and searches
        the PM's knowledge base for matching entries.

        Parameters
        ----------
        pm_profile : str
            The PM profile that owns the knowledge base.
        task_description : str
            The task description to extract keywords from.

        Returns
        -------
        str
            Formatted matching knowledge entries, or empty string.
        """
        if self._knowledge_base is None:
            logger.debug(
                "No knowledge_base available — skipping constraint "
                "cherry-picking",
            )
            return ""

        try:
            # Extract keywords: words of 4+ chars, lowered, deduplicated
            words = task_description.split()
            keywords = list(dict.fromkeys(
                w.strip(".,;:!?\"'()[]{}").lower()
                for w in words
                if len(w.strip(".,;:!?\"'()[]{}")) >= 4
            ))

            all_entries: list[object] = []
            seen_ids: set[str] = set()

            for keyword in keywords[:5]:  # limit keyword searches
                try:
                    results = self._knowledge_base.search_knowledge(
                        query=keyword,
                        limit=3,
                    )
                    for entry in results:
                        entry_id = getattr(entry, "entry_id", id(entry))
                        if entry_id not in seen_ids:
                            seen_ids.add(entry_id)
                            all_entries.append(entry)
                except Exception:
                    continue

            if not all_entries:
                return ""
            return self._format_knowledge_entries(all_entries)

        except Exception:
            logger.warning(
                "Failed to cherry-pick knowledge for task brief "
                "from %r",
                pm_profile,
                exc_info=True,
            )
            return ""

    # --- Formatters ---

    def _format_memory_entries(self, entries: list) -> str:
        """Format a list of memory entries into readable text.

        Each entry is rendered on a single line as::

            [entry_type] content (tier)

        Parameters
        ----------
        entries : list
            List of :class:`MemoryEntry` objects (duck-typed).

        Returns
        -------
        str
            Formatted text block.
        """
        lines: list[str] = []
        for entry in entries:
            entry_type = getattr(entry, "entry_type", "unknown")
            content = getattr(entry, "content", str(entry))
            tier = getattr(entry, "tier", "unknown")
            # Normalise enum values
            if hasattr(entry_type, "value"):
                entry_type = entry_type.value
            if hasattr(tier, "value"):
                tier = tier.value
            lines.append(f"[{entry_type}] {content} ({tier})")
        return "\n".join(lines)

    def _format_knowledge_entries(self, entries: list) -> str:
        """Format a list of knowledge entries into readable text.

        Each entry is rendered as::

            ### title
            content
            Tags: tag1, tag2

        Parameters
        ----------
        entries : list
            List of :class:`KnowledgeEntry` objects (duck-typed).

        Returns
        -------
        str
            Formatted text block.
        """
        blocks: list[str] = []
        for entry in entries:
            title = getattr(entry, "title", "Untitled")
            content = getattr(entry, "content", str(entry))
            tags = getattr(entry, "tags", [])
            parts = [f"### {title}", content]
            if tags:
                parts.append(f"Tags: {', '.join(str(t) for t in tags)}")
            blocks.append("\n".join(parts))
        return "\n\n".join(blocks)

    def _format_messages(self, messages: list) -> str:
        """Format IPC messages into readable text.

        Each message is rendered on a single line as::

            [priority] from -> to: payload

        Parameters
        ----------
        messages : list
            List of :class:`Message` objects (duck-typed).

        Returns
        -------
        str
            Formatted text block.
        """
        lines: list[str] = []
        for msg in messages:
            priority = getattr(msg, "priority", "normal")
            from_profile = getattr(msg, "from_profile", "?")
            to_profile = getattr(msg, "to_profile", "?")
            payload = getattr(msg, "payload", {})
            # Normalise enum values
            if hasattr(priority, "value"):
                priority = priority.value
            lines.append(
                f"[{priority}] {from_profile} -> {to_profile}: {payload}",
            )
        return "\n".join(lines)

    def _format_workers(self, workers: list) -> str:
        """Format subagent worker statuses into readable text.

        Each worker is rendered on a single line as::

            [status] worker_id: task_description

        Parameters
        ----------
        workers : list
            List of worker/subagent objects (duck-typed).

        Returns
        -------
        str
            Formatted text block.
        """
        lines: list[str] = []
        for worker in workers:
            status = getattr(worker, "status", "unknown")
            worker_id = getattr(worker, "worker_id", str(worker))
            task_desc = getattr(worker, "task_description", "")
            # Normalise enum values
            if hasattr(status, "value"):
                status = status.value
            lines.append(f"[{status}] {worker_id}: {task_desc}")
        return "\n".join(lines)
