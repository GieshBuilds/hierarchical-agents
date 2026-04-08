"""Framework integration interfaces for the memory subsystem.

Defines protocols (abstract contracts) that frameworks implement
to integrate with the hierarchical memory system.  Uses
:class:`typing.Protocol` for structural subtyping — no inheritance
required.

Four protocols are provided:

* :class:`MemoryProvider` — CRUD + search for scoped memory entries.
* :class:`KnowledgeProvider` — cross-profile knowledge base operations.
* :class:`ContextProvider` — context assembly for profile activation
  and task handoff.
* :class:`MemoryLifecycleManager` — tier assessment, transitions, and
  garbage collection.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from core.memory.models import (
        ContextBrief,
        KnowledgeEntry,
        MemoryEntry,
        MemoryTier,
        StatusSummary,
        TierTransition,
    )


# ---------------------------------------------------------------------------
# MemoryProvider
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryProvider(Protocol):
    """Protocol for memory read/write operations.

    Framework integrations implement this to provide scoped memory
    access to profiles.  Each implementation decides the underlying
    storage backend (in-memory, filesystem, database, etc.).
    """

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        """Persist a memory entry.

        If the entry already exists (by ``entry_id``), update it;
        otherwise create a new record.

        Parameters
        ----------
        entry:
            The memory entry to store.

        Returns
        -------
        MemoryEntry
            The stored entry (may include server-generated fields).
        """
        ...

    def get(self, entry_id: str) -> MemoryEntry:
        """Retrieve a single memory entry by ID.

        Parameters
        ----------
        entry_id:
            Unique identifier of the entry.

        Returns
        -------
        MemoryEntry
            The requested entry.

        Raises
        ------
        KeyError
            If no entry with *entry_id* exists.
        """
        ...

    def search(self, query: str, *, limit: int = 50) -> list[MemoryEntry]:
        """Search memory entries by content relevance.

        Parameters
        ----------
        query:
            Free-text search query.
        limit:
            Maximum number of results to return.

        Returns
        -------
        list[MemoryEntry]
            Matching entries ordered by relevance (best first).
        """
        ...

    def delete(self, entry_id: str) -> None:
        """Delete a memory entry.

        Parameters
        ----------
        entry_id:
            Unique identifier of the entry to remove.

        Raises
        ------
        KeyError
            If no entry with *entry_id* exists.
        """
        ...

    def list_entries(
        self,
        *,
        tier: MemoryTier | None = None,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        """List memory entries, optionally filtered by tier.

        Parameters
        ----------
        tier:
            If provided, only return entries in this storage tier.
        limit:
            Maximum number of entries to return.

        Returns
        -------
        list[MemoryEntry]
            Matching entries ordered by most-recently updated first.
        """
        ...

    def get_stats(self) -> dict:
        """Return aggregate statistics about the memory store.

        Returns
        -------
        dict
            Keys may include ``total_entries``, ``total_bytes``,
            ``tier_counts``, ``scope_counts``, etc.
        """
        ...


# ---------------------------------------------------------------------------
# KnowledgeProvider
# ---------------------------------------------------------------------------


@runtime_checkable
class KnowledgeProvider(Protocol):
    """Protocol for knowledge base operations.

    Knowledge entries are cross-profile, long-lived records that
    capture decisions, standards, and domain facts.  Implementations
    provide the persistence and retrieval layer.
    """

    def add_knowledge(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        """Add a new knowledge entry.

        Parameters
        ----------
        entry:
            The knowledge entry to persist.

        Returns
        -------
        KnowledgeEntry
            The stored entry.
        """
        ...

    def get_knowledge(self, entry_id: str) -> KnowledgeEntry:
        """Retrieve a knowledge entry by ID.

        Parameters
        ----------
        entry_id:
            Unique identifier of the entry.

        Returns
        -------
        KnowledgeEntry
            The requested entry.

        Raises
        ------
        KeyError
            If no entry with *entry_id* exists.
        """
        ...

    def search_knowledge(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[KnowledgeEntry]:
        """Search knowledge entries by content relevance.

        Parameters
        ----------
        query:
            Free-text search query.
        limit:
            Maximum number of results to return.

        Returns
        -------
        list[KnowledgeEntry]
            Matching entries ordered by relevance (best first).
        """
        ...

    def delete_knowledge(self, entry_id: str) -> None:
        """Delete a knowledge entry.

        Parameters
        ----------
        entry_id:
            Unique identifier of the entry to remove.

        Raises
        ------
        KeyError
            If no entry with *entry_id* exists.
        """
        ...

    def list_categories(self) -> list[str]:
        """Return the distinct categories present in the knowledge base.

        Returns
        -------
        list[str]
            Sorted list of category names.
        """
        ...

    def get_stats(self) -> dict:
        """Return aggregate statistics about the knowledge base.

        Returns
        -------
        dict
            Keys may include ``total_entries``, ``categories``,
            ``entries_per_category``, etc.
        """
        ...


# ---------------------------------------------------------------------------
# ContextProvider
# ---------------------------------------------------------------------------


@runtime_checkable
class ContextProvider(Protocol):
    """Protocol for context injection on profile activation.

    Implementations assemble the right context from memory and
    knowledge stores so that an agent can resume work with minimal
    token overhead.
    """

    def build_activation_context(self, profile_name: str) -> ContextBrief:
        """Build the context brief needed when a profile is activated.

        This gathers relevant memories, knowledge, and recent history
        to orient the agent.

        Parameters
        ----------
        profile_name:
            The profile being activated.

        Returns
        -------
        ContextBrief
            Assembled context ready for injection.
        """
        ...

    def build_task_brief(
        self,
        pm_profile: str,
        task_description: str,
        relevant_context: list[str] | None = None,
    ) -> ContextBrief:
        """Build a task brief for a project-manager delegating work.

        Parameters
        ----------
        pm_profile:
            The project-manager profile delegating the task.
        task_description:
            Human-readable description of the task.
        relevant_context:
            Optional list of context keys or entry IDs to include.

        Returns
        -------
        ContextBrief
            A brief suitable for handing off to a worker.
        """
        ...

    def build_upward_summary(
        self,
        profile_name: str,
        **kwargs: object,
    ) -> StatusSummary:
        """Build a status summary for reporting to a parent profile.

        Parameters
        ----------
        profile_name:
            The profile producing the summary.
        **kwargs:
            Additional keyword arguments interpreted by the
            implementation (e.g. ``summary_type``, ``include_metrics``).

        Returns
        -------
        StatusSummary
            A summary ready for the parent to consume.
        """
        ...

    def inject_context(self, context: ContextBrief) -> str:
        """Render a :class:`ContextBrief` into a string for the agent.

        Parameters
        ----------
        context:
            The assembled context brief.

        Returns
        -------
        str
            A formatted string suitable for injection into the agent's
            system prompt or first user message.
        """
        ...


# ---------------------------------------------------------------------------
# MemoryLifecycleManager
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryLifecycleManager(Protocol):
    """Protocol for tier management and garbage collection.

    Implementations define the policies that govern when memory
    entries move between tiers (hot → warm → cool → cold) and when
    entries should be purged.
    """

    def assess_tier(self, entry: MemoryEntry) -> MemoryTier:
        """Determine the appropriate tier for a single entry.

        Parameters
        ----------
        entry:
            The memory entry to assess.

        Returns
        -------
        MemoryTier
            The recommended tier based on recency, access patterns,
            and policy rules.
        """
        ...

    def run_tier_assessment(
        self,
        memory_store: object,
    ) -> list[TierTransition]:
        """Run a full tier-assessment pass over a memory store.

        Examines all entries and produces a list of recommended
        transitions.  Does **not** apply them — call
        :meth:`apply_transitions` for that.

        Parameters
        ----------
        memory_store:
            The memory store to assess.  The implementation casts
            this to the concrete store type it expects.

        Returns
        -------
        list[TierTransition]
            Recommended tier transitions (may be empty).
        """
        ...

    def apply_transitions(
        self,
        memory_store: object,
        transitions: list[TierTransition],
    ) -> list[TierTransition]:
        """Apply a list of tier transitions to a memory store.

        Parameters
        ----------
        memory_store:
            The memory store to mutate.
        transitions:
            The transitions to apply.

        Returns
        -------
        list[TierTransition]
            The transitions that were successfully applied (may be a
            subset if some were invalid or already completed).
        """
        ...

    def get_tier_stats(self, memory_store: object) -> dict:
        """Return tier-level statistics for a memory store.

        Parameters
        ----------
        memory_store:
            The memory store to inspect.

        Returns
        -------
        dict
            Keys may include ``tier_counts``, ``tier_bytes``,
            ``oldest_per_tier``, ``transition_candidates``, etc.
        """
        ...


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "MemoryProvider",
    "KnowledgeProvider",
    "ContextProvider",
    "MemoryLifecycleManager",
]
