# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (core has zero external deps)
pip install -e .
pip install -e ".[dev]"   # with test deps
pip install -e ".[ui]"    # with Flask + websockets for the dashboard

# Tests
python -m pytest                          # full suite
python -m pytest tests/test_registry/    # single subsystem
python -m pytest -k "test_create_profile"  # by name
python -m pytest -x --tb=long            # stop on first failure, full traceback

# CLI
python -m core --help
python -m core show-org-chart
python -m core list-profiles [--json]

# Dashboard (Flask + WebSocket)
python -m ui                  # http://localhost:5000, ws://localhost:5001/ws
python -m ui --port 8080 --ws-port 8081

# Utility scripts
python scripts/sync_hermes_profiles.py --show-chart
python scripts/bench_ipc.py
```

## Architecture

This project adds a **coordination layer on top of Hermes agent profiles** — hierarchy, messaging, delegation, and shared memory — without replacing Hermes itself. The core is pure Python 3.10+ stdlib; all persistence is SQLite.

### Three-layer structure

**Layer 1 — `core/`** (stdlib only, no external deps):
- `registry/` — Profile registry with org-chart hierarchy and role enforcement. Four roles: `ceo` (singleton, auto-created as `hermes`), `department_head`, `project_manager`, `specialist`. Validates no circular refs, one CEO, valid parents.
- `ipc/` — SQLite-backed message bus. Six message types (TASK_REQUEST, TASK_RESPONSE, STATUS_QUERY, STATUS_RESPONSE, BROADCAST, ESCALATION), three priority levels, TTL-based expiry, and auto-archival.
- `workers/` — Subagent lifecycle state machine (RUNNING ↔ SLEEPING → COMPLETED → ARCHIVED) with serialization for save/resume.
- `memory/` — Per-profile scoped memory with four tiers (HOT → WARM → COOL → COLD) that age by access time. Includes a shared `KnowledgeBase`, budget-enforced `GarbageCollector`, and `ContextManager` that builds context from multiple sources. Read access flows up the chain of command only (no sideways access).
- `integration/` — `ChainOrchestrator` manages end-to-end delegation chains across hops; `ResultCollector` propagates worker results back up.

**Layer 2 — `integrations/`**:
- `hermes/` — Bridges to existing Hermes profiles: `ProfileBridge` discovers and syncs them; `GatewayHook` handles incoming IPC messages; `MemoryBridge` bidirectionally syncs native `MEMORY.md` ↔ SQLite hierarchy memory; `IPCListener` polls every 2 seconds.
- `claude_code/` — Context generation for Claude Code agents.

**Layer 3 — `tools/` and `ui/`**:
- `tools/hierarchy_tools.py` — 12 agent-callable functions covering messaging, org visibility, worker spawning, memory read/write, and knowledge sharing.
- `ui/` — Flask dashboard with WebSocket real-time updates.

### Database layout

All state lives in SQLite under `~/.hermes/hierarchy/`:
```
registry.db          # org chart
ipc.db               # message bus
chains.db            # delegation chain tracking
memory/<profile>.db  # per-profile scoped memory
memory/knowledge.db  # shared knowledge base
workers/<pm>/subagents.db  # per-PM worker registry
```

All database classes use `threading.Lock()` and WAL mode for concurrent access.

### Key design decisions

- **Extend Hermes, don't replace it** — hierarchy sits alongside existing profiles.
- **Stdlib only in `core/`** — if you need a dep in core, that's a red flag.
- **`typing.Protocol` for interfaces** — structural subtyping, not inheritance. See `core/*/interface.py` files.
- **ID format** — prefix + truncated UUID (e.g., `msg-a1b2c3d4e5f6`).
- **Memory access is read-up-only** — a specialist can read its PM's memory, but not a sibling PM's.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_PROFILES_DIR` | `~/.hermes/profiles/` | Hermes profile storage |
| `HERMES_DB_BASE_DIR` | `~/.hermes/hierarchy/` | Hierarchy database storage |
| `HERMES_POLL_INTERVAL` | `2.0` | IPC polling interval (seconds) |
| `HIERARCHY_PROJECT_ROOT` | auto-detected | Repo root for tool resolution |
