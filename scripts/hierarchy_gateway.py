#!/usr/bin/env python3
"""Standalone gateway daemon for a single Hermes hierarchy profile.

Launched automatically by HermesProfileActivator when a profile receives
its first IPC message. Can also be started manually.

Usage
-----
    # Start a long-running gateway for a profile (background daemon):
    python scripts/hierarchy_gateway.py start <profile_name>

    # Process pending messages once and exit (cron-friendly):
    python scripts/hierarchy_gateway.py process <profile_name>

    # Stop a running gateway by sending SIGTERM to its PID:
    python scripts/hierarchy_gateway.py stop <profile_name>

Environment Variables
---------------------
    HERMES_PROFILES_DIR   Path to ~/.hermes/profiles/  (default: auto)
    HERMES_DB_BASE_DIR    Path to hierarchy databases   (default: ~/.hermes/hierarchy/)
    HERMES_POLL_INTERVAL  Seconds between IPC polls     (default: 2.0)
"""
from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from integrations.hermes.config import HermesConfig
from integrations.hermes.gateway_hook import GatewayHook, HIERARCHY_DIR, LOGS_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hierarchy_gateway")


def _pid_file(profile_name: str) -> Path:
    return LOGS_DIR / f"gateway-{profile_name}.pid"


def _log_file(profile_name: str) -> Path:
    return LOGS_DIR / f"gateway-{profile_name}.log"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_start(profile_name: str) -> None:
    """Run gateway as a foreground process with signal handling.

    In production, activation.py launches this via subprocess with
    start_new_session=True so it runs detached from the parent.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    pid_file = _pid_file(profile_name)

    # Write PID file so activation.py can detect us across restarts.
    pid_file.write_text(str(os.getpid()))
    logger.info("Gateway starting for profile '%s' (pid=%d)", profile_name, os.getpid())

    config = HermesConfig.from_env()
    gateway = GatewayHook(
        profile_name=profile_name,
        config=config,
        auto_execute=True,
    )

    stop_event_triggered = False

    def _handle_signal(signum, frame):
        nonlocal stop_event_triggered
        logger.info("Signal %d received — shutting down gateway for '%s'", signum, profile_name)
        stop_event_triggered = True
        gateway.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        gateway.start()
        logger.info("Gateway running for '%s' — waiting for messages", profile_name)

        # Block main thread until a signal fires.
        while not stop_event_triggered:
            signal.pause()

    finally:
        gateway.close()
        pid_file.unlink(missing_ok=True)
        logger.info("Gateway stopped for profile '%s'", profile_name)


def cmd_process(profile_name: str) -> None:
    """Poll once, process all pending messages, then exit.

    Suitable for cron-based setups where you want on-demand processing
    rather than a persistent daemon.
    """
    config = HermesConfig.from_env()
    gateway = GatewayHook(
        profile_name=profile_name,
        config=config,
        auto_execute=True,
    )

    try:
        messages = gateway.process_once()
        logger.info("Processed %d message(s) for '%s'", len(messages), profile_name)
    finally:
        gateway.close()


def cmd_stop(profile_name: str) -> None:
    """Send SIGTERM to a running gateway identified by its PID file."""
    pid_file = _pid_file(profile_name)

    if not pid_file.exists():
        print(f"No PID file found for '{profile_name}' — gateway may not be running.")
        sys.exit(1)

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to gateway for '{profile_name}' (pid={pid})")
    except (ValueError, ProcessLookupError):
        print(f"Stale PID file for '{profile_name}' — cleaning up.")
        pid_file.unlink(missing_ok=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    profile_name = sys.argv[2]

    if command == "start":
        cmd_start(profile_name)
    elif command == "process":
        cmd_process(profile_name)
    elif command == "stop":
        cmd_stop(profile_name)
    else:
        print(f"Unknown command: {command!r}")
        print("Usage: hierarchy_gateway.py <start|process|stop> <profile_name>")
        sys.exit(1)


if __name__ == "__main__":
    main()
