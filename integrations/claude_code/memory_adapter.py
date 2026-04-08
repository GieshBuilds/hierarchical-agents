"""ClaudeCodeMemoryAdapter — map MemoryStore to Claude Code CLAUDE.md sections.

Claude Code agents rely on CLAUDE.md for persistent context. This adapter
bridges the hierarchy MemoryStore (SQLite-backed tiered storage) to the
Claude Code workflow by:

1. Extracting relevant memory entries for a profile
2. Formatting them as CLAUDE.md sections (hot tier → inline, warm → summary)
3. Parsing CLAUDE.md annotations back into MemoryStore entries (round-trip)

The adapter is intentionally lightweight — it does not manage the MemoryStore
itself but provides serialization/deserialization logic.

Stdlib only — no external dependencies.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from integrations.claude_code.config import ClaudeCodeConfig


# Memory tier labels used in CLAUDE.md sections
_TIER_LABELS = {
    "hot": "🔥 Active Context",
    "warm": "📋 Recent History",
    "cold": "🗄️ Archive",
}

# Entry type labels for display
_TYPE_LABELS = {
    "decision": "Decision",
    "learning": "Learning",
    "context": "Context",
    "task": "Task",
    "knowledge": "Knowledge",
    "error": "Error/Blocker",
}


class ClaudeCodeMemoryAdapter:
    """Converts MemoryStore entries to/from Claude Code CLAUDE.md format.

    Parameters
    ----------
    config : ClaudeCodeConfig
        Integration configuration (controls limits and formatting).
    """

    def __init__(self, config: ClaudeCodeConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # MemoryStore → CLAUDE.md
    # ------------------------------------------------------------------

    def format_entries_for_claude_md(
        self,
        entries: List[Any],
        *,
        section_title: str = "Scoped Memory",
    ) -> str:
        """Format MemoryStore entries as a CLAUDE.md section.

        Entries are grouped by tier (hot, warm, cold) with visual labels.
        Long entries are truncated to avoid bloating the context file.

        Parameters
        ----------
        entries : list
            MemoryEntry objects from MemoryStore.list_entries().
        section_title : str
            Title for the CLAUDE.md section.

        Returns
        -------
        str
            Formatted markdown section.
        """
        if not entries:
            return f"## {section_title}\n\n*No memory entries.*"

        limit = self._config.max_memory_entries
        entries_to_show = entries[:limit]

        # Group by tier
        by_tier: Dict[str, List[Any]] = {"hot": [], "warm": [], "cold": []}
        for entry in entries_to_show:
            tier = getattr(entry.tier, "value", str(entry.tier))
            by_tier.setdefault(tier, []).append(entry)

        lines = [f"## {section_title} ({len(entries_to_show)} entries)\n"]

        for tier_key, tier_entries in by_tier.items():
            if not tier_entries:
                continue

            label = _TIER_LABELS.get(tier_key, tier_key.title())
            lines.append(f"### {label}\n")

            for entry in tier_entries:
                entry_type = getattr(entry.entry_type, "value", str(entry.entry_type))
                type_label = _TYPE_LABELS.get(entry_type, entry_type.title())

                # Truncate long content
                content = entry.content
                max_len = 400 if tier_key == "hot" else 200
                if len(content) > max_len:
                    content = content[:max_len] + "..."

                lines.append(f"**{type_label}**: {content}\n")

        if len(entries) > limit:
            lines.append(f"\n*{len(entries) - limit} older entries not shown.*")

        return "\n".join(lines)

    def format_entries_as_context_dict(
        self,
        entries: List[Any],
    ) -> Dict[str, List[Dict[str, str]]]:
        """Convert MemoryStore entries to a structured dictionary.

        Useful for JSON serialization or programmatic access.

        Parameters
        ----------
        entries : list
            MemoryEntry objects.

        Returns
        -------
        dict
            Mapping of tier_name → list of {type, content, entry_id} dicts.
        """
        result: Dict[str, List[Dict[str, str]]] = {}

        for entry in entries:
            tier = getattr(entry.tier, "value", str(entry.tier))
            entry_type = getattr(entry.entry_type, "value", str(entry.entry_type))

            if tier not in result:
                result[tier] = []

            result[tier].append({
                "entry_id": entry.entry_id,
                "type": entry_type,
                "content": entry.content,
            })

        return result

    # ------------------------------------------------------------------
    # CLAUDE.md → MemoryStore (parsing round-trip)
    # ------------------------------------------------------------------

    def parse_claude_md_annotations(
        self,
        claude_md_content: str,
    ) -> List[Tuple[str, str, str]]:
        """Parse memory annotations from a CLAUDE.md file.

        Looks for lines in the format::

            <!-- memory: type="decision" tier="hot" -->
            Content to store as a memory entry.
            <!-- /memory -->

        Returns a list of (tier, entry_type, content) tuples for
        creating new MemoryEntry objects.

        Parameters
        ----------
        claude_md_content : str
            Full text of a CLAUDE.md file.

        Returns
        -------
        list of (tier, entry_type, content) tuples
        """
        results: List[Tuple[str, str, str]] = []
        lines = claude_md_content.split("\n")
        in_block = False
        current_tier = "hot"
        current_type = "context"
        current_lines: List[str] = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("<!-- memory:"):
                in_block = True
                current_tier = self._extract_attr(stripped, "tier", "hot")
                current_type = self._extract_attr(stripped, "type", "context")
                current_lines = []

            elif stripped == "<!-- /memory -->" and in_block:
                in_block = False
                content = "\n".join(current_lines).strip()
                if content:
                    results.append((current_tier, current_type, content))

            elif in_block:
                current_lines.append(line)

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_attr(line: str, attr: str, default: str) -> str:
        """Extract an attribute value from an HTML-comment annotation."""
        import re
        pattern = rf'{attr}="([^"]+)"'
        match = re.search(pattern, line)
        return match.group(1) if match else default

    def get_memory_summary(self, entries: List[Any]) -> Dict[str, Any]:
        """Return a concise summary of memory stats.

        Parameters
        ----------
        entries : list
            MemoryEntry objects.

        Returns
        -------
        dict
            Summary with total_entries, tier_counts, and total_bytes.
        """
        tier_counts: Dict[str, int] = {}
        total_bytes = 0

        for entry in entries:
            tier = getattr(entry.tier, "value", str(entry.tier))
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            total_bytes += getattr(entry, "byte_size", len(entry.content.encode()))

        return {
            "total_entries": len(entries),
            "tier_counts": tier_counts,
            "total_bytes": total_bytes,
        }
