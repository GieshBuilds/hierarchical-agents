"""Org tree API endpoint."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ui.services import get_registry

bp = Blueprint("org_tree", __name__, url_prefix="/api")


def _build_tree(registry, root_name: str | None = None) -> list[dict]:
    """Build a nested tree structure from the profile registry."""
    profiles = registry.list_profiles()
    by_name = {p.profile_name: p for p in profiles}
    children_map: dict[str | None, list] = {}

    for p in profiles:
        parent = p.parent_profile
        children_map.setdefault(parent, []).append(p)

    def _node(profile) -> dict:
        kids = children_map.get(profile.profile_name, [])
        return {
            "name": profile.profile_name,
            "display_name": profile.display_name,
            "role": profile.role,
            "status": profile.status,
            "department": profile.department,
            "children": [_node(c) for c in sorted(kids, key=lambda x: x.profile_name)],
        }

    if root_name and root_name in by_name:
        return [_node(by_name[root_name])]

    # Find roots (profiles with no parent or parent not in registry)
    roots = [p for p in profiles if p.parent_profile is None or p.parent_profile not in by_name]
    return [_node(r) for r in sorted(roots, key=lambda x: x.profile_name)]


@bp.route("/org-tree", methods=["GET"])
def org_tree():
    root = request.args.get("root")
    tree = _build_tree(get_registry(), root)
    return jsonify(tree)
