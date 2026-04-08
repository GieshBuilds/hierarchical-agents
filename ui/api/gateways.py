"""Gateway process management endpoints."""
from __future__ import annotations

import os
import signal
import subprocess

from flask import Blueprint, jsonify

from ui.config import GATEWAY_SCRIPT, LOGS_DIR
from ui.services import get_bus, get_gateway_status, get_registry

bp = Blueprint("gateways", __name__, url_prefix="/api/gateways")


@bp.route("", methods=["GET"])
def list_gateways():
    """List all profiles with their gateway status."""
    reg = get_registry()
    bus = get_bus()
    result = []
    for p in reg.list_profiles():
        status = get_gateway_status(p.profile_name)
        try:
            pending = bus.get_pending_count(p.profile_name)
        except Exception:
            pending = 0
        result.append({
            "profile": p.profile_name,
            "display_name": p.display_name,
            "role": p.role,
            **status,
            "pending_messages": pending,
        })
    return jsonify(result)


@bp.route("/<profile>", methods=["GET"])
def gateway_status(profile: str):
    status = get_gateway_status(profile)
    try:
        pending = get_bus().get_pending_count(profile)
    except Exception:
        pending = 0
    return jsonify({**status, "profile": profile, "pending_messages": pending})


@bp.route("/<profile>/start", methods=["POST"])
def start_gateway(profile: str):
    status = get_gateway_status(profile)
    if status["running"]:
        return jsonify({"error": "Gateway already running", **status}), 409

    if not GATEWAY_SCRIPT.exists():
        return jsonify({"error": f"Gateway script not found: {GATEWAY_SCRIPT}"}), 500

    proc = subprocess.Popen(
        ["python3", str(GATEWAY_SCRIPT), "start", profile],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return jsonify({"started": True, "profile": profile, "pid": proc.pid})


@bp.route("/<profile>/stop", methods=["POST"])
def stop_gateway(profile: str):
    status = get_gateway_status(profile)
    if not status["running"] or status["pid"] is None:
        return jsonify({"error": "Gateway not running"}), 404

    try:
        os.kill(status["pid"], signal.SIGTERM)
        # Clean up PID file
        pid_file = LOGS_DIR / f"gateway-{profile}.pid"
        if pid_file.exists():
            pid_file.unlink()
        return jsonify({"stopped": True, "profile": profile})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/<profile>/logs", methods=["GET"])
def gateway_logs(profile: str):
    """Return the last N lines of a gateway's log file."""
    log_file = LOGS_DIR / f"gateway-{profile}.log"
    if not log_file.exists():
        return jsonify({"lines": [], "exists": False})

    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
        lines = text.strip().split("\n")
        # Return last 200 lines
        return jsonify({"lines": lines[-200:], "exists": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
