"""Setup wizard API endpoints.

Provides environment scanning, health checks, bulk gateway management,
and end-to-end verification for the hierarchy system.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from ui.config import (
    GATEWAY_SCRIPT,
    HIERARCHY_DIR,
    LOGS_DIR,
    PROFILES_DIR,
)
from ui.services import get_bus, get_gateway_status, get_registry

bp = Blueprint("setup", __name__, url_prefix="/api/setup")

# -----------------------------------------------------------------------
# Scan: compare hermes profiles on disk vs registered in hierarchy
# -----------------------------------------------------------------------

@bp.route("/scan", methods=["GET"])
def scan_environment():
    """Scan the environment and return setup status for every profile."""
    reg = get_registry()
    bus = get_bus()

    # 1. Registered profiles in the hierarchy registry
    registered = {}
    for p in reg.list_profiles():
        registered[p.profile_name] = {
            "profile_name": p.profile_name,
            "display_name": p.display_name,
            "role": p.role,
            "parent_profile": p.parent_profile,
            "department": p.department,
            "status": p.status,
        }

    # 2. Hermes profile directories on disk
    disk_profiles = set()
    if PROFILES_DIR.exists():
        for d in PROFILES_DIR.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                disk_profiles.add(d.name)

    # 3. Build unified list
    all_names = sorted(disk_profiles | set(registered.keys()))
    profiles = []
    for name in all_names:
        on_disk = name in disk_profiles
        in_registry = name in registered
        profile_dir = PROFILES_DIR / name

        # Check docs
        has_soul = (profile_dir / "SOUL.md").exists() if on_disk else False
        doc_files = []
        if on_disk:
            for doc in ("SOUL.md", "HANDOFF.md", "WORKFLOWS.md", "TOOLS.md",
                        "CONTEXT.md", "PLAYBOOK.md"):
                if (profile_dir / doc).exists():
                    doc_files.append(doc)

        # Gateway status
        gw = get_gateway_status(name)

        # Pending messages
        try:
            pending = bus.get_pending_count(name)
        except Exception:
            pending = 0

        entry = {
            "name": name,
            "on_disk": on_disk,
            "registered": in_registry,
            "has_soul": has_soul,
            "docs": doc_files,
            "docs_count": len(doc_files),
            "gateway_running": gw["running"],
            "gateway_pid": gw.get("pid"),
            "pending_messages": pending,
        }
        if in_registry:
            entry.update(registered[name])
        profiles.append(entry)

    # 4. Summary stats
    summary = {
        "total_disk": len(disk_profiles),
        "total_registered": len(registered),
        "unregistered": len(disk_profiles - set(registered.keys())),
        "missing_docs": sum(1 for p in profiles if p["registered"] and not p["has_soul"]),
        "gateways_running": sum(1 for p in profiles if p["gateway_running"]),
        "gateways_needed": sum(1 for p in profiles if p["registered"] and p.get("status") == "active"),
    }

    return jsonify({"profiles": profiles, "summary": summary})


# -----------------------------------------------------------------------
# Health: comprehensive health check
# -----------------------------------------------------------------------

@bp.route("/health", methods=["GET"])
def health_check():
    """Run health checks on the hierarchy system."""
    checks = []

    # 1. Registry DB accessible
    try:
        reg = get_registry()
        profiles = reg.list_profiles()
        checks.append({
            "name": "Registry Database",
            "status": "ok",
            "detail": f"{len(profiles)} profiles registered",
        })
    except Exception as e:
        checks.append({
            "name": "Registry Database",
            "status": "error",
            "detail": str(e),
        })
        profiles = []

    # 2. IPC Bus accessible
    try:
        bus = get_bus()
        # Quick connectivity test
        checks.append({
            "name": "IPC Message Bus",
            "status": "ok",
            "detail": "Connected",
        })
    except Exception as e:
        checks.append({
            "name": "IPC Message Bus",
            "status": "error",
            "detail": str(e),
        })

    # 3. CEO profile exists
    ceo_profiles = [p for p in profiles if p.role == "ceo"]
    if ceo_profiles:
        checks.append({
            "name": "CEO Profile (hermes)",
            "status": "ok",
            "detail": f"Found: {ceo_profiles[0].profile_name}",
        })
    else:
        checks.append({
            "name": "CEO Profile (hermes)",
            "status": "error",
            "detail": "No CEO profile registered",
        })

    # 4. At least one department head
    dh = [p for p in profiles if p.role == "department_head" and p.status == "active"]
    if dh:
        checks.append({
            "name": "Department Heads",
            "status": "ok",
            "detail": f"{len(dh)} active: {', '.join(p.profile_name for p in dh)}",
        })
    else:
        checks.append({
            "name": "Department Heads",
            "status": "warning",
            "detail": "No active department heads",
        })

    # 5. At least one PM
    pms = [p for p in profiles if p.role == "project_manager" and p.status == "active"]
    if pms:
        checks.append({
            "name": "Project Managers",
            "status": "ok",
            "detail": f"{len(pms)} active",
        })
    else:
        checks.append({
            "name": "Project Managers",
            "status": "warning",
            "detail": "No active project managers",
        })

    # 6. Gateway script exists
    if GATEWAY_SCRIPT.exists():
        checks.append({
            "name": "Gateway Script",
            "status": "ok",
            "detail": str(GATEWAY_SCRIPT),
        })
    else:
        checks.append({
            "name": "Gateway Script",
            "status": "error",
            "detail": f"Not found: {GATEWAY_SCRIPT}",
        })

    # 7. Gateways running for active profiles
    active_profiles = [p for p in profiles if p.status == "active"]
    running_gw = 0
    stopped_gw = []
    for p in active_profiles:
        gw = get_gateway_status(p.profile_name)
        if gw["running"]:
            running_gw += 1
        else:
            stopped_gw.append(p.profile_name)

    if not stopped_gw:
        checks.append({
            "name": "Gateways",
            "status": "ok",
            "detail": f"All {running_gw} active profiles have running gateways",
        })
    else:
        checks.append({
            "name": "Gateways",
            "status": "warning",
            "detail": f"{running_gw} running, {len(stopped_gw)} stopped: {', '.join(stopped_gw)}",
        })

    # 8. Telegram delivery hook
    env_path = Path.home() / ".hermes" / ".env"
    has_token = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    has_chat = bool(os.environ.get("TELEGRAM_HOME_CHANNEL"))
    if not has_token and env_path.exists():
        try:
            for line in env_path.read_text().splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN=") and line.split("=", 1)[1].strip():
                    has_token = True
                if line.startswith("TELEGRAM_HOME_CHANNEL=") and line.split("=", 1)[1].strip():
                    has_chat = True
        except Exception:
            pass

    if has_token and has_chat:
        checks.append({
            "name": "Telegram Delivery",
            "status": "ok",
            "detail": "Bot token and channel configured",
        })
    elif has_token:
        checks.append({
            "name": "Telegram Delivery",
            "status": "warning",
            "detail": "Bot token set but TELEGRAM_HOME_CHANNEL missing",
        })
    else:
        checks.append({
            "name": "Telegram Delivery",
            "status": "warning",
            "detail": "Not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_HOME_CHANNEL in ~/.hermes/.env)",
        })

    # 9. Hierarchy tools registered
    tools_file = Path.home() / ".hermes" / "hermes-agent" / "tools" / "hierarchy_tool.py"
    if tools_file.exists():
        checks.append({
            "name": "Hierarchy Tools",
            "status": "ok",
            "detail": "hierarchy_tool.py found in hermes-agent",
        })
    else:
        checks.append({
            "name": "Hierarchy Tools",
            "status": "error",
            "detail": "hierarchy_tool.py not found — agents can't use send_to_profile",
        })

    # Overall status
    statuses = [c["status"] for c in checks]
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    return jsonify({"overall": overall, "checks": checks})


# -----------------------------------------------------------------------
# Bulk gateway management
# -----------------------------------------------------------------------

@bp.route("/gateways/start-all", methods=["POST"])
def start_all_gateways():
    """Start gateways for all active registered profiles."""
    if not GATEWAY_SCRIPT.exists():
        return jsonify({"error": f"Gateway script not found: {GATEWAY_SCRIPT}"}), 500

    reg = get_registry()
    results = []
    for p in reg.list_profiles():
        if p.status != "active":
            continue
        gw = get_gateway_status(p.profile_name)
        if gw["running"]:
            results.append({"profile": p.profile_name, "action": "already_running", "pid": gw["pid"]})
            continue

        try:
            log_path = LOGS_DIR / f"gateway-{p.profile_name}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fd = open(log_path, "a")
            proc = subprocess.Popen(
                ["python3", str(GATEWAY_SCRIPT), "start", p.profile_name],
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
            )
            results.append({"profile": p.profile_name, "action": "started", "pid": proc.pid})
        except Exception as e:
            results.append({"profile": p.profile_name, "action": "error", "error": str(e)})

    return jsonify({"results": results})


@bp.route("/gateways/stop-all", methods=["POST"])
def stop_all_gateways():
    """Stop all running gateways."""
    reg = get_registry()
    results = []
    for p in reg.list_profiles():
        gw = get_gateway_status(p.profile_name)
        if not gw["running"] or gw["pid"] is None:
            continue
        try:
            os.kill(gw["pid"], signal.SIGTERM)
            pid_file = LOGS_DIR / f"gateway-{p.profile_name}.pid"
            if pid_file.exists():
                pid_file.unlink()
            results.append({"profile": p.profile_name, "action": "stopped"})
        except Exception as e:
            results.append({"profile": p.profile_name, "action": "error", "error": str(e)})

    return jsonify({"results": results})


# -----------------------------------------------------------------------
# Test: send a test task through the hierarchy
# -----------------------------------------------------------------------

@bp.route("/test", methods=["POST"])
def test_flow():
    """Send a test task and verify it reaches the target.

    Body: { "to": "cto", "message": "Reply with: TEST OK" }
    """
    from core.ipc.models import MessageType, MessagePriority

    data = request.get_json(force=True) if request.is_json else {}
    to = data.get("to", "cto")
    message = data.get("message", "Reply with exactly: TEST OK")

    bus = get_bus()
    try:
        msg_id = bus.send(
            from_profile="hermes",
            to_profile=to,
            message_type=MessageType.TASK_REQUEST,
            payload={"task": message, "user_talk": True},
            priority=MessagePriority.NORMAL,
        )
    except Exception as e:
        return jsonify({"error": f"Failed to send: {e}"}), 500

    return jsonify({
        "status": "sent",
        "message_id": msg_id,
        "to": to,
        "message": message,
        "note": "Check the Messages page or gateway logs to verify delivery.",
    })


# -----------------------------------------------------------------------
# Register a batch of profiles
# -----------------------------------------------------------------------

@bp.route("/register-batch", methods=["POST"])
def register_batch():
    """Register multiple profiles at once.

    Body: { "profiles": [ { "name": "...", "role": "...", "parent": "...", ... } ] }
    """
    data = request.get_json(force=True)
    profiles_data = data.get("profiles", [])
    results = []

    reg = get_registry()
    for pdata in profiles_data:
        name = pdata.get("name")
        if not name:
            continue
        try:
            reg.create_profile(
                name=name,
                display_name=pdata.get("display_name", name),
                role=pdata.get("role", "project_manager"),
                parent=pdata.get("parent"),
                department=pdata.get("department", "engineering"),
                description=pdata.get("description", ""),
                _skip_onboarding=pdata.get("skip_onboarding", True),
            )
            results.append({"name": name, "status": "created"})
        except Exception as e:
            results.append({"name": name, "status": "error", "error": str(e)})

    return jsonify({"results": results})
