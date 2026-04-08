"""SQLite database watcher — detects changes and publishes events."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ui.config import DB_POLL_INTERVAL, IPC_DB, LOGS_DIR, WORKERS_DIR
from ui.realtime.event_bus import EventBus

logger = logging.getLogger(__name__)


class DatabaseWatcher:
    """Polls SQLite databases for changes and publishes events.

    Watches:
    - IPC messages (new messages, status changes)
    - Worker status changes
    - Gateway process liveness
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # High-water marks for change detection
        self._last_message_time = _now_iso()
        self._last_message_count = 0
        self._last_worker_snapshot: dict[str, dict[str, int]] = {}
        self._last_gateway_pids: dict[str, int | None] = {}

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="db-watcher",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._check_messages()
            except Exception as e:
                logger.debug("Message check error: %s", e)

            try:
                self._check_workers()
            except Exception as e:
                logger.debug("Worker check error: %s", e)

            try:
                self._check_gateways()
            except Exception as e:
                logger.debug("Gateway check error: %s", e)

            self._stop_event.wait(timeout=DB_POLL_INTERVAL)

    def _check_messages(self) -> None:
        """Detect new IPC messages."""
        if not IPC_DB.exists():
            return

        conn = sqlite3.connect(str(IPC_DB))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT message_id, from_profile, to_profile, message_type,
                          priority, status, created_at, substr(payload, 1, 500) as payload_preview
                   FROM messages
                   WHERE created_at > ?
                   ORDER BY created_at ASC""",
                (self._last_message_time,),
            ).fetchall()

            for r in rows:
                self._bus.publish({
                    "type": f"message.{r['message_type']}",
                    "message_id": r["message_id"],
                    "from_profile": r["from_profile"],
                    "to_profile": r["to_profile"],
                    "message_type": r["message_type"],
                    "priority": r["priority"],
                    "status": r["status"],
                    "payload_preview": r["payload_preview"],
                    "created_at": r["created_at"],
                })
                self._last_message_time = r["created_at"]

        finally:
            conn.close()

    def _check_workers(self) -> None:
        """Detect worker status changes."""
        if not WORKERS_DIR.exists():
            return

        for pm_dir in WORKERS_DIR.iterdir():
            if not pm_dir.is_dir():
                continue
            db_path = pm_dir / "subagents.db"
            if not db_path.exists():
                continue

            pm = pm_dir.name
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT subagent_id, status, task_goal FROM subagents ORDER BY created_at DESC LIMIT 20"
                ).fetchall()

                current = {}
                for r in rows:
                    current[r["subagent_id"]] = r["status"]

                prev = self._last_worker_snapshot.get(pm, {})

                # Detect new or changed workers
                for sid, status in current.items():
                    if sid not in prev:
                        # Find goal for this worker
                        goal = next((r["task_goal"] for r in rows if r["subagent_id"] == sid), "")
                        self._bus.publish({
                            "type": "worker.spawned",
                            "subagent_id": sid,
                            "project_manager": pm,
                            "status": status,
                            "task_goal": goal[:200],
                        })
                    elif prev[sid] != status:
                        self._bus.publish({
                            "type": f"worker.{status}",
                            "subagent_id": sid,
                            "project_manager": pm,
                            "status": status,
                        })

                self._last_worker_snapshot[pm] = current
            finally:
                conn.close()

    def _check_gateways(self) -> None:
        """Detect gateway start/stop by checking PID files."""
        if not LOGS_DIR.exists():
            return

        current_pids: dict[str, int | None] = {}
        for pid_file in LOGS_DIR.glob("gateway-*.pid"):
            profile = pid_file.stem.replace("gateway-", "")
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)  # Check alive
                current_pids[profile] = pid
            except (ValueError, ProcessLookupError, PermissionError):
                current_pids[profile] = None

        # Detect changes
        all_profiles = set(current_pids) | set(self._last_gateway_pids)
        for profile in all_profiles:
            was_running = self._last_gateway_pids.get(profile) is not None
            is_running = current_pids.get(profile) is not None

            if is_running and not was_running:
                self._bus.publish({
                    "type": "gateway.started",
                    "profile": profile,
                    "pid": current_pids[profile],
                })
            elif was_running and not is_running:
                self._bus.publish({
                    "type": "gateway.stopped",
                    "profile": profile,
                })

        self._last_gateway_pids = current_pids


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
