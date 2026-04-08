"""Memory store endpoints."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from dashboard.api.serializers import memory_entry_to_dict
from ui.services import get_memory_store
from ui.config import MEMORY_DIR

bp = Blueprint("memory", __name__, url_prefix="/api/memory")


@bp.route("", methods=["GET"])
def list_profiles_with_memory():
    """List profiles that have memory databases."""
    if not MEMORY_DIR.exists():
        return jsonify([])
    profiles = sorted(
        f.stem for f in MEMORY_DIR.glob("*.db")
    )
    return jsonify(profiles)


@bp.route("/<profile>", methods=["GET"])
def list_entries(profile: str):
    limit = int(request.args.get("limit", 50))
    tier = request.args.get("tier")
    entry_type = request.args.get("type")

    store = get_memory_store(profile)
    if store is None:
        return jsonify({"entries": [], "total": 0})

    try:
        entries = store.list_entries(limit=limit)
        result = []
        for e in entries:
            d = memory_entry_to_dict(e)
            if tier and d.get("tier") != tier:
                continue
            if entry_type and d.get("entry_type") != entry_type:
                continue
            result.append(d)
        return jsonify({"entries": result, "total": len(result)})
    finally:
        store.close()


@bp.route("/<profile>/search", methods=["GET"])
def search_memory(profile: str):
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    store = get_memory_store(profile)
    if store is None:
        return jsonify({"entries": []})

    try:
        entries = store.search(q, limit=20)
        return jsonify({"entries": [memory_entry_to_dict(e) for e in entries]})
    finally:
        store.close()
