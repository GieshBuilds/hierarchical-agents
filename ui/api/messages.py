"""IPC message endpoints."""
from __future__ import annotations

import sqlite3
from flask import Blueprint, jsonify, request

from core.ipc.models import MessageType, MessagePriority
from dashboard.api.serializers import message_to_dict
from ui.config import IPC_DB
from ui.services import get_bus

bp = Blueprint("messages", __name__, url_prefix="/api/messages")


@bp.route("", methods=["GET"])
def list_messages():
    """List messages with optional filters.

    Query params: profile, direction (to/from), status, type, priority, limit, offset
    """
    profile = request.args.get("profile")
    direction = request.args.get("direction")  # "to", "from", or None for both
    status = request.args.get("status")
    msg_type = request.args.get("type")
    priority = request.args.get("priority")
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))

    # Build query directly against SQLite for flexible filtering
    conditions = []
    params = []

    if profile:
        if direction == "to":
            conditions.append("to_profile = ?")
            params.append(profile)
        elif direction == "from":
            conditions.append("from_profile = ?")
            params.append(profile)
        else:
            conditions.append("(to_profile = ? OR from_profile = ?)")
            params.extend([profile, profile])

    if status:
        conditions.append("status = ?")
        params.append(status)

    if msg_type:
        conditions.append("message_type = ?")
        params.append(msg_type)

    if priority:
        conditions.append("priority = ?")
        params.append(priority)

    where = " AND ".join(conditions) if conditions else "1=1"
    query = f"""
        SELECT * FROM messages
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    conn = sqlite3.connect(str(IPC_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, params).fetchall()

    # Count total
    count_query = f"SELECT COUNT(*) FROM messages WHERE {where}"
    total = conn.execute(count_query, params[:-2]).fetchone()[0]
    conn.close()

    messages = []
    for r in rows:
        messages.append({
            "message_id": r["message_id"],
            "from_profile": r["from_profile"],
            "to_profile": r["to_profile"],
            "message_type": r["message_type"],
            "payload": _parse_json(r["payload"]),
            "correlation_id": r["correlation_id"],
            "priority": r["priority"],
            "status": r["status"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
        })

    return jsonify({"messages": messages, "total": total, "limit": limit, "offset": offset})


@bp.route("/<message_id>", methods=["GET"])
def get_message(message_id: str):
    try:
        msg = get_bus().get(message_id)
        return jsonify(message_to_dict(msg))
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@bp.route("/stats", methods=["GET"])
def message_stats():
    """Aggregate message stats."""
    conn = sqlite3.connect(str(IPC_DB))
    conn.row_factory = sqlite3.Row

    stats = {}
    # By status
    for row in conn.execute("SELECT status, COUNT(*) as cnt FROM messages GROUP BY status"):
        stats.setdefault("by_status", {})[row["status"]] = row["cnt"]
    # By type
    for row in conn.execute("SELECT message_type, COUNT(*) as cnt FROM messages GROUP BY message_type"):
        stats.setdefault("by_type", {})[row["message_type"]] = row["cnt"]
    # Total
    stats["total"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    # Recent (last hour)
    stats["last_hour"] = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE created_at > datetime('now', '-1 hour')"
    ).fetchone()[0]

    conn.close()
    return jsonify(stats)


@bp.route("/correlation/<corr_id>", methods=["GET"])
def by_correlation(corr_id: str):
    """Get all messages with the same correlation_id (thread view)."""
    conn = sqlite3.connect(str(IPC_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM messages WHERE correlation_id = ? ORDER BY created_at ASC",
        (corr_id,),
    ).fetchall()
    conn.close()

    messages = []
    for r in rows:
        messages.append({
            "message_id": r["message_id"],
            "from_profile": r["from_profile"],
            "to_profile": r["to_profile"],
            "message_type": r["message_type"],
            "payload": _parse_json(r["payload"]),
            "correlation_id": r["correlation_id"],
            "priority": r["priority"],
            "status": r["status"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
        })

    return jsonify({"correlation_id": corr_id, "messages": messages})


@bp.route("/send", methods=["POST"])
def send_message():
    data = request.get_json(force=True)
    try:
        msg_type = MessageType(data["message_type"])
        priority = MessagePriority(data.get("priority", "normal"))
        msg_id = get_bus().send(
            from_profile=data["from_profile"],
            to_profile=data["to_profile"],
            message_type=msg_type,
            payload=data.get("payload", {}),
            correlation_id=data.get("correlation_id"),
            priority=priority,
        )
        return jsonify({"message_id": msg_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


def _parse_json(val):
    """Parse a JSON string or return as-is."""
    if isinstance(val, str):
        try:
            import json
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val
