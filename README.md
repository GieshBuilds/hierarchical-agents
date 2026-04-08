# hierarchical-agents

**A framework-agnostic multi-layer agent hierarchy for orchestrating AI agents in organizational structures.**

Pure Python 3.10+ stdlib. Zero external dependencies. SQLite-backed persistence everywhere.

---

## Features

- **Zero external dependencies** -- core runs on Python 3.10+ stdlib alone
- **SQLite-backed persistence** -- thread-safe, zero-config, portable; no database server needed
- **5-layer organizational hierarchy** -- CEO, Department Heads, Project Managers, Specialists, Workers
- **IPC message bus** -- inter-profile messaging with priority levels, TTL, and correlation tracking
- **Tiered memory system** -- hot/warm/cool/cold tiers with automatic garbage collection
- **Cross-profile knowledge sharing** -- shared knowledge base with scoped per-profile memory
- **Delegation chains** -- structured task delegation with result propagation up the hierarchy
- **Framework-agnostic core** -- protocol-based interfaces; bring your own agent framework
- **Comprehensive test suite** -- extensive coverage across all core modules

## Architecture

```
                          +----------+
                          |   CEO    |
                          +----+-----+
                               |
              +----------------+----------------+
              |                |                |
       +------+------+  +-----+------+  +------+------+
       |  Dept Head  |  |  Dept Head |  |  Dept Head  |
       +------+------+  +-----+------+  +------+------+
              |                |                |
        +-----+-----+    +----+----+     +-----+-----+
        |     PM    |    |    PM   |     |     PM    |
        +-----+-----+   +----+----+     +-----+-----+
              |               |                |
         +----+----+     +----+----+      +----+----+
         |Specialist|    |Specialist|     |Specialist|
         +----+----+     +----+----+      +----+----+
              |               |                |
          +---+---+       +---+---+        +---+---+
          |Worker |       |Worker |        |Worker |
          +-------+       +-------+        +-------+
```

Tasks flow **down** through delegation chains. Results propagate **up** through the same chain. Each layer has clear responsibilities:

| Layer | Role | Responsibility |
|-------|------|----------------|
| 1 | CEO | Strategic direction, top-level delegation |
| 2 | Department Heads | Domain ownership, resource allocation |
| 3 | Project Managers | Task decomposition, worker lifecycle |
| 4 | Specialists | Domain expertise, complex subtasks |
| 5 | Workers | Atomic task execution |

## Quick Start

### Installation

```bash
git clone https://github.com/GieshBuilds/hierarchical-agents.git
cd hierarchical-agents
```

No `pip install` needed for core functionality -- it runs on Python stdlib alone.

### Usage

```python
from core.registry import ProfileRegistry
from core.ipc import MessageBus, MessageType
from core.workers import SubagentRegistry
from core.integration import ChainOrchestrator

# Set up the hierarchy (CEO 'hermes' auto-created)
registry = ProfileRegistry(":memory:")
registry.create_profile(name="cto", role="department_head", parent="hermes")
registry.create_profile(name="backend-pm", role="project_manager", parent="cto")

# Set up IPC
bus = MessageBus(":memory:")

# Create the orchestrator
orchestrator = ChainOrchestrator(
    registry=registry,
    bus=bus,
    worker_registry_factory=lambda pm: SubagentRegistry(":memory:"),
)

# Create and execute a delegation chain
chain = orchestrator.create_chain("Build the API", originator="hermes")
orchestrator.delegate(chain, "hermes", "cto")
orchestrator.delegate(chain, "cto", "backend-pm")

# Spawn a worker
worker_id = orchestrator.spawn_worker(chain, "backend-pm", "Implement /users endpoint")

# Complete and propagate results
orchestrator.complete_worker(chain, "backend-pm", worker_id, "Endpoint implemented")
orchestrator.propagate_result(chain, "API built successfully")
```

### CLI

```bash
# Profile management
python -m core create-profile --name cto --role department_head --parent hermes
python -m core list-profiles --json
python -m core show-org-chart

# Messaging
python -m core send-message --from hermes --to cto --type task_request \
    --payload '{"task": "review architecture"}'
python -m core poll-messages --profile cto
```

## Core Modules

| Module | Key Class | Purpose |
|--------|-----------|---------|
| `core/registry/` | `ProfileRegistry` | Profile CRUD, hierarchy validation, org chart |
| `core/ipc/` | `MessageBus`, `MessageProtocol` | Inter-profile messaging with priority, TTL, correlation |
| `core/workers/` | `SubagentRegistry` | Per-PM worker lifecycle management |
| `core/memory/` | `MemoryStore`, `KnowledgeBase` | Tiered memory with scoping and garbage collection |
| `core/integration/` | `ChainOrchestrator` | End-to-end delegation chains with result propagation |

## Project Structure

```
hierarchical-agents/
├── core/                    # Framework-agnostic modules (stdlib only)
│   ├── registry/            # Profile registry + org chart
│   ├── ipc/                 # Inter-profile messaging
│   ├── workers/             # Subagent lifecycle management
│   ├── memory/              # Tiered memory + knowledge base
│   └── integration/         # Delegation chains + orchestration
├── integrations/            # Framework-specific adapters
│   ├── hermes/              # Reference Hermes integration
│   ├── claude_code/         # Claude Code context generation
│   └── openclaw/            # OpenClaw integration
├── tools/                   # Agent tool definitions
├── templates/               # Profile templates + generator
├── ui/                      # Dashboard UI
├── dashboard/               # Dashboard API
├── tests/                   # Test suite
├── scripts/                 # Utility scripts
└── pyproject.toml
```

## Design Philosophy

**Stdlib only.** The core has zero external dependencies. Python 3.10+ stdlib provides everything needed: `sqlite3` for persistence, `typing.Protocol` for interfaces, `dataclasses` for data structures, `json` for serialization. This keeps the dependency tree clean and the project portable.

**SQLite everywhere.** Every stateful component -- registry, message bus, memory store, knowledge base, worker registry -- persists to SQLite. Thread-safe, zero-config, single-file databases that work anywhere Python runs. Pass `":memory:"` for ephemeral use or a file path for durable storage.

**Protocol interfaces.** Core modules define `typing.Protocol` classes for structural subtyping. Any framework can implement these protocols without inheriting from base classes. The `integrations/` directory contains reference implementations.

**Auto-bootstrapping.** The CEO profile is created automatically when a `ProfileRegistry` is initialized. No manual setup required to start building a hierarchy.

**Shared knowledge, scoped memory.** The `KnowledgeBase` is shared across all profiles for organizational knowledge. `MemoryStore` instances are scoped per-profile with tiered storage (hot/warm/cool/cold) and automatic garbage collection based on access patterns and age.

**Framework-agnostic core.** All framework-specific code lives in `integrations/`. The core modules know nothing about Hermes, Claude Code, or any other agent framework. Swap frameworks without touching core logic.

## Contributing

Contributions are welcome.

- **Bug reports and feature requests** -- open an issue
- **Pull requests** -- fork, branch, and submit a PR
- **Tests** -- add tests for new functionality; run the suite with `python -m pytest tests/`

Please keep PRs focused on a single change and include tests where applicable.

## Documentation

- **[Getting Started](docs/GETTING-STARTED.md)** — Step-by-step setup guide covering installation, hierarchy creation, messaging, delegation, memory, and the dashboard

## License

MIT License. See [LICENSE](LICENSE) for details.
