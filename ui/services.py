"""Singleton service layer — wraps core modules for use by API endpoints."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from core.integration.chain_store import ChainStore
from core.ipc.message_bus import MessageBus
from core.registry.profile_registry import ProfileRegistry
from core.workers.subagent_registry import SubagentRegistry

from ui.config import (
    CHAINS_DB,
    IPC_DB,
    MEMORY_DIR,
    REGISTRY_DB,
    ROLE_TO_SCOPE,
    WORKERS_DIR,
)


# ---------------------------------------------------------------------------
# Registry adapter (MessageBus needs .get() on the profile registry)
# ---------------------------------------------------------------------------

class RegistryAdapter:
    def __init__(self, registry: ProfileRegistry) -> None:
        self._registry = registry

    def get(self, name: str):
        return self._registry.get_profile(name)

    def get_profile(self, name: str):
        return self._registry.get_profile(name)

    def get_chain_of_command(self, name: str):
        return self._registry.get_chain_of_command(name)


# ---------------------------------------------------------------------------
# Singletons (initialised lazily on first access)
# ---------------------------------------------------------------------------

_registry: Optional[ProfileRegistry] = None
_bus: Optional[MessageBus] = None
_chain_store: Optional[ChainStore] = None


def get_registry() -> ProfileRegistry:
    global _registry
    if _registry is None:
        _registry = ProfileRegistry(str(REGISTRY_DB))
    return _registry


def get_bus() -> MessageBus:
    global _bus
    if _bus is None:
        adapter = RegistryAdapter(get_registry())
        _bus = MessageBus(str(IPC_DB), profile_registry=adapter)
    return _bus


def get_chain_store() -> ChainStore:
    global _chain_store
    if _chain_store is None:
        _chain_store = ChainStore(str(CHAINS_DB))
    return _chain_store


def get_worker_registry(pm_profile: str) -> SubagentRegistry:
    """Return a SubagentRegistry for a specific PM's worker database."""
    db_path = WORKERS_DIR / pm_profile / "subagents.db"
    if not db_path.exists():
        return SubagentRegistry(":memory:")
    return SubagentRegistry(str(WORKERS_DIR))


def get_all_worker_pms() -> list[str]:
    """Return list of PM profile names that have worker databases."""
    if not WORKERS_DIR.exists():
        return []
    return sorted(
        d.name
        for d in WORKERS_DIR.iterdir()
        if d.is_dir() and (d / "subagents.db").exists()
    )


def get_memory_store(profile_name: str):
    """Return a MemoryStore for the given profile, or None if no DB exists."""
    from core.memory.memory_store import MemoryStore
    from core.memory.models import MemoryScope

    db_path = MEMORY_DIR / f"{profile_name}.db"
    if not db_path.exists():
        return None

    # Determine scope from profile role
    try:
        p = get_registry().get_profile(profile_name)
        scope_str = ROLE_TO_SCOPE.get(p.role, "project")
    except Exception:
        scope_str = "project"

    scope = MemoryScope(scope_str)
    return MemoryStore(str(db_path), profile_name=profile_name, profile_scope=scope)


def get_gateway_status(profile_name: str) -> dict:
    """Check if a gateway process is running for a profile."""
    from ui.config import LOGS_DIR

    pid_file = LOGS_DIR / f"gateway-{profile_name}.pid"
    if not pid_file.exists():
        return {"running": False, "pid": None}

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # Check if process is alive
        return {"running": True, "pid": pid}
    except (ValueError, ProcessLookupError, PermissionError):
        return {"running": False, "pid": None}


def close_all() -> None:
    """Close all open database connections."""
    global _registry, _bus, _chain_store
    for obj in (_bus, _chain_store, _registry):
        if obj is not None:
            try:
                obj.close()
            except Exception:
                pass
    _registry = _bus = _chain_store = None
