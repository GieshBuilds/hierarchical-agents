"""Serialize core dataclasses to JSON-safe dicts for the dashboard API."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _dt(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _enum(val: Any) -> str:
    return val.value if hasattr(val, "value") else str(val)


def profile_to_dict(p: Any) -> dict[str, Any]:
    return {
        "profile_name": p.profile_name,
        "display_name": p.display_name,
        "role": p.role,
        "parent_profile": p.parent_profile,
        "department": p.department,
        "status": p.status,
        "created_at": _dt(p.created_at),
        "updated_at": _dt(p.updated_at),
        "config_path": p.config_path,
        "description": p.description,
    }


def message_to_dict(m: Any) -> dict[str, Any]:
    return {
        "message_id": m.message_id,
        "from_profile": m.from_profile,
        "to_profile": m.to_profile,
        "message_type": _enum(m.message_type),
        "payload": m.payload,
        "correlation_id": m.correlation_id,
        "priority": _enum(m.priority),
        "status": _enum(m.status),
        "created_at": _dt(m.created_at),
        "expires_at": _dt(m.expires_at),
    }


def memory_entry_to_dict(e: Any) -> dict[str, Any]:
    return {
        "entry_id": e.entry_id,
        "profile_name": e.profile_name,
        "scope": _enum(e.scope),
        "tier": _enum(e.tier),
        "entry_type": _enum(e.entry_type),
        "content": e.content,
        "metadata": e.metadata,
        "created_at": _dt(e.created_at),
        "updated_at": _dt(e.updated_at),
        "accessed_at": _dt(e.accessed_at),
        "expires_at": _dt(e.expires_at),
        "byte_size": e.byte_size,
    }


def knowledge_entry_to_dict(e: Any) -> dict[str, Any]:
    return {
        "entry_id": e.entry_id,
        "profile_name": e.profile_name,
        "category": e.category,
        "title": e.title,
        "content": e.content,
        "source_profile": e.source_profile,
        "source_context": e.source_context,
        "tags": e.tags if isinstance(e.tags, list) else [],
        "created_at": _dt(e.created_at),
        "updated_at": _dt(e.updated_at),
    }


def subagent_to_dict(s: Any) -> dict[str, Any]:
    return {
        "subagent_id": s.subagent_id,
        "project_manager": s.project_manager,
        "task_goal": s.task_goal,
        "status": s.status if isinstance(s.status, str) else _enum(s.status),
        "created_at": _dt(s.created_at),
        "updated_at": _dt(s.updated_at),
        "result_summary": s.result_summary,
        "token_cost": s.token_cost,
    }


def integrity_issue_to_dict(i: Any) -> dict[str, Any]:
    return {
        "severity": i.severity,
        "profile_name": i.profile_name,
        "message": i.message,
        "rule_violated": i.rule_violated,
    }
