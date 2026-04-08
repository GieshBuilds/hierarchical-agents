"""Profile file (SOUL.md, config) read/write endpoints."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from ui.config import HERMES_HOME, PROFILES_DIR

bp = Blueprint("files", __name__, url_prefix="/api/files")


def _profile_dir(profile_name: str) -> Path:
    return PROFILES_DIR / profile_name


def _find_soul_md(profile_name: str) -> Path:
    """Find SOUL.md — hermes keeps it at ~/.hermes/SOUL.md, others under profiles/."""
    profile_path = _profile_dir(profile_name) / "SOUL.md"
    if profile_path.exists():
        return profile_path
    # Fallback: hermes root SOUL.md
    root_path = HERMES_HOME / "SOUL.md"
    if profile_name == "hermes" and root_path.exists():
        return root_path
    return profile_path  # Default to profile path (may not exist)


@bp.route("/<profile>/soul", methods=["GET"])
def read_soul(profile: str):
    path = _find_soul_md(profile)
    if not path.exists():
        return jsonify({"content": "", "exists": False})
    return jsonify({"content": path.read_text(encoding="utf-8"), "exists": True})


@bp.route("/<profile>/soul", methods=["PUT"])
def write_soul(profile: str):
    data = request.get_json(force=True)
    content = data.get("content", "")
    path = _profile_dir(profile) / "SOUL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return jsonify({"saved": True, "path": str(path)})


@bp.route("/<profile>/files", methods=["GET"])
def list_files(profile: str):
    """List all files in a profile's directory."""
    pdir = _profile_dir(profile)
    if not pdir.exists():
        return jsonify({"files": []})
    files = []
    for f in sorted(pdir.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(pdir))
            files.append({
                "name": rel,
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
    return jsonify({"files": files})


@bp.route("/<profile>/file/<path:filepath>", methods=["GET"])
def read_file(profile: str, filepath: str):
    path = _profile_dir(profile) / filepath
    if not path.exists():
        return jsonify({"error": "File not found"}), 404
    # Safety: ensure path is under profile dir
    try:
        path.resolve().relative_to(_profile_dir(profile).resolve())
    except ValueError:
        return jsonify({"error": "Path traversal not allowed"}), 403
    try:
        content = path.read_text(encoding="utf-8")
        return jsonify({"content": content, "path": filepath})
    except UnicodeDecodeError:
        return jsonify({"error": "Binary file, cannot display"}), 400


@bp.route("/<profile>/file/<path:filepath>", methods=["PUT"])
def write_file(profile: str, filepath: str):
    data = request.get_json(force=True)
    content = data.get("content", "")
    path = _profile_dir(profile) / filepath
    # Safety check
    try:
        path.resolve().relative_to(_profile_dir(profile).resolve())
    except ValueError:
        return jsonify({"error": "Path traversal not allowed"}), 403
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return jsonify({"saved": True, "path": filepath})
