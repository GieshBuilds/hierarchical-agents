# hierarchical-agents

**Organize AI agents like a company. Give them roles, chain of command, messaging, memory, and task delegation — all backed by SQLite.**

Built for the [Hermes](https://github.com/GieshBuilds) agent framework. Pure Python 3.10+ stdlib. Zero external dependencies.

---

## What This Does

Most multi-agent systems are flat — agents talk to each other without structure. This project gives your agents an **organizational hierarchy**, the same way a company has a CEO, department heads, project managers, and workers.

Each agent gets:
- A **profile** with a defined role and position in the org chart
- An **inbox** for receiving tasks and sending results via IPC
- **Scoped memory** that persists across sessions, with automatic aging and cleanup
- Access to a **shared knowledge base** that any agent can read or write
- The ability to **delegate work** down the chain and **propagate results** back up

The system enforces hierarchy rules (who can delegate to whom), tracks task chains end-to-end, and manages the full lifecycle of worker agents that get spawned for specific tasks.

## How It Works

### The Org Chart

```
                          +---------+
                          | hermes  |  CEO — strategic decisions, top-level delegation
                          +----+----+
                               |
              +----------------+----------------+
              |                |                |
         +----+----+     +----+----+      +----+----+
         |   CTO   |     |   CMO   |      |   CFO   |  Dept Heads — domain ownership
         +----+----+     +----+----+      +----+----+
              |                |                |
        +-----+-----+    +----+----+     +-----+-----+
        | backend-pm |    | mktg-pm |    | finance-pm |  PMs — task decomposition
        +-----+------+   +----+----+    +-----+------+
              |                                |
         +----+----+                      +----+----+
         |  dev-*  |                      |  sec-*  |   Specialists — persistent experts
         +----+----+                      +----+----+
              |
          +---+---+
          | sa-*  |  Workers — disposable subagents spawned for a single task
          +-------+
```

When the CEO receives "build the authentication system", it delegates to the CTO, who delegates to the backend PM, who spawns a worker to write the code. The result flows back up the same path: worker -> PM -> CTO -> CEO.

### Task Flow

1. **Task arrives** at a profile's inbox as a `TASK_REQUEST` message
2. **Profile delegates** down to a report or spawns a worker
3. **Worker executes** the task and completes with a result
4. **Result propagates up** through each hop in the delegation chain via `TASK_RESPONSE` messages
5. **Originator receives** the final result

Each step is tracked, persisted, and auditable. Messages have priority levels (urgent/normal/low), TTL expiry, and correlation IDs for threading conversations.

### Memory Model

Agents don't start from scratch every session. Each profile has:

- **Personal memory** — decisions, learnings, context scoped to their role. Entries age through tiers (hot -> warm -> cool -> cold) with automatic garbage collection.
- **Shared knowledge base** — organizational knowledge any agent can publish to or search. Standards, decisions, patterns that the whole org needs.
- **Ancestor access** — agents can read memory from profiles above them in the chain of command (read-up only, never sideways).

### Profile Lifecycle

New profiles go through onboarding before activation:

1. **Created** — registered in the hierarchy with a role and parent
2. **Onboarding** — parent submits a brief defining the profile's scope, success criteria, and handoff protocol
3. **Active** — profile can send/receive messages, spawn workers, and participate in delegation chains
4. **Suspended/Archived** — taken offline without losing data

Each profile also gets generated documentation (SOUL.md, HANDOFF.md, WORKFLOWS.md, TOOLS.md, CONTEXT.md) that defines its identity and operating procedures.

---

## Quick Start

### Installation

```bash
git clone https://github.com/GieshBuilds/hierarchical-agents.git
cd hierarchical-agents
```

No `pip install` needed for core functionality — it runs on Python stdlib alone.

### Build a Hierarchy

```python
from core.registry import ProfileRegistry
from core.ipc import MessageBus
from core.workers import SubagentRegistry
from core.integration import ChainOrchestrator

# Initialize — CEO 'hermes' is created automatically
registry = ProfileRegistry(":memory:")

# Add agents to the org chart
registry.create_profile(name="cto", role="department_head", parent="hermes")
registry.create_profile(name="backend-pm", role="project_manager", parent="cto")

# Set up messaging and orchestration
bus = MessageBus(":memory:")
orchestrator = ChainOrchestrator(
    registry=registry,
    bus=bus,
    worker_registry_factory=lambda pm: SubagentRegistry(":memory:"),
)
```

### Delegate a Task

```python
# Create a delegation chain
chain = orchestrator.create_chain("Build the API", originator="hermes")

# Route it down: CEO -> CTO -> PM
orchestrator.delegate(chain, "hermes", "cto")
orchestrator.delegate(chain, "cto", "backend-pm")

# PM spawns a worker to do the actual work
worker_id = orchestrator.spawn_worker(chain, "backend-pm", "Implement /users endpoint")

# Worker completes — result auto-propagates back up to hermes
orchestrator.complete_worker(chain, "backend-pm", worker_id, "Endpoint implemented")
```

### Send Messages Directly

```python
from core.ipc import MessageProtocol, MessagePriority

protocol = MessageProtocol(bus)

# Request/response pattern
msg_id, corr_id = protocol.send_request(
    from_profile="hermes",
    to_profile="cto",
    payload={"task": "Review backend architecture"},
    priority=MessagePriority.URGENT,
)

# Recipient polls their inbox
messages = bus.poll("cto")

# Respond
protocol.send_response(
    correlation_id=corr_id,
    from_profile="cto",
    to_profile="hermes",
    payload={"result": "Architecture approved"},
)
```

### CLI

```bash
# Profile management
python -m core create-profile --name cto --display-name CTO \
    --role department_head --parent hermes
python -m core list-profiles --json
python -m core show-org-chart

# Messaging
python -m core send-message --from hermes --to cto --type task_request \
    --payload '{"task": "review architecture"}' --priority urgent
python -m core poll-messages --profile cto

# Memory
python -m core inspect-memory hermes --memory-db ./memory.db --scope strategic
python -m core search-knowledge hermes "database standards"

# Stats
python -m core ipc-stats
```

---

## Core Modules

| Module | Key Class | What It Does |
|--------|-----------|-------------|
| `core/registry/` | `ProfileRegistry` | Agent profiles, hierarchy validation, org chart, onboarding |
| `core/ipc/` | `MessageBus`, `MessageProtocol` | Inter-agent messaging with priority, TTL, correlation, broadcast, escalation |
| `core/workers/` | `SubagentRegistry` | Worker spawn/sleep/resume/complete lifecycle with completion callbacks |
| `core/memory/` | `MemoryStore`, `KnowledgeBase` | Per-profile scoped memory with tiered aging + shared cross-profile knowledge |
| `core/integration/` | `ChainOrchestrator` | End-to-end delegation chains with tracked hops and result propagation |

## Project Structure

```
hierarchical-agents/
├── core/                    # Core modules (stdlib only, zero dependencies)
│   ├── registry/            # Profile registry, hierarchy rules, org chart
│   ├── ipc/                 # Message bus, protocol patterns, cleanup
│   ├── workers/             # Subagent lifecycle, state machine, resume
│   ├── memory/              # Memory store, knowledge base, tiered storage, GC
│   └── integration/         # Delegation chains, orchestrator, result propagation
├── integrations/
│   ├── hermes/              # Hermes agent framework integration (gateway, bridges, delivery)
│   ├── claude_code/         # Claude Code context generation
│   └── openclaw/            # OpenClaw integration
├── tools/                   # Agent-callable tool definitions (12 tools)
├── templates/               # Profile document templates + generator
├── ui/                      # Web dashboard (org chart, messages, workers, memory)
├── tests/                   # Test suite
└── pyproject.toml
```

## Design Philosophy

**Stdlib only.** Zero external dependencies. `sqlite3` for persistence, `typing.Protocol` for interfaces, `dataclasses` for models. Portable and lightweight.

**SQLite everywhere.** Every stateful component persists to SQLite. Thread-safe, zero-config, single-file databases. Pass `":memory:"` for testing or a file path for production.

**Hierarchy is enforced, not suggested.** The registry validates every profile creation and delegation against hierarchy rules. Department heads report to the CEO, PMs report to department heads, and so on. Circular references are prevented.

**Tasks are tracked end-to-end.** Delegation chains record every hop from originator to worker. Results propagate back up the same path. Nothing gets lost in the middle.

**Memory has a lifecycle.** Entries start hot and age through warm, cool, and cold tiers based on access patterns. Garbage collection enforces budgets. Agents don't accumulate unbounded context.

---

## Documentation

- **[Getting Started](docs/GETTING-STARTED.md)** — Full setup guide: installation, hierarchy creation, messaging, delegation, memory, templates, and the dashboard

## Contributing

Contributions are welcome.

- **Bug reports and feature requests** — open an issue
- **Pull requests** — fork, branch, and submit a PR
- **Tests** — run the suite with `python -m pytest tests/`

## License

MIT License. See [LICENSE](LICENSE) for details.
