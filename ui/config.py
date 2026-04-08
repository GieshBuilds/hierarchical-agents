"""Configuration for the hierarchy management UI server."""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERMES_HOME = Path.home() / ".hermes"
HIERARCHY_DIR = HERMES_HOME / "hierarchy"
PROFILES_DIR = HERMES_HOME / "profiles"

REGISTRY_DB = HIERARCHY_DIR / "registry.db"
IPC_DB = HIERARCHY_DIR / "ipc.db"
CHAINS_DB = HIERARCHY_DIR / "chains.db"
WORKERS_DIR = HIERARCHY_DIR / "workers"
MEMORY_DIR = HIERARCHY_DIR / "memory"
LOGS_DIR = HIERARCHY_DIR / "logs"

GATEWAY_SCRIPT = HIERARCHY_DIR / "hierarchy_gateway.py"
CONFIG_YAML = HERMES_HOME / "config.yaml"

# ---------------------------------------------------------------------------
# Server defaults
# ---------------------------------------------------------------------------

HOST = "0.0.0.0"
HTTP_PORT = 8765
WS_PORT = 8766

# ---------------------------------------------------------------------------
# Real-time
# ---------------------------------------------------------------------------

DB_POLL_INTERVAL = 1.5  # seconds between SQLite change checks
LOG_TAIL_INTERVAL = 1.0  # seconds between log file checks

# ---------------------------------------------------------------------------
# Role -> memory scope mapping
# ---------------------------------------------------------------------------

ROLE_TO_SCOPE = {
    "ceo": "strategic",
    "department_head": "domain",
    "project_manager": "project",
    "specialist": "task",
}
