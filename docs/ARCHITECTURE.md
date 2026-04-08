# Architecture

A deep dive into how hierarchical-agents works under the hood.

---

## System Overview

The system has three layers:

1. **Core** (`core/`) — Pure Python stdlib modules that handle profiles, messaging, workers, memory, and delegation. No knowledge of Hermes or any other framework.
2. **Integration** (`integrations/hermes/`) — Bridges between the core and running Hermes profiles. Handles discovery, activation, message routing, worker spawning, and result delivery.
3. **Tools** (`tools/`) — Agent-callable functions that expose the core to LLM agents during sessions.

All state is persisted to SQLite databases under `~/.hermes/hierarchy/`:

```
~/.hermes/hierarchy/
├── registry.db                  # Profile registry (org chart)
├── ipc.db                       # Message bus
├── chains.db                    # Delegation chain tracking
├── memory/
│   ├── <profile>.db             # Per-profile scoped memory
│   └── knowledge.db             # Shared knowledge base
└── workers/
    └── <pm>/subagents.db        # Per-PM worker registry
```

---

## 1. Profile Registry

**Module:** `core/registry/`
**Database:** `registry.db`

The registry organizes Hermes profiles into a hierarchy with enforced role constraints.

### Roles

Four roles with flexible parenting:

| Role | Can Report To | Limit |
|------|--------------|-------|
| `ceo` | Nobody (root) | Exactly one, auto-created as `hermes` |
| `department_head` | Any profile | Unlimited |
| `project_manager` | Any profile | Unlimited |
| `specialist` | Any profile | Unlimited |

The only hard rules: one CEO with no parent, every other profile must have a parent, and no circular references. Beyond that, you can structure the hierarchy however you want — PMs directly under the CEO, specialists under other specialists, whatever fits your org. Circular references are detected by walking the parent chain to the root.

### Profile Data

```python
@dataclass
class Profile:
    profile_name: str              # Primary key, lowercase alphanumeric + hyphens
    display_name: str              # Human-readable name
    role: str                      # ceo | department_head | project_manager | specialist
    parent_profile: str | None     # Foreign key to parent (None for CEO)
    department: str | None         # Optional grouping
    status: str                    # onboarding | active | suspended | archived
    created_at: datetime
    updated_at: datetime
    config_path: str | None        # Optional config file reference
    description: str | None
```

Profile names must match `^[a-z][a-z0-9-]*$` and be at most 64 characters.

### Lifecycle

```
Created ──► Onboarding ──► Active ──► Suspended
                                  └──► Archived
```

- **CEO** starts as `active` immediately (auto-bootstrapped on `ProfileRegistry.__init__()`)
- **All other roles** start as `onboarding` and require an onboarding brief before activation

### Onboarding

The parent profile submits a brief with four required fields:

| Field | Purpose |
|-------|---------|
| `role_definition` | What this agent does |
| `scope` | What's in and out of scope |
| `success_criteria` | How to measure success |
| `handoff_protocol` | How finished work returns upstream |

Once submitted, the profile transitions to `active` automatically. Optional fields include `discovery_answers`, `dependencies`, `first_task`, and `extra` metadata.

### Integrity Scanning

`scan_integrity()` performs a read-only audit of the entire registry, checking:

- Exactly one non-archived CEO exists
- All parent references are valid (no orphans)
- Role-parent constraints are satisfied
- No circular references
- No archived profiles with active dependents
- Profile names match the naming pattern

Returns a list of `IntegrityIssue` objects with severity (`error`/`warning`), the affected profile, and which rule was violated.

### Schema

```sql
CREATE TABLE profiles (
    profile_name    TEXT PRIMARY KEY,
    display_name    TEXT,
    role            TEXT CHECK(role IN ('ceo','department_head','project_manager','specialist')),
    parent_profile  TEXT REFERENCES profiles(profile_name) ON DELETE RESTRICT,
    department      TEXT,
    status          TEXT CHECK(status IN ('onboarding','active','suspended','archived')),
    created_at      TEXT,
    updated_at      TEXT,
    config_path     TEXT,
    description     TEXT
);

CREATE TABLE onboarding_briefs (
    profile_name      TEXT PRIMARY KEY REFERENCES profiles ON DELETE CASCADE,
    parent_pm         TEXT,
    role_definition   TEXT,
    scope             TEXT,
    success_criteria  TEXT,
    handoff_protocol  TEXT,
    discovery_answers TEXT,
    dependencies      TEXT,
    first_task        TEXT,
    submitted_at      TEXT,
    extra_json        TEXT
);
```

Indexes on `parent_profile`, `department`, and `status` for fast lookups.

---

## 2. IPC Message Bus

**Module:** `core/ipc/`
**Database:** `ipc.db`

The message bus is how profiles communicate. It's a shared SQLite database that all profiles read from and write to.

### Message Structure

```python
@dataclass
class Message:
    message_id: str              # msg-{uuid_hex[:12]}
    from_profile: str
    to_profile: str
    message_type: MessageType    # One of 6 types
    payload: dict                # JSON-serializable content
    correlation_id: str | None   # Links request/response pairs (corr-{uuid_hex[:12]})
    priority: MessagePriority    # Determines delivery order
    status: MessageStatus        # Tracks delivery state
    created_at: datetime
    expires_at: datetime | None  # TTL — None means never expires
```

### Message Types

| Type | When Used |
|------|-----------|
| `TASK_REQUEST` | Delegating work to another profile |
| `TASK_RESPONSE` | Returning results from completed work |
| `STATUS_QUERY` | Asking a profile for its current status |
| `STATUS_RESPONSE` | Replying to a status query |
| `BROADCAST` | Sending to multiple profiles at once |
| `ESCALATION` | Escalating to parent profile (auto-routed) |

### Priority & Ordering

Three levels: `URGENT` (sort order 2) > `NORMAL` (1) > `LOW` (0).

When a profile polls its inbox, messages are returned highest-priority first, then oldest first within each priority level. This means urgent messages always surface before normal ones, and within the same priority, FIFO order is preserved.

### Message Lifecycle

```
PENDING ──► DELIVERED ──► READ
   │             │
   └─► EXPIRED ◄─┘
```

- **PENDING** — sent but not yet picked up
- **DELIVERED** — acknowledged by the recipient's gateway
- **READ** — processed by the recipient (terminal)
- **EXPIRED** — TTL elapsed before delivery (terminal)

Default TTL is 24 hours. Can be overridden per-message, or set to `None` for no expiry.

### Protocol Patterns

`MessageProtocol` provides higher-level communication patterns on top of the raw bus:

**Request/Response:**
```python
msg_id, corr_id = protocol.send_request(from_profile, to_profile, payload)
# ... recipient processes ...
protocol.send_response(corr_id, from_profile, to_profile, result_payload)
# Sender can wait:
response = protocol.wait_for_response(corr_id, responding_profile, timeout=30)
```

**Broadcast:**
```python
msg_ids = protocol.send_broadcast(from_profile, [list_of_recipients], payload)
# All recipients get the same message, sharing a correlation ID
```

**Escalation:**
```python
msg_id = protocol.send_escalation(from_profile, payload, priority=URGENT)
# Automatically routes to from_profile's parent in the hierarchy
```

**Conversation Threading:**
```python
messages = protocol.get_conversation(correlation_id)
# Returns all messages in a request/response chain
```

### TTL & Cleanup

`MessageCleanup` handles expiry and archival:

1. **Expire** — scans for messages where `expires_at < now` and transitions them to `EXPIRED`
2. **Archive** — moves expired messages from `messages` to `message_archive` table with a timestamp, then deletes from the active table

### Schema

```sql
CREATE TABLE messages (
    message_id      TEXT PRIMARY KEY,
    from_profile    TEXT,
    to_profile      TEXT,
    message_type    TEXT CHECK(message_type IN (...)),
    payload         TEXT DEFAULT '{}',
    correlation_id  TEXT,
    priority        TEXT CHECK(priority IN ('low','normal','urgent')),
    status          TEXT CHECK(status IN ('pending','delivered','read','expired')),
    created_at      TEXT,
    expires_at      TEXT
);

CREATE TABLE message_archive (
    -- Same columns as messages, plus:
    archived_at     TEXT
);
```

Indexes on `(to_profile, status)` for fast polling, `correlation_id` for conversation threading, `(priority, created_at)` for ordered delivery, and `expires_at` for TTL scans.

---

## 3. Worker Lifecycle

**Module:** `core/workers/`
**Database:** `workers/<pm>/subagents.db` (one per project manager)

Workers are disposable subagents spawned by project managers to execute specific tasks. Each PM has its own worker registry.

### Worker Data

```python
@dataclass
class Subagent:
    subagent_id: str              # sa-{uuid}
    project_manager: str          # PM that owns this worker
    task_goal: str                # What the worker is supposed to do
    status: str                   # running | sleeping | completed | archived
    created_at: datetime
    updated_at: datetime
    conversation_path: str | None # Path to session history
    result_summary: str | None    # Final result
    artifacts: list[str]          # Paths to created files
    token_cost: int               # Tokens consumed
    parent_request_id: str | None # Links to delegation chain
```

### State Machine

```
RUNNING ◄──► SLEEPING
   │
   ▼
COMPLETED ──► ARCHIVED
```

- **RUNNING** — actively executing
- **SLEEPING** — paused, can be resumed with full context (session history, config, task goal)
- **COMPLETED** — finished, result recorded, completion callbacks fired
- **ARCHIVED** — cleaned up (terminal)

Invalid transitions (e.g., `COMPLETED → RUNNING`) raise `InvalidSubagentStatus`.

### Completion Callbacks

When a worker completes, the registry fires all registered callbacks:

```python
registry.register_completion_callback(fn)
# fn(subagent_id, result_summary) called on every complete()
```

This is how delegation chains get notified — the `ChainOrchestrator` registers a callback that triggers result propagation up the chain.

### Worker Serialization

Workers can be serialized to disk for resume:

```
<base_path>/<pm>/<subagent_id>/
├── session.json      # Full conversation history
├── config.json       # Model, provider, toolsets, system prompt
├── metadata.json     # Task, status, timestamps
├── summary.md        # Human-readable summary
└── artifacts/        # Created files
```

`resume()` loads all of this into a `ResumeContext` that can reconstruct the agent's state.

### WorkerManager Protocol

The integration layer implements this protocol:

```python
class WorkerManager(Protocol):
    def spawn_worker(self, goal, context=None, config=None) -> str: ...
    def on_worker_complete(self, pm_profile, subagent_id, result, chain=None) -> None: ...
    def on_worker_error(self, subagent_id, error) -> None: ...
    def resume_worker(self, subagent_id, new_message=None) -> ResumeContext: ...
```

---

## 4. Memory System

**Module:** `core/memory/`
**Database:** `memory/<profile>.db` (per-profile) and `memory/knowledge.db` (shared)

### Two Types of Memory

**Personal memory** (`MemoryStore`) — scoped to a single profile, with tiered aging.
**Shared knowledge** (`KnowledgeBase`) — accessible to all profiles, for organizational knowledge.

### Memory Entries

```python
@dataclass
class MemoryEntry:
    entry_id: str                  # mem-{uuid}
    profile_name: str
    scope: MemoryScope             # strategic | domain | project | task
    tier: MemoryTier               # hot | warm | cool | cold
    entry_type: MemoryEntryType    # preference | decision | learning | context | summary | artifact
    content: str
    metadata: dict
    created_at: datetime
    updated_at: datetime
    accessed_at: datetime          # Updated on every read (used for aging)
    expires_at: datetime | None
    byte_size: int                 # Auto-calculated
```

### Scope Mapping

Each role has a natural memory scope:

| Role | Scope | Example Content |
|------|-------|----------------|
| CEO | `strategic` | Org-wide decisions, priorities |
| Department Head | `domain` | Domain standards, team policies |
| Project Manager | `project` | Project decisions, sprint context |
| Specialist/Worker | `task` | Task-specific learnings, findings |

### Tier Lifecycle

Memory entries age through four tiers based on time since last access:

```
HOT ──(immediately)──► WARM ──(30 days)──► COOL ──(90 days)──► COLD
```

| Tier | Age | Purpose |
|------|-----|---------|
| **Hot** | Active | Current working memory, immediate context |
| **Warm** | Recent | Recently completed work, still relevant |
| **Cool** | 30+ days | Older context, may need refresh |
| **Cold** | 90+ days | Archive, terminal tier |

`TieredStorage` scans entries and produces tier transition recommendations. Transitions are forward-only — an entry never moves from cool back to warm.

### Garbage Collection

`GarbageCollector` enforces memory budgets:

```python
@dataclass
class MemoryBudget:
    max_entries: int = 1000
    max_bytes: int = 10_485_760    # 10 MB
    tier_quotas: dict = {
        "hot": 200, "warm": 300,
        "cool": 300, "cold": 200,
    }
```

When a profile exceeds its budget, the GC purges entries starting from the coldest tier, oldest first.

### Shared Knowledge Base

```python
@dataclass
class KnowledgeEntry:
    entry_id: str                  # kb-{uuid}
    profile_name: str              # Who contributed this
    category: str                  # e.g., "standards", "architecture"
    title: str
    content: str
    source_profile: str            # Original source
    source_context: str            # Why it was shared
    tags: list[str]
    created_at: datetime
    updated_at: datetime
```

Any profile can write to the knowledge base. Any profile can search it. This is how organizational standards, decisions, and patterns propagate across the hierarchy.

### Ancestor Memory Access

Agents can read memory from profiles **above them** in the chain of command:

```
CEO memory ← readable by CTO, PMs, Specialists
CTO memory ← readable by PMs under that CTO, their Specialists
PM memory  ← readable by Specialists under that PM
```

Access is up-only. A PM cannot read a sibling PM's memory. The chain of command is determined by `ProfileRegistry.get_chain_of_command()`.

### Context Assembly

`ContextManager` builds activation context for agents by pulling from multiple sources:

1. Profile identity (role, parent, department)
2. Hot-tier personal memory
3. Pending IPC messages
4. Active workers
5. Ancestor context (parent's hot memory)
6. Relevant shared knowledge

Sections are prioritized and truncated if the total exceeds `max_context_tokens` (default 4000). Identity and current task always survive truncation; shared knowledge is the first to be cut.

---

## 5. Delegation Chains

**Module:** `core/integration/`
**Database:** `chains.db`

Delegation chains track the full path of a task from originator to worker and back.

### Chain Structure

```python
@dataclass
class DelegationChain:
    chain_id: str                      # chain-{uuid}
    task_description: str
    originator: str                    # Profile that started the chain
    status: ChainStatus                # pending | active | completed | failed | expired
    hops: list[DelegationHop]          # Ordered list of delegation steps
    worker_results: dict[str, str]     # subagent_id -> result
    created_at: datetime
    completed_at: datetime | None

@dataclass
class DelegationHop:
    from_profile: str
    to_profile: str
    status: HopStatus                  # pending | delegated | working | completed | failed
    message_id: str | None             # IPC message that carried this delegation
    delegated_at: datetime | None
    completed_at: datetime | None
```

### Example Chain

Task: "Implement user authentication"

```
Chain: chain-a1b2c3d4
  Hop 0: hermes → cto          [COMPLETED]  msg-xxx
  Hop 1: cto → pm-backend      [COMPLETED]  msg-yyy
  Worker: sa-zzz (pm-backend)   [COMPLETED]  "Login endpoint implemented"
```

### Flow

1. **Create** — `orchestrator.create_chain(task, originator)` returns a PENDING chain
2. **Delegate** — `delegate_down_chain(chain, target)` walks the hierarchy from originator to target, creating a hop and sending a `TASK_REQUEST` for each step
3. **Spawn** — `spawn_worker(chain, pm, task)` creates a tracked worker linked to the chain via `parent_request_id`
4. **Complete** — `complete_worker(chain, pm, worker_id, result)` records the result
5. **Propagate** — `ResultCollector.propagate_up()` walks the hops in reverse, sending a `TASK_RESPONSE` from each `to_profile` back to each `from_profile`

### Event-Driven Propagation

Instead of manually calling `propagate_up()`, you can wire automatic propagation:

```python
orchestrator.setup_event_driven_propagation()
```

This registers a completion callback on the worker registry. When any worker completes, it checks if the worker is part of a chain and whether all workers in that chain are done. If so, it auto-propagates the result up through every hop.

### Persistence

`ChainStore` persists chains to SQLite as JSON blobs:

```sql
CREATE TABLE delegation_chains (
    chain_id          TEXT PRIMARY KEY,
    task_description  TEXT,
    originator        TEXT,
    status            TEXT,
    chain_json        TEXT,       -- Full chain serialized as JSON
    created_at        TEXT,
    completed_at      TEXT
);
```

---

## 6. Hermes Integration

**Module:** `integrations/hermes/`

This layer connects the core to running Hermes agent profiles. It's the bridge between abstract hierarchy operations and actual Hermes processes.

### ProfileBridge — Discovery & Sync

`ProfileBridge` scans `~/.hermes/profiles/` and syncs discovered profiles into the registry:

1. Lists subdirectories in the profiles directory
2. For each profile, reads `SOUL.md` and guesses the role via keyword matching:
   - "ceo", "chief executive" → `ceo`
   - "department head", "director" → `department_head`
   - "project manager", "pm" → `project_manager`
   - Default: `department_head`
3. Creates the profile in the registry (skips if already exists)

This is how existing Hermes profiles join the hierarchy without manual registration.

### GatewayHook — Message Handling

Each active profile runs a `GatewayHook` that implements the `MessageHandler` protocol:

- On `TASK_REQUEST`: validates the profile is active, spawns a worker via `WorkerBridge`, links to delegation chain if `chain_id` is in the payload, wires completion callback for result propagation
- On `STATUS_QUERY`: collects worker status and returns a `STATUS_RESPONSE`

The gateway strips tool traces from worker output, extracting only the final prose summary for upstream delivery.

### WorkerBridge — Spawning & Completion

`WorkerBridge` implements the `WorkerManager` protocol and maps hierarchy operations to actual Hermes/Claude CLI invocations:

- **Spawn**: registers a worker in the `SubagentRegistry`, invokes the Hermes or Claude CLI as a subprocess
- **Complete**: updates the registry, fires completion callbacks, propagates results if part of a chain
- **Resume**: loads serialized worker state from disk and reconstructs the agent context

### IPCListener — Polling Loop

`IPCListener` runs a background polling loop per profile:

1. Poll `ipc.db` for pending messages addressed to this profile (every 2 seconds by default)
2. For each message, call `GatewayHook.handle_message()`
3. Mark the message as `READ`
4. If the handler returns a response, send it back via the bus

### Message Router

`HermesMessageRouter` decides how to deliver a message:

1. Check if the target profile has a directory in `~/.hermes/profiles/`
2. If the profile's gateway is active, deliver directly
3. If not, activate the profile (spawn its gateway daemon) and queue the message

### Memory Bridge

`MemoryBridge` handles bidirectional sync between Hermes' native `MEMORY.md` files and the hierarchy's structured SQLite memory. Runs on profile activation.

**Native → Hierarchy (import):**
- Reads `~/.hermes/profiles/<name>/memories/MEMORY.md`
- Parses each line/bullet as a memory entry
- Deduplicates via content hashing against existing hierarchy entries
- Imports new entries into the profile's `MemoryStore` as `context` type, `warm` tier

**Hierarchy → Native (export):**
- Reads hot-tier and recent warm-tier entries from the profile's `MemoryStore`
- Reads ancestor memory (hot tier from chain of command)
- Reads relevant shared knowledge from the `KnowledgeBase`
- Writes everything to `~/.hermes/profiles/<name>/memories/HIERARCHY_CONTEXT.md`
- Hermes picks up this file at session startup alongside the native `MEMORY.md`

This ensures agents see hierarchy context in their system prompt without having to call tools first, and that anything they save in native `MEMORY.md` flows into the structured hierarchy store.

### Delivery

Results are delivered through a file-based queue at `~/.hermes/hierarchy/delivery/`. The root profile's gateway hook (`_deliver_to_owner`) fires Telegram notifications or other delivery mechanisms when results arrive at the top of the chain.

A specialist bubble-up filter suppresses auto-forwarded specialist responses — the owner only hears from PMs, not from their internal specialists.

### Configuration

```python
@dataclass
class HermesConfig:
    profiles_dir: Path       # ~/.hermes/profiles
    workspace_dir: Path      # ~/.hermes/workspace
    poll_interval: float     # 2.0 seconds
    db_base_dir: Path        # ~/.hermes/hierarchy
```

Configurable via environment variables: `HERMES_PROFILES_DIR`, `HERMES_WORKSPACE_DIR`, `HERMES_POLL_INTERVAL`, `HERMES_DB_BASE_DIR`.

---

## 7. Agent Tools

**Module:** `tools/hierarchy_tools.py`

Twelve tools are made available to all Hermes profiles in the hierarchy. These are the functions agents call during their sessions to interact with the org.

### Communication

| Tool | Parameters | What It Does |
|------|-----------|-------------|
| `send_to_profile` | `to`, `message`, `priority`, `track`, `direct`, `wait_for_response`, `deliver_to` | Send a task or message to any profile. `track=True` creates a delegation chain. `direct=True` bypasses the specialist guard. |
| `check_inbox` | `limit` | Poll pending messages from the IPC bus. Returns sender, type, priority, and payload preview. |

### Visibility

| Tool | Parameters | What It Does |
|------|-----------|-------------|
| `org_chart` | `root`, `show_status`, `active_only` | Render the organizational tree as Unicode text. |
| `profile_status` | `profile` | Get a profile's info, memory stats, worker counts, and pending message count. |
| `get_project_status` | `pm_profile` | Get a PM's active workers, recent completions, token costs, and pending messages. |

### Worker Management

| Tool | Parameters | What It Does |
|------|-----------|-------------|
| `spawn_tracked_worker` | `task`, `toolsets`, `context` | Spawn a worker subagent with lifecycle tracking. Returns the `subagent_id`. |

### Memory & Knowledge

| Tool | Parameters | What It Does |
|------|-----------|-------------|
| `save_memory` | `content`, `entry_type`, `metadata` | Save a decision, learning, or context to the profile's personal memory store. |
| `search_knowledge` | `query`, `category` | Full-text search across the shared knowledge base. |
| `share_knowledge` | `category`, `title`, `content`, `tags` | Publish knowledge to the shared knowledge base. |
| `read_ancestor_memory` | `limit` | Read hot-tier memory from profiles above you in the chain of command. |
| `get_chain_context` | `chain_id` | Pull hot memory and shared knowledge from all ancestors in one call. |

### Administration

| Tool | Parameters | What It Does |
|------|-----------|-------------|
| `create_profile` | `name`, `display_name`, `role`, `parent` | Register a new profile in the hierarchy. |

### Specialist Guard

When the root profile (`hermes`) sends to a specialist (dev-*, sec-*), the tool blocks the send and returns an error redirecting to the specialist's parent PM. This prevents the CEO from bypassing the PM and directly coordinating specialists.

The `direct=True` parameter overrides this guard — used only when the user explicitly names a specialist as the target.

---

## 8. Templates

**Module:** `templates/`

Every profile in the hierarchy gets a set of markdown documents that define its behavior. Templates are organized by role:

```
templates/
├── PLAYBOOK.md                        # Global rules (shared by all roles)
└── roles/
    ├── ceo/
    │   ├── SOUL.md                    # Identity and purpose
    │   ├── HANDOFF.md                 # How to receive/return work
    │   ├── WORKFLOWS.md              # Standard operating procedures
    │   ├── TOOLS.md                   # Available tools
    │   └── CONTEXT.md                 # Background context
    ├── department_head/
    │   └── (same 5 files)
    ├── project_manager/
    │   └── (same 5 files)
    └── specialist/
        └── (same 5 files)
```

Templates use `{{variable}}` placeholders that are filled in during generation:

```python
variables = build_variables(
    profile_name="pm-backend",
    display_name="Backend PM",
    role="project_manager",
    parent_profile="cto",
    department="engineering",
    description="Manages backend API development",
)
generate_profile_docs(profile_dir, "project_manager", variables)
```

Generated docs are written into `~/.hermes/profiles/<name>/` where Hermes reads them during agent sessions.

There's also an AI-powered generator (`generator.py`) that can produce customized docs from a free-text purpose description by prompting the Hermes CLI.

---

## 9. Dashboard

**Module:** `ui/`

A web interface for monitoring the hierarchy.

```bash
python -m ui                              # http://localhost:5000
python -m ui --port 8080 --ws-port 8081   # Custom ports
python -m ui --no-realtime                # Without WebSocket updates
```

### API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/api/profiles` | Profile CRUD |
| `/api/org-tree` | Organization tree structure |
| `/api/messages` | IPC message listing and polling |
| `/api/workers` | Subagent status and lifecycle |
| `/api/chains` | Delegation chain tracking |
| `/api/memory` | Memory store browsing |
| `/api/dashboard` | Aggregate metrics |
| `/api/setup` | Setup and onboarding |

### Real-Time Updates

When `--no-realtime` is not set, the dashboard starts:

- `DatabaseWatcher` — polls SQLite databases for changes
- `EventBus` — internal pub/sub for change events
- `WebSocket server` — pushes updates to connected browsers

---

## Design Patterns

### Thread Safety

All core classes use `threading.Lock()` to guard SQLite operations. Connections are created with `check_same_thread=False` and use WAL mode for concurrent read access.

### Protocol Interfaces

Core modules define contracts using `typing.Protocol` (structural subtyping). The integration layer implements these protocols without inheriting from base classes:

- `MessageHandler` — process incoming messages
- `ProfileActivator` — activate/deactivate profiles
- `MessageRouter` — route messages to profiles
- `WorkerManager` — spawn, complete, resume workers
- `WorkerResult` — standard result interface

### Lazy Initialization

Tools and integration components use lazy singletons. Database connections and registries are created on first use, not at import time. This keeps startup fast and avoids circular dependencies.

### Graceful Degradation

`ContextManager` continues building context even when some components are unavailable. If the knowledge base is missing, it skips that section and logs a warning. If no memory store exists for a profile, it proceeds without memory context.

### ID Generation

All IDs use a prefix + truncated UUID pattern for readability:

| Entity | Format | Example |
|--------|--------|---------|
| Message | `msg-{hex[:12]}` | `msg-a1b2c3d4e5f6` |
| Correlation | `corr-{hex[:12]}` | `corr-f6e5d4c3b2a1` |
| Worker | `sa-{full_uuid}` | `sa-550e8400-e29b-41d4-a716-446655440000` |
| Memory | `mem-{hex[:12]}` | `mem-1a2b3c4d5e6f` |
| Knowledge | `kb-{hex[:12]}` | `kb-6f5e4d3c2b1a` |
| Chain | `chain-{hex[:12]}` | `chain-abcdef123456` |
