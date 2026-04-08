# hierarchical-agents

**Turn isolated [Hermes](https://github.com/GieshBuilds) agent profiles into a coordinated organization with hierarchy, messaging, delegation, and shared memory.**

Hermes gives each agent its own profile — an isolated session with its own identity, tools, and state. But profiles can't talk to each other. This project adds the coordination layer: an org chart, an IPC message bus, delegation chains, scoped memory, and worker lifecycle management. Profiles stop being silos and start working as a team.

Pure Python 3.10+ stdlib. Zero external dependencies in core. All persistence via SQLite.

---

## The Problem

Hermes profiles are powerful in isolation. Each one has its own SOUL.md, session history, skills, model selection, and gateway. But out of the box:

- Profiles **can't message each other**
- There's **no hierarchy** — no way to say "the CTO manages these PMs"
- There's **no task delegation** — no way to assign work from one profile to another and get results back
- There's **no shared memory** — each profile's knowledge is locked in its own silo
- There's **no worker tracking** — when a PM spawns a subagent, nothing tracks its lifecycle

This project solves all of that.

## What It Adds

| Capability | What Hermes Has | What This Adds |
|-----------|----------------|---------------|
| **Identity** | Profile directories with SOUL.md, skills, state | Org chart registry with roles (CEO, Dept Head, PM, Specialist) and flexible hierarchy |
| **Communication** | None between profiles | IPC message bus with priority, TTL, correlation, broadcast, escalation |
| **Delegation** | None | Delegation chains that track tasks through the hierarchy and propagate results back up |
| **Memory** | Per-profile markdown files (MEMORY.md) | Tiered memory (hot/warm/cool/cold) with scoping, GC, shared knowledge base, ancestor read access, and **bidirectional sync** with native Hermes memory |
| **Workers** | Can spawn subagents | Per-PM worker registry with lifecycle tracking (running/sleeping/completed/archived) and completion callbacks |
| **Coordination** | None | ChainOrchestrator for end-to-end task flow with event-driven result propagation |

## How It Works

### The Org Chart (example)

Existing Hermes profiles are synced into a hierarchy via `ProfileBridge`. Each profile gets a role and a parent. The hierarchy is flexible — you structure it however makes sense for your org:

**Flat (PMs report directly to CEO):**
```
         +---------+
         | hermes  |  CEO
         +----+----+
              |
    +---------+---------+
    |         |         |
 +--+--+  +--+--+  +---+---+
 | pm-a |  | pm-b |  | pm-c |  PMs — report directly to CEO
 +--+---+  +------+  +------+
    |
 +--+--+
 |dev-a|  Specialists
 +-----+
```

**Layered (with department heads):**
```
         +---------+
         | hermes  |  CEO
         +----+----+
              |
       +------+------+
       |             |
  +----+----+   +----+----+
  |   CTO   |   |   CMO   |  Dept Heads
  +----+----+   +----+----+
       |             |
  +----+----+   +----+----+
  |backend-pm|  | mktg-pm |  PMs
  +----+-----+  +---------+
       |
  +----+----+
  |  dev-*  |  Specialists
  +----+----+
       |
   +---+---+
   | sa-*  |  Workers (disposable)
   +-------+
```

Any non-CEO role can report to any other profile. The only hard rules: one CEO (auto-created as `hermes`), every other profile must have a parent, and no circular references.

Each of these is a real Hermes profile with its own `~/.hermes/profiles/<name>/` directory. The hierarchy layer organizes them, gives them tools to communicate, and tracks work flowing between them.

### Task Flow

1. **Task arrives** at a profile's inbox as a `TASK_REQUEST` message
2. **Profile delegates** down to a report or spawns a worker
3. **Worker executes** the task and completes with a result
4. **Result propagates up** through each hop in the delegation chain via `TASK_RESPONSE` messages
5. **Originator receives** the final result

Each step is tracked, persisted, and auditable. Messages have priority levels (urgent/normal/low), TTL expiry, and correlation IDs for threading conversations.

### Memory Model

Each profile gets scoped memory on top of Hermes' native session state:

- **Personal memory** — decisions, learnings, context scoped to their role. Entries age through tiers (hot -> warm -> cool -> cold) with automatic garbage collection.
- **Shared knowledge base** — organizational knowledge any agent can publish to or search. Standards, decisions, patterns that the whole org needs.
- **Ancestor access** — agents can read memory from profiles above them in the chain of command (read-up only, never sideways).
- **Bidirectional sync** — a memory bridge syncs between Hermes' native `MEMORY.md` files and the hierarchy's structured SQLite memory. Native memories are imported into the hierarchy store; hierarchy context is exported to `HIERARCHY_CONTEXT.md` so Hermes reads it at session startup. Sync runs on profile activation.

### What Gets Installed

When you set up the hierarchy, it adds to your existing Hermes installation:

```
~/.hermes/hierarchy/              # New — all coordination state
  ├── registry.db                 # Org chart (profiles, roles, parents)
  ├── ipc.db                      # Message bus
  ├── chains.db                   # Delegation chain tracking
  ├── memory/<profile>.db         # Per-profile scoped memory
  ├── memory/knowledge.db         # Shared knowledge base
  └── workers/<pm>/subagents.db   # Per-PM worker registry

~/.hermes/profiles/<name>/        # Existing Hermes profiles — updated with:
  ├── SOUL.md                     # Updated with hierarchy role + tools
  ├── HANDOFF.md                  # How to receive/return work via IPC
  ├── WORKFLOWS.md                # Standard operating procedures
  └── TOOLS.md                    # Lists 12 new hierarchy tools
```

### Tools Given to Agents

Every profile in the hierarchy gets these tools:

| Tool | Purpose |
|------|---------|
| `send_to_profile` | Send a task or message to any profile in the org chart |
| `check_inbox` | Read pending messages and task results |
| `org_chart` | View the full organizational hierarchy |
| `profile_status` | Check if a profile is active, see their workload |
| `spawn_tracked_worker` | Spawn a worker subagent with lifecycle tracking |
| `get_project_status` | Check status of delegated work and workers |
| `save_memory` | Persist decisions and context to personal memory |
| `search_knowledge` | Search the shared knowledge base |
| `share_knowledge` | Publish knowledge for other profiles to find |
| `read_ancestor_memory` | Read memory from profiles above you in the chain |
| `get_chain_context` | Pull context from your full chain of command |
| `create_profile` | Register a new profile in the hierarchy |

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
| `core/registry/` | `ProfileRegistry` | Organizes Hermes profiles into a hierarchy with roles and rules |
| `core/ipc/` | `MessageBus`, `MessageProtocol` | Inter-profile messaging with priority, TTL, correlation, broadcast, escalation |
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
│   ├── hermes/              # Hermes integration (gateway hooks, bridges, delivery)
│   ├── claude_code/         # Claude Code context generation
│   └── openclaw/            # OpenClaw integration
├── tools/                   # 12 agent-callable hierarchy tools
├── templates/               # Profile document templates + generator
├── ui/                      # Web dashboard (org chart, messages, workers, memory)
├── tests/                   # Test suite
└── pyproject.toml
```

## Design Philosophy

**Extend, don't replace.** Hermes profiles are the foundation. This project adds coordination on top — it doesn't replace Hermes' session management, skill system, or gateway infrastructure.

**Stdlib only.** Zero external dependencies in core. `sqlite3` for persistence, `typing.Protocol` for interfaces, `dataclasses` for models.

**SQLite everywhere.** Every stateful component persists to SQLite. Thread-safe, zero-config, single-file databases. Pass `":memory:"` for testing or a file path for production.

**Hierarchy is enforced, not suggested.** The registry validates every profile creation and delegation against hierarchy rules. Circular references are prevented. Role constraints are checked.

**Tasks are tracked end-to-end.** Delegation chains record every hop from originator to worker. Results propagate back up the same path. Nothing gets lost in the middle.

**Memory has a lifecycle.** Entries start hot and age through warm, cool, and cold tiers based on access patterns. Garbage collection enforces budgets. Agents don't accumulate unbounded context.

---

## Documentation

- **[Getting Started](docs/GETTING-STARTED.md)** — Setup guide: installation, hierarchy creation, messaging, delegation, memory, and the dashboard
- **[Architecture](docs/ARCHITECTURE.md)** — Deep dive: how every module works, data models, schemas, state machines, integration layer, and design patterns

## Contributing

Contributions are welcome.

- **Bug reports and feature requests** — open an issue
- **Pull requests** — fork, branch, and submit a PR
- **Tests** — run the suite with `python -m pytest tests/`

## License

MIT License. See [LICENSE](LICENSE) for details.
