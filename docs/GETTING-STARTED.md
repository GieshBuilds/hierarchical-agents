# Getting Started

This guide walks you through setting up a hierarchical agent organization from scratch.

## Prerequisites

- **Hermes** installed and working
- Python 3.10 or later
- No external dependencies required for core (stdlib only)

> **New to this project?** Start with the [Hermes Integration Guide](HERMES-INTEGRATION.md) — it walks through connecting to your existing Hermes setup end-to-end.

## Installation

```bash
git clone https://github.com/GieshBuilds/hierarchical-agents.git
cd hierarchical-agents

# Optional: install in editable mode (enables `python -m core`)
pip install -e .

# Optional: install dev dependencies for running tests
pip install -e ".[dev]"
```

## Core Concepts

The system models an **organizational chart** for AI agents. Every agent has a profile with a role, a position in the hierarchy, and access to messaging, memory, and task delegation.

### Roles

| Role | Description | Reports To |
|------|-------------|------------|
| **CEO** | Top of the hierarchy. Auto-created as `hermes` on init. | Nobody |
| **Department Head** | Domain owner (e.g., CTO, CMO). Optional layer. | Any profile |
| **Project Manager** | Manages a specific project. Spawns workers. | Any profile (CEO, Dept Head, etc.) |
| **Specialist** | Persistent expert agent for complex subtasks. | Any profile |

### The Five Subsystems

| Subsystem | What It Does |
|-----------|-------------|
| **Registry** | Profile CRUD, hierarchy rules, org chart |
| **IPC** | Inter-profile messaging with priority, TTL, correlation |
| **Workers** | Subagent lifecycle (spawn, sleep, resume, complete) |
| **Memory** | Per-profile scoped memory with tiered storage and GC |
| **Integration** | Delegation chains with result propagation |

---

## Step 1: Create the Hierarchy

```python
from core.registry import ProfileRegistry

# Initialize registry — CEO 'hermes' is created automatically
registry = ProfileRegistry("./registry.db")

# PMs can report directly to the CEO
registry.create_profile(
    name="pm-backend",
    display_name="Backend PM",
    role="project_manager",
    parent="hermes",
)

registry.create_profile(
    name="pm-frontend",
    display_name="Frontend PM",
    role="project_manager",
    parent="hermes",
)

# Add a specialist under a PM
registry.create_profile(
    name="dev-backend",
    display_name="Backend Developer",
    role="specialist",
    parent="pm-backend",
)

# Or add a department head layer if you want it
registry.create_profile(
    name="cto",
    display_name="CTO",
    role="department_head",
    parent="hermes",
    department="engineering",
)
```

### Hierarchy Rules

The hierarchy is flexible — structure it however fits your needs:

- Only one CEO is allowed (auto-created as `hermes`).
- Every other profile must have a parent.
- Any non-CEO role can report to any other profile.
- Circular references are prevented automatically.

### Profile Lifecycle

New profiles start in **onboarding** status. To activate a profile, submit an onboarding brief:

```python
registry.submit_onboarding_brief(
    profile_name="pm-backend",
    parent_pm="cto",
    role_definition="Manages backend API development",
    scope="Backend services and APIs. Not frontend.",
    success_criteria="All endpoints tested and deployed",
    handoff_protocol="Return results via TASK_RESPONSE",
)
# Profile is now active
```

Or use the CLI:

```bash
python -m core create-profile --name cto --display-name CTO \
    --role department_head --parent hermes --department engineering

python -m core show-org-chart
```

---

## Step 2: Set Up Messaging (IPC)

The message bus handles all communication between profiles.

```python
from core.ipc import MessageBus, MessageProtocol, MessageType, MessagePriority

bus = MessageBus("./ipc.db")
protocol = MessageProtocol(bus)

# Send a task request
message_id, correlation_id = protocol.send_request(
    from_profile="hermes",
    to_profile="cto",
    payload={"task": "Review backend architecture"},
    priority=MessagePriority.NORMAL,
)

# Recipient polls their inbox
messages = bus.poll("cto")
for msg in messages:
    print(f"[{msg.priority.name}] {msg.payload}")

# Recipient responds
protocol.send_response(
    correlation_id=correlation_id,
    from_profile="cto",
    to_profile="hermes",
    payload={"result": "Architecture looks good, proceeding"},
)

# Sender can wait for the response
response = protocol.wait_for_response(
    correlation_id=correlation_id,
    responding_profile="cto",
    timeout=30.0,
)
```

### Message Types

| Type | Use Case |
|------|----------|
| `TASK_REQUEST` | Assign work to another profile |
| `TASK_RESPONSE` | Return results from completed work |
| `STATUS_QUERY` | Ask a profile for its current status |
| `STATUS_RESPONSE` | Reply to a status query |
| `BROADCAST` | Send to multiple profiles at once |
| `ESCALATION` | Escalate to parent (auto-routes) |

### Priority Levels

Messages are delivered in priority order: `URGENT` > `NORMAL` > `LOW`.

### CLI

```bash
# Send a message
python -m core send-message --from hermes --to cto --type task_request \
    --payload '{"task": "Review architecture"}' --priority normal

# Check inbox
python -m core poll-messages --profile cto

# View bus stats
python -m core ipc-stats
```

---

## Step 3: Delegate Work with Chains

Delegation chains track tasks as they flow down the hierarchy and results as they propagate back up.

```python
from core.workers import SubagentRegistry
from core.integration import ChainOrchestrator

orchestrator = ChainOrchestrator(
    registry=registry,
    bus=bus,
    worker_registry_factory=lambda pm: SubagentRegistry(":memory:"),
)

# Create a chain
chain = orchestrator.create_chain(
    task="Implement user authentication",
    originator="hermes",
)

# Delegate down: CEO -> CTO -> PM (auto-routes through hierarchy)
hops = orchestrator.delegate_down_chain(chain, "pm-backend")

# PM spawns a worker to execute
worker_id = orchestrator.spawn_worker(
    chain=chain,
    pm_profile="pm-backend",
    task="Build login endpoint with JWT",
)

# Worker completes — result propagates up automatically
orchestrator.complete_worker(
    chain=chain,
    pm_profile="pm-backend",
    subagent_id=worker_id,
    result="Login endpoint implemented with JWT auth",
)
```

### Event-Driven Propagation

For automatic result flow without manual `complete_worker` calls:

```python
orchestrator.setup_event_driven_propagation()
# Now worker completions auto-propagate results up the chain
```

---

## Step 4: Use the Memory System

Each profile has its own scoped memory store. Memory entries age through tiers automatically.

```python
from core.memory import MemoryStore, KnowledgeBase
from core.memory.models import (
    MemoryEntry, MemoryScope, MemoryTier, MemoryEntryType,
    KnowledgeEntry,
)

# CEO's strategic memory
store = MemoryStore("./memory/hermes.db", "hermes", MemoryScope.strategic)

entry = MemoryEntry(
    entry_id="",  # auto-generated
    profile_name="hermes",
    scope=MemoryScope.strategic,
    tier=MemoryTier.hot,
    entry_type=MemoryEntryType.decision,
    content="All new services must use PostgreSQL",
    metadata={"reason": "Standardization across projects"},
)
stored = store.store(entry)

# Search memory
results = store.search("PostgreSQL")
```

### Memory Tiers

| Tier | Description | Transition |
|------|-------------|------------|
| **Hot** | Active working memory | Stays hot while accessed |
| **Warm** | Recently completed work | 30 days after completion |
| **Cool** | Older context | 90 days after completion |
| **Cold** | Archive (terminal) | No further transitions |

### Shared Knowledge Base

The knowledge base is shared across all profiles — any agent can read or write.

```python
kb = KnowledgeBase("./memory/knowledge.db", "hermes")

kb.add_knowledge(KnowledgeEntry(
    entry_id="",
    profile_name="hermes",
    category="standards",
    title="Database Standard",
    content="All services use PostgreSQL 15+",
    tags=["database", "standards"],
))

# Any profile can search
results = kb.search_all_profiles("database standard")
```

### CLI

```bash
# Inspect memory
python -m core inspect-memory hermes --memory-db ./memory/hermes.db --scope strategic

# Run garbage collection (dry run)
python -m core run-gc hermes --memory-db ./memory/hermes.db --dry-run

# Add shared knowledge
python -m core add-knowledge hermes --category standards \
    --title "Database Standard" --content "Use PostgreSQL 15+"

# Search knowledge
python -m core search-knowledge hermes "database"
```

---

## Step 5: Generate Profile Documents

Every profile gets a set of markdown documents that define its behavior.

```python
from pathlib import Path
from templates import generate_profile_docs, build_variables

profile_dir = Path("./profiles/pm-backend")
profile_dir.mkdir(parents=True, exist_ok=True)

variables = build_variables(
    profile_name="pm-backend",
    display_name="Backend PM",
    role="project_manager",
    parent_profile="cto",
    department="engineering",
    description="Manages backend API development",
)

written = generate_profile_docs(profile_dir, "project_manager", variables)
# Creates: SOUL.md, HANDOFF.md, WORKFLOWS.md, TOOLS.md, CONTEXT.md, PLAYBOOK.md
```

### Document Purposes

| File | Purpose |
|------|---------|
| `SOUL.md` | Role definition, identity, scope |
| `HANDOFF.md` | How to receive and return work |
| `WORKFLOWS.md` | Standard operating procedures |
| `TOOLS.md` | Available tools and when to use them |
| `CONTEXT.md` | Relevant context and history |
| `PLAYBOOK.md` | Global rules (shared across all profiles) |

---

## Step 6: Run the Dashboard

The UI provides a web interface for monitoring the hierarchy.

### Install Dependencies

The dashboard needs Flask and websockets (not required for core functionality):

```bash
pip install -e ".[ui]"
```

### Launch

```bash
python -m ui
# HTTP server at http://localhost:5000
# WebSocket at ws://localhost:5001/ws (real-time updates)
```

### Options

```bash
python -m ui --port 8080           # Custom HTTP port
python -m ui --ws-port 8081        # Custom WebSocket port
python -m ui --no-realtime         # Disable WebSocket (polling only)
```

### Features

The dashboard shows:

- **Org Chart** — interactive hierarchy tree with roles and status
- **Messages** — IPC message bus activity (pending, delivered, read, expired)
- **Workers** — subagent lifecycle per PM (running, sleeping, completed, archived)
- **Chains** — delegation chain tracking with hop-by-hop status
- **Memory** — per-profile memory browser with tier and type filtering
- **Dashboard** — aggregate metrics and system health

Real-time mode uses a `DatabaseWatcher` that polls the SQLite databases for changes and pushes updates to the browser via WebSocket.

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `HIERARCHY_PROJECT_ROOT` | Root directory for the project | Auto-detected |
| `HERMES_DB_BASE_DIR` | Base directory for SQLite databases | `~/.hermes/hierarchy/` |

---

## Database Layout

All state is stored in SQLite databases:

```
<db_base_dir>/
  registry.db               # Profile registry
  ipc.db                    # Message bus
  chains.db                 # Delegation chains
  memory/
    <profile>.db            # Per-profile memory
    knowledge.db            # Shared knowledge base
  workers/
    <pm>/subagents.db       # Per-PM worker registry
```

Pass `":memory:"` to any constructor for ephemeral (in-memory) databases — useful for testing and experimentation.

---

## Running Tests

```bash
# Full suite
python -m pytest

# Specific subsystem
python -m pytest tests/test_registry/
python -m pytest tests/test_ipc/
python -m pytest tests/test_workers/
python -m pytest tests/test_memory/
python -m pytest tests/test_integration/

# Single test
python -m pytest -k "test_create_profile"
```

All tests use in-memory SQLite — no filesystem setup needed.

---

## What's Next

- Read the [README](../README.md) for architecture overview and design philosophy
- Browse `integrations/hermes/` for a reference integration with the Hermes agent framework
- Check `templates/roles/` for example profile document templates
- Look at `tools/hierarchy_tools.py` for the full set of agent-callable tools
