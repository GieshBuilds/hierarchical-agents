"""Worker/subagent endpoints."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from dashboard.api.serializers import subagent_to_dict
from ui.services import get_all_worker_pms, get_worker_registry

bp = Blueprint("workers", __name__, url_prefix="/api/workers")


@bp.route("", methods=["GET"])
def list_workers():
    """List workers across all PMs, or filtered by PM."""
    pm_filter = request.args.get("pm")
    status_filter = request.args.get("status")
    limit = int(request.args.get("limit", 50))

    pms = [pm_filter] if pm_filter else get_all_worker_pms()
    workers = []

    for pm in pms:
        try:
            reg = get_worker_registry(pm)
            items = reg.list(project_manager=pm, limit=limit)
            for w in items:
                d = subagent_to_dict(w)
                if status_filter and d["status"] != status_filter:
                    continue
                workers.append(d)
            reg.close()
        except Exception:
            continue

    # Sort by created_at descending
    workers.sort(key=lambda w: w.get("created_at") or "", reverse=True)
    return jsonify({"workers": workers[:limit], "total": len(workers)})


@bp.route("/stats", methods=["GET"])
def worker_stats():
    """Aggregate worker stats across all PMs."""
    pms = get_all_worker_pms()
    stats = {"by_pm": {}, "by_status": {}, "total": 0}

    for pm in pms:
        try:
            reg = get_worker_registry(pm)
            items = reg.list(project_manager=pm, limit=500)
            pm_stats = {}
            for w in items:
                s = w.status if isinstance(w.status, str) else w.status.value
                pm_stats[s] = pm_stats.get(s, 0) + 1
                stats["by_status"][s] = stats["by_status"].get(s, 0) + 1
                stats["total"] += 1
            stats["by_pm"][pm] = pm_stats
            reg.close()
        except Exception:
            continue

    return jsonify(stats)
