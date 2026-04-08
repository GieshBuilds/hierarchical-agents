"""Hermes framework integration adapter.

Provides bridge classes that connect Hermes-specific profile directories,
worker spawning, IPC polling, and message routing to the core hierarchy
architecture.
"""
from __future__ import annotations

from integrations.hermes.activation import HermesProfileActivator
from integrations.hermes.config import HermesConfig
from integrations.hermes.ipc_listener import IPCListener
from integrations.hermes.message_router import HermesMessageRouter
from integrations.hermes.profile_bridge import ProfileBridge, SyncReport
from integrations.hermes.worker_bridge import WorkerBridge

__all__ = [
    "HermesConfig",
    "HermesMessageRouter",
    "HermesProfileActivator",
    "IPCListener",
    "ProfileBridge",
    "SyncReport",
    "WorkerBridge",
]
