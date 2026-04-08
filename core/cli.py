"""Command-line interface for the Hierarchical Agent Architecture.

A thin administrative tool for managing agent profiles, the IPC
message bus, and the memory subsystem from the terminal.  Uses only
stdlib modules.

Usage::

    python -m core --help
    python -m core create-profile --name cto --display-name CTO --role department_head --parent hermes
    python -m core list-profiles --json
    python -m core show-org-chart
    python -m core send-message --from ceo --to cto --type task_request --payload '{"task": "fix bug"}'
    python -m core poll-messages --profile cto
    python -m core ipc-stats
    python -m core inspect-memory hermes --memory-db ./memory.db --scope strategic
    python -m core memory-stats hermes --memory-db ./memory.db --scope strategic
    python -m core run-gc hermes --memory-db ./memory.db --dry-run
    python -m core add-knowledge hermes --category decisions --title "Use SQLite" --content "Decided to use SQLite"
    python -m core search-knowledge hermes "SQLite" --memory-db ./memory.db
    python -m core memory-budget hermes --set --max-entries 500 --memory-db ./memory.db
    python -m core tier-report hermes --memory-db ./memory.db
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import timedelta
from typing import Any, Sequence

from core.ipc.cleanup import MessageCleanup
from core.ipc.exceptions import IPCError
from core.ipc.message_bus import MessageBus
from core.ipc.models import (
    Message,
    MessagePriority,
    MessageStatus,
    MessageType,
)
from core.memory.exceptions import ScopedMemoryError
from core.memory.knowledge_base import KnowledgeBase
from core.memory.memory_store import MemoryStore
from core.memory.models import (
    KnowledgeEntry,
    MemoryBudget,
    MemoryEntryType,
    MemoryScope,
    MemoryTier,
)
from core.memory.tiered_storage import TieredStorage
from core.registry.exceptions import RegistryError
from core.registry.integrity import scan_integrity
from core.registry.models import Profile
from core.registry.org_chart import render_org_chart
from core.registry.profile_registry import ProfileRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile_to_dict(profile: Profile) -> dict[str, Any]:
    """Convert a Profile to a JSON-friendly dict."""
    d = asdict(profile)
    # Convert datetime objects to ISO strings for JSON
    for key in ("created_at", "updated_at"):
        if d.get(key) is not None:
            d[key] = d[key].isoformat()
    return d


def _print_profile_human(profile: Profile) -> None:
    """Print a single profile in a human-readable format."""
    print(f"  Name:         {profile.profile_name}")
    print(f"  Display Name: {profile.display_name}")
    print(f"  Role:         {profile.role}")
    print(f"  Parent:       {profile.parent_profile or '(none)'}")
    print(f"  Department:   {profile.department or '(none)'}")
    print(f"  Status:       {profile.status}")
    print(f"  Description:  {profile.description or '(none)'}")
    print(f"  Config Path:  {profile.config_path or '(none)'}")
    print(f"  Created:      {profile.created_at.isoformat()}")
    print(f"  Updated:      {profile.updated_at.isoformat()}")


def _print_json(data: Any) -> None:
    """Print data as indented JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_create_profile(registry: ProfileRegistry, args: argparse.Namespace) -> int:
    """Handle the ``create-profile`` subcommand."""
    profile = registry.create_profile(
        name=args.name,
        display_name=args.display_name,
        role=args.role,
        parent=args.parent,
        department=args.department,
        description=args.description,
    )
    if args.json:
        _print_json(_profile_to_dict(profile))
    else:
        print(f"Created profile '{profile.profile_name}':")
        _print_profile_human(profile)
    return 0


def _cmd_get_profile(registry: ProfileRegistry, args: argparse.Namespace) -> int:
    """Handle the ``get-profile`` subcommand."""
    profile = registry.get_profile(args.name)
    if args.json:
        _print_json(_profile_to_dict(profile))
    else:
        _print_profile_human(profile)
    return 0


def _cmd_list_profiles(registry: ProfileRegistry, args: argparse.Namespace) -> int:
    """Handle the ``list-profiles`` subcommand."""
    profiles = registry.list_profiles(
        role=args.role,
        department=args.department,
        status=args.status,
    )
    if args.json:
        _print_json([_profile_to_dict(p) for p in profiles])
    else:
        if not profiles:
            print("No profiles found.")
        else:
            print(f"Found {len(profiles)} profile(s):\n")
            for p in profiles:
                status_tag = f"[{p.status}]"
                print(f"  {p.profile_name:<30} {p.role:<20} {status_tag}")
    return 0


def _cmd_show_org_chart(registry: ProfileRegistry, args: argparse.Namespace) -> int:
    """Handle the ``show-org-chart`` subcommand."""
    if args.json:
        tree = registry.get_org_tree(root=args.root)
        _print_json(tree)
    else:
        chart = render_org_chart(
            registry,
            root=args.root,
            active_only=args.active_only,
        )
        print(chart)
    return 0


def _cmd_suspend(registry: ProfileRegistry, args: argparse.Namespace) -> int:
    """Handle the ``suspend`` subcommand."""
    profile = registry.suspend(args.name)
    if args.json:
        _print_json(_profile_to_dict(profile))
    else:
        print(f"Profile '{profile.profile_name}' suspended.")
    return 0


def _cmd_activate(registry: ProfileRegistry, args: argparse.Namespace) -> int:
    """Handle the ``activate`` subcommand."""
    profile = registry.activate(args.name)
    if args.json:
        _print_json(_profile_to_dict(profile))
    else:
        print(f"Profile '{profile.profile_name}' activated.")
    return 0


def _cmd_reassign(registry: ProfileRegistry, args: argparse.Namespace) -> int:
    """Handle the ``reassign`` subcommand."""
    profile = registry.reassign(args.name, args.new_parent)
    if args.json:
        _print_json(_profile_to_dict(profile))
    else:
        print(
            f"Profile '{profile.profile_name}' reassigned to "
            f"parent '{profile.parent_profile}'."
        )
    return 0


def _cmd_check_integrity(registry: ProfileRegistry, args: argparse.Namespace) -> int:
    """Handle the ``check-integrity`` subcommand."""
    issues = scan_integrity(registry)
    if args.json:
        _print_json([
            {
                "severity": issue.severity,
                "profile_name": issue.profile_name,
                "message": issue.message,
                "rule_violated": issue.rule_violated,
            }
            for issue in issues
        ])
    else:
        if not issues:
            print("No integrity issues found. Registry is healthy.")
        else:
            print(f"Found {len(issues)} integrity issue(s):\n")
            for issue in issues:
                severity = issue.severity.upper()
                print(f"  [{severity}] {issue.profile_name}: {issue.message}")
                print(f"          Rule: {issue.rule_violated}")
    return 1 if any(i.severity == "error" for i in issues) else 0


# ---------------------------------------------------------------------------
# IPC helpers
# ---------------------------------------------------------------------------

def _message_to_dict(msg: Message) -> dict[str, Any]:
    """Convert a Message to a JSON-friendly dict."""
    return {
        "message_id": msg.message_id,
        "from_profile": msg.from_profile,
        "to_profile": msg.to_profile,
        "message_type": msg.message_type.value if isinstance(msg.message_type, MessageType) else msg.message_type,
        "payload": msg.payload,
        "correlation_id": msg.correlation_id,
        "priority": msg.priority.value if isinstance(msg.priority, MessagePriority) else msg.priority,
        "status": msg.status.value if isinstance(msg.status, MessageStatus) else msg.status,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "expires_at": msg.expires_at.isoformat() if msg.expires_at else None,
    }


def _print_message_human(msg: Message) -> None:
    """Print a single message in human-readable format."""
    print(f"  ID:           {msg.message_id}")
    print(f"  From:         {msg.from_profile}")
    print(f"  To:           {msg.to_profile}")
    print(f"  Type:         {msg.message_type.value if isinstance(msg.message_type, MessageType) else msg.message_type}")
    print(f"  Priority:     {msg.priority.value if isinstance(msg.priority, MessagePriority) else msg.priority}")
    print(f"  Status:       {msg.status.value if isinstance(msg.status, MessageStatus) else msg.status}")
    print(f"  Payload:      {json.dumps(msg.payload)}")
    print(f"  Correlation:  {msg.correlation_id or '(none)'}")
    print(f"  Created:      {msg.created_at.isoformat()}")
    print(f"  Expires:      {msg.expires_at.isoformat() if msg.expires_at else '(never)'}")


# ---------------------------------------------------------------------------
# IPC subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_send_message(bus: MessageBus, args: argparse.Namespace) -> int:
    """Handle the ``send-message`` subcommand."""
    try:
        payload = json.loads(args.payload) if args.payload else {}
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON payload: {e}", file=sys.stderr)
        return 1

    try:
        message_type = MessageType(args.type)
    except ValueError:
        print(f"Error: Invalid message type: {args.type}", file=sys.stderr)
        return 1

    try:
        priority = MessagePriority(args.priority)
    except ValueError:
        print(f"Error: Invalid priority: {args.priority}", file=sys.stderr)
        return 1

    ttl = timedelta(hours=float(args.ttl_hours)) if args.ttl_hours else ...

    message_id = bus.send(
        from_profile=getattr(args, "from"),
        to_profile=args.to,
        message_type=message_type,
        payload=payload,
        correlation_id=args.correlation_id,
        priority=priority,
        ttl=ttl,
    )

    msg = bus.get(message_id)
    if args.json:
        _print_json(_message_to_dict(msg))
    else:
        print(f"Message sent: {message_id}")
        _print_message_human(msg)
    return 0


def _cmd_poll_messages(bus: MessageBus, args: argparse.Namespace) -> int:
    """Handle the ``poll-messages`` subcommand."""
    message_type = MessageType(args.type) if args.type else None
    messages = bus.poll(
        args.profile,
        limit=args.limit,
        message_type=message_type,
    )
    if args.json:
        _print_json([_message_to_dict(m) for m in messages])
    else:
        if not messages:
            print(f"No pending messages for '{args.profile}'.")
        else:
            print(f"Found {len(messages)} pending message(s) for '{args.profile}':\n")
            for msg in messages:
                priority_tag = f"[{msg.priority}]"
                print(f"  {msg.message_id}  {msg.message_type:<20} {priority_tag:<10} from {msg.from_profile}")
    return 0


def _cmd_list_ipc_messages(bus: MessageBus, args: argparse.Namespace) -> int:
    """Handle the ``list-messages`` subcommand."""
    status = MessageStatus(args.status) if args.status else None
    message_type = MessageType(args.type) if args.type else None
    messages = bus.list_messages(
        profile_name=args.profile,
        status=status,
        message_type=message_type,
        direction=args.direction,
        limit=args.limit,
    )
    if args.json:
        _print_json([_message_to_dict(m) for m in messages])
    else:
        if not messages:
            print("No messages found.")
        else:
            print(f"Found {len(messages)} message(s):\n")
            for msg in messages:
                status_tag = f"[{msg.status}]"
                print(
                    f"  {msg.message_id}  {msg.from_profile} -> {msg.to_profile}  "
                    f"{msg.message_type:<20} {status_tag}"
                )
    return 0


def _cmd_message_status(bus: MessageBus, args: argparse.Namespace) -> int:
    """Handle the ``message-status`` subcommand."""
    msg = bus.get(args.message_id)
    if args.json:
        _print_json(_message_to_dict(msg))
    else:
        _print_message_human(msg)
    return 0


def _cmd_ipc_stats(bus: MessageBus, args: argparse.Namespace) -> int:
    """Handle the ``ipc-stats`` subcommand."""
    stats = bus.get_stats()
    if args.json:
        _print_json(stats)
    else:
        print("IPC Message Bus Statistics:")
        print(f"  Total messages:    {stats['total']}")
        print(f"  Archived:          {stats['archived']}")
        if stats["by_status"]:
            print("  By status:")
            for status, count in sorted(stats["by_status"].items()):
                print(f"    {status:<15} {count}")
        if stats["by_type"]:
            print("  By type:")
            for mtype, count in sorted(stats["by_type"].items()):
                print(f"    {mtype:<20} {count}")
        if stats["by_profile"]:
            print("  Pending by profile:")
            for profile, count in sorted(stats["by_profile"].items()):
                print(f"    {profile:<20} {count}")
    return 0


def _cmd_ipc_cleanup(bus: MessageBus, args: argparse.Namespace) -> int:
    """Handle the ``ipc-cleanup`` subcommand."""
    cleanup = MessageCleanup(bus)
    result = cleanup.cleanup()
    if args.json:
        _print_json(result)
    else:
        print(f"Cleanup complete: {result['expired']} expired, {result['archived']} archived.")
    return 0


# IPC commands that need a MessageBus instead of ProfileRegistry
_IPC_COMMANDS = {
    "send-message", "poll-messages", "list-messages",
    "message-status", "ipc-stats", "ipc-cleanup",
}

# ---------------------------------------------------------------------------
# Memory subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_inspect_memory(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Handle the ``inspect-memory`` subcommand."""
    scope = MemoryScope(args.scope)
    store = MemoryStore(
        db_path=args.memory_db,
        profile_name=args.profile,
        profile_scope=scope,
    )
    try:
        tier = MemoryTier(args.tier) if args.tier else None
        entry_type = MemoryEntryType(args.type) if args.type else None
        entry_scope = MemoryScope(args.filter_scope) if args.filter_scope else None

        entries = store.list_entries(
            tier=tier,
            scope=entry_scope,
            entry_type=entry_type,
            limit=args.limit,
        )

        if args.json:
            _print_json([e.to_dict() for e in entries])
        else:
            if not entries:
                print(f"No memory entries found for profile '{args.profile}'.")
            else:
                print(f"Found {len(entries)} memory entry(ies) for '{args.profile}':\n")
                print(f"  {'ENTRY_ID':<14} {'TIER':<6} {'TYPE':<12} {'CONTENT':<80} {'CREATED_AT'}")
                print(f"  {'-'*14} {'-'*6} {'-'*12} {'-'*80} {'-'*25}")
                for e in entries:
                    preview = e.content[:80].replace("\n", " ")
                    created = e.created_at.isoformat() if e.created_at else ""
                    print(f"  {e.entry_id:<14} {e.tier.value:<6} {e.entry_type.value:<12} {preview:<80} {created}")
        return 0
    finally:
        store.close()


def _cmd_memory_stats(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Handle the ``memory-stats`` subcommand."""
    scope = MemoryScope(args.scope)
    store = MemoryStore(
        db_path=args.memory_db,
        profile_name=args.profile,
        profile_scope=scope,
    )
    try:
        stats = store.get_stats()

        if args.json:
            _print_json(stats)
        else:
            print(f"Memory Statistics for '{args.profile}':")
            print(f"  Total entries:  {stats['total_entries']}")
            print(f"  Total bytes:    {stats['total_bytes']}")
            if stats["by_tier"]:
                print("  By tier:")
                for tier, count in sorted(stats["by_tier"].items()):
                    print(f"    {tier:<10} {count}")
            if stats["by_type"]:
                print("  By type:")
                for etype, count in sorted(stats["by_type"].items()):
                    print(f"    {etype:<15} {count}")
            if stats["by_scope"]:
                print("  By scope:")
                for s, count in sorted(stats["by_scope"].items()):
                    print(f"    {s:<15} {count}")
            if stats.get("budget"):
                budget = stats["budget"]
                print("  Budget:")
                print(f"    Max entries:  {budget.get('max_entries', 'N/A')}")
                print(f"    Max bytes:    {budget.get('max_bytes', 'N/A')}")
        return 0
    finally:
        store.close()


def _cmd_run_gc(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Handle the ``run-gc`` subcommand."""
    scope = MemoryScope(args.scope)
    store = MemoryStore(
        db_path=args.memory_db,
        profile_name=args.profile,
        profile_scope=scope,
    )
    try:
        tiered = TieredStorage()
        # Run tier assessment
        transitions = tiered.run_tier_assessment(store)

        if args.dry_run:
            report = {
                "dry_run": True,
                "transitions_recommended": len(transitions),
                "transitions": [t.to_dict() for t in transitions],
            }
            if args.json:
                _print_json(report)
            else:
                print(f"GC dry run for '{args.profile}':")
                print(f"  Transitions recommended: {len(transitions)}")
                for t in transitions:
                    print(f"    {t.entry_id}: {t.from_tier.value} -> {t.to_tier.value} ({t.reason})")
        else:
            applied = tiered.apply_transitions(store, transitions)
            report = {
                "dry_run": False,
                "transitions_recommended": len(transitions),
                "transitions_applied": len(applied),
                "transitions": [t.to_dict() for t in applied],
            }
            if args.json:
                _print_json(report)
            else:
                print(f"GC complete for '{args.profile}':")
                print(f"  Transitions recommended: {len(transitions)}")
                print(f"  Transitions applied:     {len(applied)}")
                for t in applied:
                    print(f"    {t.entry_id}: {t.from_tier.value} -> {t.to_tier.value}")
        return 0
    finally:
        store.close()


def _cmd_add_knowledge(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Handle the ``add-knowledge`` subcommand."""
    kb = KnowledgeBase(
        db_path=args.memory_db,
        profile_name=args.profile,
    )
    try:
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
        entry = KnowledgeEntry(
            entry_id="",
            profile_name=args.profile,
            category=args.category,
            title=args.title,
            content=args.content,
            tags=tags,
        )
        created = kb.add_knowledge(entry)

        if args.json:
            _print_json(created.to_dict())
        else:
            print(f"Knowledge entry created:")
            print(f"  ID:       {created.entry_id}")
            print(f"  Category: {created.category}")
            print(f"  Title:    {created.title}")
            print(f"  Tags:     {', '.join(created.tags) if created.tags else '(none)'}")
            print(f"  Created:  {created.created_at.isoformat()}")
        return 0
    finally:
        kb.close()


def _cmd_search_knowledge(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Handle the ``search-knowledge`` subcommand."""
    kb = KnowledgeBase(
        db_path=args.memory_db,
        profile_name=args.profile,
    )
    try:
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
        results = kb.search_knowledge(
            query=args.query,
            category=args.category,
            tags=tags,
            limit=args.limit,
        )

        if args.json:
            _print_json([r.to_dict() for r in results])
        else:
            if not results:
                print(f"No knowledge entries found matching '{args.query}'.")
            else:
                print(f"Found {len(results)} knowledge entry(ies):\n")
                for r in results:
                    print(f"  [{r.entry_id}] {r.category} / {r.title}")
                    preview = r.content[:120].replace("\n", " ")
                    print(f"    {preview}")
                    if r.tags:
                        print(f"    Tags: {', '.join(r.tags)}")
                    print()
        return 0
    finally:
        kb.close()


def _cmd_memory_budget(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Handle the ``memory-budget`` subcommand."""
    scope = MemoryScope(args.scope)
    store = MemoryStore(
        db_path=args.memory_db,
        profile_name=args.profile,
        profile_scope=scope,
    )
    try:
        if args.set:
            # Update budget
            existing = store.get_budget()
            if existing:
                budget = existing
            else:
                budget = MemoryBudget(profile_name=args.profile)

            if args.max_entries is not None:
                budget.max_entries = args.max_entries
            if args.max_bytes is not None:
                budget.max_bytes = args.max_bytes

            store.set_budget(budget)

            if args.json:
                _print_json(budget.to_dict())
            else:
                print(f"Budget updated for '{args.profile}':")
                print(f"  Max entries: {budget.max_entries}")
                print(f"  Max bytes:   {budget.max_bytes}")
                print(f"  Tier quotas: {budget.tier_quotas}")
        else:
            # Show current budget
            budget = store.get_budget()
            if budget is None:
                if args.json:
                    _print_json({"profile": args.profile, "budget": None})
                else:
                    print(f"No budget set for '{args.profile}'.")
            else:
                if args.json:
                    _print_json(budget.to_dict())
                else:
                    print(f"Memory budget for '{args.profile}':")
                    print(f"  Max entries: {budget.max_entries}")
                    print(f"  Max bytes:   {budget.max_bytes}")
                    print(f"  Tier quotas:")
                    for tier_name, quota in sorted(budget.tier_quotas.items()):
                        print(f"    {tier_name:<10} {quota}")
        return 0
    finally:
        store.close()


def _cmd_tier_report(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Handle the ``tier-report`` subcommand."""
    scope = MemoryScope(args.scope)
    store = MemoryStore(
        db_path=args.memory_db,
        profile_name=args.profile,
        profile_scope=scope,
    )
    try:
        tiered = TieredStorage()
        report = tiered.get_aging_report(store)

        if args.json:
            _print_json(report)
        else:
            if not report:
                print(f"No entries approaching tier transitions for '{args.profile}'.")
            else:
                print(f"Tier aging report for '{args.profile}' ({len(report)} entries):\n")
                print(f"  {'ENTRY_ID':<14} {'CURRENT':<10} {'RECOMMENDED':<14} {'AGE (days)':<12} {'DAYS LEFT'}")
                print(f"  {'-'*14} {'-'*10} {'-'*14} {'-'*12} {'-'*10}")
                for r in report:
                    print(
                        f"  {r['entry_id']:<14} {r['current_tier']:<10} "
                        f"{r['recommended_tier']:<14} {r['age_days']:<12} "
                        f"{r['days_until_transition']}"
                    )
        return 0
    finally:
        store.close()


# Memory commands that need MemoryStore / KnowledgeBase / TieredStorage
_MEMORY_COMMANDS = {
    "inspect-memory", "memory-stats", "run-gc",
    "add-knowledge", "search-knowledge", "memory-budget",
    "tier-report",
}


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="hierarchical-registry",
        description=(
            "Administrative CLI for the Hierarchical Agent Profile Registry. "
            "Create, inspect, and manage agent profiles."
        ),
    )
    parser.add_argument(
        "--db",
        default="./registry.db",
        help="Path to the SQLite database (default: ./registry.db)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output in JSON format instead of human-readable text",
    )
    parser.add_argument(
        "--bus-db",
        default=None,
        help="Path to the IPC bus database (default: ./bus.db)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # -- create-profile ----------------------------------------------------
    p_create = subparsers.add_parser(
        "create-profile",
        help="Create a new agent profile",
    )
    p_create.add_argument("--name", required=True, help="Unique profile name (lowercase, hyphens allowed)")
    p_create.add_argument("--display-name", required=True, help="Human-readable display name")
    p_create.add_argument("--role", required=True, choices=["ceo", "department_head", "project_manager", "specialist"], help="Role in the hierarchy")
    p_create.add_argument("--parent", required=True, help="Parent profile name (use 'none' for CEO)")
    p_create.add_argument("--department", default=None, help="Department name (optional)")
    p_create.add_argument("--description", default=None, help="Profile description (optional)")
    p_create.set_defaults(func=_cmd_create_profile)

    # -- get-profile -------------------------------------------------------
    p_get = subparsers.add_parser(
        "get-profile",
        help="Get details of a specific profile",
    )
    p_get.add_argument("name", help="Profile name to look up")
    p_get.set_defaults(func=_cmd_get_profile)

    # -- list-profiles -----------------------------------------------------
    p_list = subparsers.add_parser(
        "list-profiles",
        help="List profiles with optional filters",
    )
    p_list.add_argument("--role", default=None, choices=["ceo", "department_head", "project_manager", "specialist"], help="Filter by role")
    p_list.add_argument("--department", default=None, help="Filter by department")
    p_list.add_argument("--status", default=None, choices=["active", "suspended", "archived"], help="Filter by status")
    p_list.set_defaults(func=_cmd_list_profiles)

    # -- show-org-chart ----------------------------------------------------
    p_chart = subparsers.add_parser(
        "show-org-chart",
        help="Display the organizational chart as a tree",
    )
    p_chart.add_argument("--root", default=None, help="Start the tree at this profile (default: CEO)")
    p_chart.add_argument("--active-only", action="store_true", default=False, help="Hide suspended and archived profiles")
    p_chart.set_defaults(func=_cmd_show_org_chart)

    # -- suspend -----------------------------------------------------------
    p_suspend = subparsers.add_parser(
        "suspend",
        help="Suspend a profile",
    )
    p_suspend.add_argument("name", help="Profile name to suspend")
    p_suspend.set_defaults(func=_cmd_suspend)

    # -- activate ----------------------------------------------------------
    p_activate = subparsers.add_parser(
        "activate",
        help="Activate a suspended profile",
    )
    p_activate.add_argument("name", help="Profile name to activate")
    p_activate.set_defaults(func=_cmd_activate)

    # -- reassign ----------------------------------------------------------
    p_reassign = subparsers.add_parser(
        "reassign",
        help="Reassign a profile to a new parent",
    )
    p_reassign.add_argument("name", help="Profile name to reassign")
    p_reassign.add_argument("--new-parent", required=True, help="New parent profile name")
    p_reassign.set_defaults(func=_cmd_reassign)

    # -- check-integrity ---------------------------------------------------
    p_integrity = subparsers.add_parser(
        "check-integrity",
        help="Run integrity checks on the registry",
    )
    p_integrity.set_defaults(func=_cmd_check_integrity)

    # -- IPC: send-message -------------------------------------------------
    p_send = subparsers.add_parser(
        "send-message",
        help="Send an IPC message between profiles",
    )
    p_send.add_argument("--from", required=True, help="Sender profile name")
    p_send.add_argument("--to", required=True, help="Recipient profile name")
    p_send.add_argument(
        "--type", required=True,
        choices=[t.value for t in MessageType],
        help="Message type",
    )
    p_send.add_argument("--payload", default=None, help="JSON payload string")
    p_send.add_argument("--correlation-id", default=None, help="Correlation ID for request/response linking")
    p_send.add_argument(
        "--priority", default="normal",
        choices=[p.value for p in MessagePriority],
        help="Message priority (default: normal)",
    )
    p_send.add_argument("--ttl-hours", default=None, type=float, help="TTL in hours (default: bus default)")
    p_send.set_defaults(func=_cmd_send_message)

    # -- IPC: poll-messages ------------------------------------------------
    p_poll = subparsers.add_parser(
        "poll-messages",
        help="Poll pending messages for a profile",
    )
    p_poll.add_argument("--profile", required=True, help="Profile to poll messages for")
    p_poll.add_argument(
        "--type", default=None,
        choices=[t.value for t in MessageType],
        help="Filter by message type",
    )
    p_poll.add_argument("--limit", type=int, default=50, help="Max messages to return (default: 50)")
    p_poll.set_defaults(func=_cmd_poll_messages)

    # -- IPC: list-messages ------------------------------------------------
    p_list_msgs = subparsers.add_parser(
        "list-messages",
        help="List IPC messages with filters",
    )
    p_list_msgs.add_argument("--profile", default=None, help="Filter by profile name")
    p_list_msgs.add_argument(
        "--status", default=None,
        choices=[s.value for s in MessageStatus],
        help="Filter by message status",
    )
    p_list_msgs.add_argument(
        "--type", default=None,
        choices=[t.value for t in MessageType],
        help="Filter by message type",
    )
    p_list_msgs.add_argument(
        "--direction", default=None,
        choices=["sent", "received"],
        help="Filter direction (sent/received)",
    )
    p_list_msgs.add_argument("--limit", type=int, default=50, help="Max messages to return (default: 50)")
    p_list_msgs.set_defaults(func=_cmd_list_ipc_messages)

    # -- IPC: message-status -----------------------------------------------
    p_msg_status = subparsers.add_parser(
        "message-status",
        help="Get the status of a specific message",
    )
    p_msg_status.add_argument("message_id", help="Message ID to look up")
    p_msg_status.set_defaults(func=_cmd_message_status)

    # -- IPC: ipc-stats ----------------------------------------------------
    p_ipc_stats = subparsers.add_parser(
        "ipc-stats",
        help="Show IPC message bus statistics",
    )
    p_ipc_stats.set_defaults(func=_cmd_ipc_stats)

    # -- IPC: ipc-cleanup --------------------------------------------------
    p_ipc_cleanup = subparsers.add_parser(
        "ipc-cleanup",
        help="Run TTL expiry and archive cleanup on the message bus",
    )
    p_ipc_cleanup.set_defaults(func=_cmd_ipc_cleanup)

    # -- Memory: inspect-memory --------------------------------------------
    p_inspect_mem = subparsers.add_parser(
        "inspect-memory",
        help="List memory entries for a profile with optional filters",
    )
    p_inspect_mem.add_argument("profile", help="Profile name to inspect")
    p_inspect_mem.add_argument(
        "--tier", default=None,
        choices=[t.value for t in MemoryTier],
        help="Filter by memory tier",
    )
    p_inspect_mem.add_argument(
        "--scope", default="strategic",
        choices=[s.value for s in MemoryScope],
        help="Memory scope for the profile (default: strategic)",
    )
    p_inspect_mem.add_argument(
        "--filter-scope", default=None,
        choices=[s.value for s in MemoryScope],
        help="Filter entries by scope (distinct from profile scope)",
    )
    p_inspect_mem.add_argument(
        "--type", default=None,
        choices=[t.value for t in MemoryEntryType],
        help="Filter by entry type",
    )
    p_inspect_mem.add_argument("--limit", type=int, default=50, help="Max entries to return (default: 50)")
    p_inspect_mem.add_argument("--memory-db", default=":memory:", help="Path to the memory database (default: :memory:)")
    p_inspect_mem.set_defaults(func=_cmd_inspect_memory)

    # -- Memory: memory-stats ----------------------------------------------
    p_mem_stats = subparsers.add_parser(
        "memory-stats",
        help="Show memory store statistics for a profile",
    )
    p_mem_stats.add_argument("profile", help="Profile name")
    p_mem_stats.add_argument(
        "--scope", default="strategic",
        choices=[s.value for s in MemoryScope],
        help="Memory scope for the profile (default: strategic)",
    )
    p_mem_stats.add_argument("--memory-db", default=":memory:", help="Path to the memory database (default: :memory:)")
    p_mem_stats.set_defaults(func=_cmd_memory_stats)

    # -- Memory: run-gc ----------------------------------------------------
    p_run_gc = subparsers.add_parser(
        "run-gc",
        help="Run garbage collection (tier lifecycle) for a profile",
    )
    p_run_gc.add_argument("profile", help="Profile name")
    p_run_gc.add_argument("--dry-run", action="store_true", default=False, help="Preview transitions without applying")
    p_run_gc.add_argument(
        "--scope", default="strategic",
        choices=[s.value for s in MemoryScope],
        help="Memory scope for the profile (default: strategic)",
    )
    p_run_gc.add_argument("--memory-db", default=":memory:", help="Path to the memory database (default: :memory:)")
    p_run_gc.set_defaults(func=_cmd_run_gc)

    # -- Memory: add-knowledge ---------------------------------------------
    p_add_kb = subparsers.add_parser(
        "add-knowledge",
        help="Add a knowledge entry for a profile",
    )
    p_add_kb.add_argument("profile", help="Profile name")
    p_add_kb.add_argument("--category", required=True, help="Knowledge category")
    p_add_kb.add_argument("--title", required=True, help="Entry title")
    p_add_kb.add_argument("--content", required=True, help="Entry content")
    p_add_kb.add_argument("--tags", default=None, help="Comma-separated tags")
    p_add_kb.add_argument("--memory-db", default=":memory:", help="Path to the memory database (default: :memory:)")
    p_add_kb.set_defaults(func=_cmd_add_knowledge)

    # -- Memory: search-knowledge ------------------------------------------
    p_search_kb = subparsers.add_parser(
        "search-knowledge",
        help="Search knowledge base entries for a profile",
    )
    p_search_kb.add_argument("profile", help="Profile name")
    p_search_kb.add_argument("query", help="Search query text")
    p_search_kb.add_argument("--category", default=None, help="Filter by category")
    p_search_kb.add_argument("--tags", default=None, help="Comma-separated tags to filter by")
    p_search_kb.add_argument("--limit", type=int, default=20, help="Max results to return (default: 20)")
    p_search_kb.add_argument("--memory-db", default=":memory:", help="Path to the memory database (default: :memory:)")
    p_search_kb.set_defaults(func=_cmd_search_knowledge)

    # -- Memory: memory-budget ---------------------------------------------
    p_budget = subparsers.add_parser(
        "memory-budget",
        help="View or set the memory budget for a profile",
    )
    p_budget.add_argument("profile", help="Profile name")
    p_budget.add_argument("--set", action="store_true", default=False, help="Update the budget (otherwise show current)")
    p_budget.add_argument("--max-entries", type=int, default=None, help="Maximum number of memory entries")
    p_budget.add_argument("--max-bytes", type=int, default=None, help="Maximum total bytes for memory entries")
    p_budget.add_argument(
        "--scope", default="strategic",
        choices=[s.value for s in MemoryScope],
        help="Memory scope for the profile (default: strategic)",
    )
    p_budget.add_argument("--memory-db", default=":memory:", help="Path to the memory database (default: :memory:)")
    p_budget.set_defaults(func=_cmd_memory_budget)

    # -- Memory: tier-report -----------------------------------------------
    p_tier_report = subparsers.add_parser(
        "tier-report",
        help="Show aging report for entries approaching tier transitions",
    )
    p_tier_report.add_argument("profile", help="Profile name")
    p_tier_report.add_argument(
        "--scope", default="strategic",
        choices=[s.value for s in MemoryScope],
        help="Memory scope for the profile (default: strategic)",
    )
    p_tier_report.add_argument("--memory-db", default=":memory:", help="Path to the memory database (default: :memory:)")
    p_tier_report.set_defaults(func=_cmd_tier_report)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to the appropriate subcommand.

    Parameters
    ----------
    argv:
        Command-line arguments.  Defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code — ``0`` for success, non-zero for errors.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    # Normalise --parent 'none' / 'None' to Python None
    if hasattr(args, "parent") and args.parent is not None:
        if args.parent.lower() == "none":
            args.parent = None

    # Normalise --new-parent for the same reason
    if hasattr(args, "new_parent") and args.new_parent is not None:
        if args.new_parent.lower() == "none":
            args.new_parent = None

    # Dispatch to IPC commands, memory commands, or registry commands
    if args.command in _IPC_COMMANDS:
        bus_db = getattr(args, "bus_db", None) or "./bus.db"
        bus = MessageBus(db_path=bus_db)
        try:
            return args.func(bus, args)
        except IPCError as exc:
            if args.json:
                _print_json({"error": str(exc)})
            else:
                print(f"Error: {exc}", file=sys.stderr)
            return 1
        finally:
            bus.close()
    elif args.command in _MEMORY_COMMANDS:
        try:
            return args.func(args, parser)
        except ScopedMemoryError as exc:
            if args.json:
                _print_json({"error": str(exc)})
            else:
                print(f"Error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            if args.json:
                _print_json({"error": str(exc)})
            else:
                print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        registry = ProfileRegistry(args.db)
        try:
            return args.func(registry, args)
        except RegistryError as exc:
            if args.json:
                _print_json({"error": str(exc)})
            else:
                print(f"Error: {exc}", file=sys.stderr)
            return 1
        finally:
            registry.close()


if __name__ == "__main__":
    sys.exit(main())
