"""Bidirectional sync between Hermes native memory and hierarchy memory.

Hermes stores per-profile memory as markdown files:
    ~/.hermes/profiles/<name>/memories/MEMORY.md

The hierarchy system stores structured memory in SQLite:
    ~/.hermes/hierarchy/memory/<name>.db

This bridge syncs between the two so that:
- Native MEMORY.md entries are imported into the hierarchy MemoryStore
- Hierarchy hot-tier entries + shared knowledge are exported to a
  HIERARCHY_CONTEXT.md file that Hermes reads at session startup

Sync is triggered on profile activation (gateway startup).
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _content_hash(text: str) -> str:
    """Short hash for dedup."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Native → Hierarchy (import)
# ---------------------------------------------------------------------------


def _parse_memory_md(text: str) -> list[dict]:
    """Parse a Hermes MEMORY.md into a list of entries.

    Hermes memory files typically have entries as bullet points or
    sections.  We treat each non-empty line or bullet as an entry.
    """
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading bullet markers
        cleaned = re.sub(r"^[-*•]\s*", "", line)
        if cleaned:
            entries.append({"content": cleaned})
    return entries


def import_native_to_hierarchy(
    profile_name: str,
    profiles_dir: Path,
    memory_store: object,
) -> dict:
    """Import entries from Hermes MEMORY.md into the hierarchy MemoryStore.

    Parameters
    ----------
    profile_name:
        The profile whose native memory to import.
    profiles_dir:
        Path to ~/.hermes/profiles/
    memory_store:
        A MemoryStore instance for this profile.

    Returns
    -------
    dict with keys: imported (int), skipped (int), errors (list[str])
    """
    from core.memory.models import (
        MemoryEntry,
        MemoryEntryType,
        MemoryTier,
        generate_memory_id,
    )

    memory_path = profiles_dir / profile_name / "memories" / "MEMORY.md"
    result = {"imported": 0, "skipped": 0, "errors": []}

    if not memory_path.exists():
        logger.debug("[%s] No native MEMORY.md found at %s", profile_name, memory_path)
        return result

    try:
        text = memory_path.read_text(encoding="utf-8")
    except Exception as e:
        result["errors"].append(f"Failed to read {memory_path}: {e}")
        return result

    parsed = _parse_memory_md(text)
    if not parsed:
        return result

    # Build a set of content hashes already in the hierarchy store for dedup
    existing_hashes = set()
    try:
        existing = memory_store.list_entries(limit=5000)
        for entry in existing:
            existing_hashes.add(_content_hash(entry.content))
    except Exception:
        pass  # If listing fails, import everything (worst case: some dupes)

    for item in parsed:
        content = item["content"]
        h = _content_hash(content)

        if h in existing_hashes:
            result["skipped"] += 1
            continue

        try:
            entry = MemoryEntry(
                entry_id=generate_memory_id(),
                profile_name=profile_name,
                scope=memory_store._profile_scope,
                tier=MemoryTier.warm,  # Native memories are not "active" work
                entry_type=MemoryEntryType.context,
                content=content,
                metadata={"source": "native_memory_md", "hash": h},
            )
            memory_store.store(entry)
            existing_hashes.add(h)
            result["imported"] += 1
        except Exception as e:
            result["errors"].append(f"Failed to import entry: {e}")

    logger.info(
        "[%s] Native→Hierarchy: imported=%d, skipped=%d, errors=%d",
        profile_name, result["imported"], result["skipped"], len(result["errors"]),
    )
    return result


# ---------------------------------------------------------------------------
# Hierarchy → Native (export)
# ---------------------------------------------------------------------------


def export_hierarchy_to_native(
    profile_name: str,
    profiles_dir: Path,
    memory_store: object,
    knowledge_base: Optional[object] = None,
    profile_registry: Optional[object] = None,
    max_entries: int = 30,
) -> dict:
    """Export hierarchy memory to HIERARCHY_CONTEXT.md for Hermes prompt injection.

    Writes a structured markdown file that Hermes can read at session
    startup alongside the native MEMORY.md.

    Parameters
    ----------
    profile_name:
        The profile to export for.
    profiles_dir:
        Path to ~/.hermes/profiles/
    memory_store:
        The profile's MemoryStore instance.
    knowledge_base:
        Optional shared KnowledgeBase instance.
    profile_registry:
        Optional ProfileRegistry for ancestor memory access.
    max_entries:
        Max entries per section.

    Returns
    -------
    dict with keys: sections_written (int), path (str)
    """
    from core.memory.models import MemoryTier

    profile_dir = profiles_dir / profile_name
    if not profile_dir.exists():
        return {"sections_written": 0, "path": None, "error": "Profile directory not found"}

    sections = []

    # 1. Hot-tier personal memory
    try:
        hot_entries = memory_store.list_entries(tier=MemoryTier.hot, limit=max_entries)
        if hot_entries:
            lines = ["## Active Memory\n"]
            for e in hot_entries:
                lines.append(f"- **[{e.entry_type}]** {e.content}")
            sections.append("\n".join(lines))
    except Exception as e:
        logger.warning("[%s] Failed to read hot memory: %s", profile_name, e)

    # 2. Recent warm-tier entries (decisions, learnings)
    try:
        warm_entries = memory_store.list_entries(tier=MemoryTier.warm, limit=10)
        if warm_entries:
            lines = ["## Recent Context\n"]
            for e in warm_entries:
                lines.append(f"- **[{e.entry_type}]** {e.content}")
            sections.append("\n".join(lines))
    except Exception:
        pass

    # 3. Ancestor memory (if registry available)
    if profile_registry is not None:
        try:
            chain = profile_registry.get_chain_of_command(profile_name)
            for ancestor in chain:
                if ancestor.profile_name == profile_name:
                    continue
                # Try to get ancestor's memory store
                try:
                    from core.memory.memory_store import MemoryStore
                    from core.memory.models import MemoryScope, ROLE_SCOPE_MAP
                    scope = ROLE_SCOPE_MAP.get(ancestor.role, MemoryScope.strategic)
                    ancestor_db = profiles_dir.parent / "hierarchy" / "memory" / f"{ancestor.profile_name}.db"
                    if not ancestor_db.exists():
                        continue
                    ancestor_store = MemoryStore(str(ancestor_db), ancestor.profile_name, scope)
                    ancestor_hot = ancestor_store.list_entries(tier=MemoryTier.hot, limit=5)
                    if ancestor_hot:
                        lines = [f"## From {ancestor.display_name or ancestor.profile_name} ({ancestor.role})\n"]
                        for e in ancestor_hot:
                            lines.append(f"- {e.content}")
                        sections.append("\n".join(lines))
                except Exception:
                    continue
        except Exception as e:
            logger.warning("[%s] Failed to read ancestor memory: %s", profile_name, e)

    # 4. Shared knowledge
    if knowledge_base is not None:
        try:
            kb_entries = knowledge_base.search_all_profiles(query="", limit=15)
            if kb_entries:
                lines = ["## Shared Knowledge\n"]
                for e in kb_entries:
                    lines.append(f"- **{e.title}** ({e.category}): {e.content}")
                sections.append("\n".join(lines))
        except Exception as e:
            logger.warning("[%s] Failed to read shared knowledge: %s", profile_name, e)

    if not sections:
        return {"sections_written": 0, "path": None}

    # Write the file
    output_path = profile_dir / "memories" / "HIERARCHY_CONTEXT.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    header = (
        "# Hierarchy Context\n\n"
        "> Auto-generated by the hierarchy memory bridge. Do not edit manually.\n"
        f"> Last synced: {_now_utc().isoformat()}\n"
    )
    content = header + "\n" + "\n\n".join(sections) + "\n"

    try:
        output_path.write_text(content, encoding="utf-8")
        logger.info(
            "[%s] Hierarchy→Native: wrote %d sections to %s",
            profile_name, len(sections), output_path,
        )
        return {"sections_written": len(sections), "path": str(output_path)}
    except Exception as e:
        return {"sections_written": 0, "path": None, "error": str(e)}


# ---------------------------------------------------------------------------
# Full bidirectional sync
# ---------------------------------------------------------------------------


def sync_memory(
    profile_name: str,
    profiles_dir: Path,
    memory_store: object,
    knowledge_base: Optional[object] = None,
    profile_registry: Optional[object] = None,
) -> dict:
    """Run bidirectional memory sync for a profile.

    1. Import: Native MEMORY.md → hierarchy MemoryStore
    2. Export: Hierarchy hot/warm memory + KB → HIERARCHY_CONTEXT.md

    Call this on profile activation (gateway startup).

    Returns
    -------
    dict with import_result and export_result
    """
    import_result = import_native_to_hierarchy(profile_name, profiles_dir, memory_store)
    export_result = export_hierarchy_to_native(
        profile_name, profiles_dir, memory_store,
        knowledge_base=knowledge_base,
        profile_registry=profile_registry,
    )

    return {
        "profile": profile_name,
        "import": import_result,
        "export": export_result,
    }
