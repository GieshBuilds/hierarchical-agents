"""Aggregated dashboard snapshot endpoint."""
from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify

from dashboard.api.serializers import profile_to_dict
from ui.config import IPC_DB, MEMORY_DIR
from ui.services import (
    get_all_worker_pms,
    get_bus,
    get_chain_store,
    get_gateway_status,
    get_registry,
    get_worker_registry,
)

bp = Blueprint("dashboard", __name__, url_prefix="/api")


@bp.route("/dashboard", methods=["GET"])
def dashboard():
    reg = get_registry()
    profiles = reg.list_profiles()

    # Enrich profiles with gateway/worker/message counts
    enriched = []
    for p in profiles:
        d = profile_to_dict(p)
        d["gateway"] = get_gateway_status(p.profile_name)
        try:
            d["pending_messages"] = get_bus().get_pending_count(p.profile_name)
        except Exception:
            d["pending_messages"] = 0
        enriched.append(d)

    # Worker summary
    worker_summary = {"running": 0, "completed": 0, "failed": 0, "total": 0}
    for pm in get_all_worker_pms():
        try:
            wreg = get_worker_registry(pm)
            for w in wreg.list(project_manager=pm, limit=500):
                s = w.status if isinstance(w.status, str) else w.status.value
                worker_summary["total"] += 1
                if s in worker_summary:
                    worker_summary[s] += 1
            wreg.close()
        except Exception:
            pass

    # Chain summary
    chain_summary = {"pending": 0, "active": 0, "completed": 0, "failed": 0, "total": 0}
    try:
        chains = get_chain_store().list()
        for c in chains:
            s = c.status.value if hasattr(c.status, "value") else str(c.status)
            chain_summary["total"] += 1
            if s in chain_summary:
                chain_summary[s] += 1
    except Exception:
        pass

    # Message summary
    msg_summary = {"total": 0, "pending": 0, "last_hour": 0}
    try:
        conn = sqlite3.connect(str(IPC_DB))
        msg_summary["total"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        msg_summary["pending"] = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE status = 'pending'"
        ).fetchone()[0]
        msg_summary["last_hour"] = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at > datetime('now', '-1 hour')"
        ).fetchone()[0]
        conn.close()
    except Exception:
        pass

    return jsonify({
        "profiles": enriched,
        "workers": worker_summary,
        "chains": chain_summary,
        "messages": msg_summary,
    })
