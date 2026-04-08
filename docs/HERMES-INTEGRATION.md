# Hermes Integration Guide

This guide walks through connecting hierarchical-agents to an existing Hermes setup — from first install to running profiles that communicate, delegate, and share memory.

## Prerequisites

- **Hermes** installed and working (`hermes --help` works)
- At least one Hermes profile in `~/.hermes/profiles/`
- Python 3.10+

## Installation

```bash
git clone https://github.com/GieshBuilds/hierarchical-agents.git
cd hierarchical-agents
pip install -e .
```

No extra dependencies needed for core functionality. For the web dashboard:

```bash
pip install -e ".[ui]"
```

---

## Step 1: Sync Your Profiles

Run the sync script once to bring your existing Hermes profiles into the hierarchy registry:

```bash
python scripts/sync_hermes_profiles.py --show-chart
```

This scans `~/.hermes/profiles/`, reads each profile's `SOUL.md` to guess a role, and registers them in the hierarchy database at `~/.hermes/hierarchy/registry.db`.

**Sample output:**

```
Found 4 profile(s): hermes, cto, pm-backend, dev-backend
Syncing...

  Added   (4): hermes, cto, pm-backend, dev-backend

Done. All non-CEO profiles default to parent='hermes'.

hermes (ceo) [active]
├── cto (department_head) [active]
├── pm-backend (project_manager) [active]
└── dev-backend (specialist) [active]
```

**How roles are guessed:** ProfileBridge scans each `SOUL.md` for keywords like `ceo`, `director`, `project manager`, `pm`, etc. The `hermes` profile is always treated as CEO. Unknown profiles default to `department_head`.

**Re-running is safe.** Profiles that already exist are skipped.

---

## Step 2: Set Roles and Parents

After sync, every non-CEO profile defaults to `parent='hermes'`. If you want a layered hierarchy (e.g., PM under CTO instead of directly under CEO), reassign parents:

```bash
# PM reports to CTO instead of directly to hermes
python -m core reassign-parent --name pm-backend --parent cto

# View the updated chart
python -m core show-org-chart
```

Or set roles explicitly:

```bash
python -m core update-role --name cto --role department_head
python -m core update-role --name pm-backend --role project_manager
```

The hierarchy is flexible. Any non-CEO profile can report to any other profile. The only hard rules: one CEO (always `hermes`), every other profile needs a parent, no circular refs.

---

## Step 3: Install the Gateway Script

The gateway is a background process that listens for IPC messages on behalf of a profile. The activation system expects a `hierarchy_gateway.py` script at `~/.hermes/hierarchy/`:

```bash
mkdir -p ~/.hermes/hierarchy
cp scripts/hierarchy_gateway.py ~/.hermes/hierarchy/hierarchy_gateway.py
```

Or symlink it so you get updates automatically:

```bash
ln -sf "$(pwd)/scripts/hierarchy_gateway.py" ~/.hermes/hierarchy/hierarchy_gateway.py
```

---

## Step 4: Start Gateways

Each active profile needs a gateway running to receive IPC messages. Start them manually or let the system auto-start them when messages arrive.

### Manual start

```bash
# Start a gateway for each profile (runs in background)
python ~/.hermes/hierarchy/hierarchy_gateway.py start hermes &
python ~/.hermes/hierarchy/hierarchy_gateway.py start cto &
python ~/.hermes/hierarchy/hierarchy_gateway.py start pm-backend &
```

Logs go to `~/.hermes/hierarchy/logs/gateway-<profile>.log`. PID files at `~/.hermes/hierarchy/logs/gateway-<profile>.pid`.

### Auto-start

Gateways start automatically when a profile first receives a message. The `HermesProfileActivator` detects that no gateway is running and launches one. No manual setup needed beyond installing the script.

### Stop a gateway

```bash
python ~/.hermes/hierarchy/hierarchy_gateway.py stop cto
```

### One-shot mode (cron-friendly)

If you prefer not to run persistent daemons:

```bash
# Process whatever is in cto's inbox right now, then exit
python ~/.hermes/hierarchy/hierarchy_gateway.py process cto
```

---

## Step 5: Add Hierarchy Tools to Your Profiles

Profiles need access to the 12 hierarchy tools (send messages, check inbox, delegate tasks, share knowledge, etc.). Register `tools/hierarchy_tools.py` as a tool provider for each profile in your Hermes configuration.

The tool file is at `tools/hierarchy_tools.py` relative to the repo root. It uses two environment variables to locate the databases:

```bash
export HIERARCHY_PROJECT_ROOT=/path/to/hierarchical-agents
export HERMES_DB_BASE_DIR=~/.hermes/hierarchy
```

Add these to your shell profile (`.bashrc`, `.zshrc`, etc.) so they're available when Hermes starts.

The 12 tools each profile gets:

| Tool | What It Does |
|------|-------------|
| `send_to_profile` | Send a task to any profile in the org chart |
| `check_inbox` | Read pending messages and task results |
| `org_chart` | View the full organizational hierarchy |
| `profile_status` | Check if a profile is active, see their workload |
| `spawn_tracked_worker` | Spawn a worker subagent with lifecycle tracking |
| `get_project_status` | Check delegated work and worker status |
| `save_memory` | Persist decisions and context to personal memory |
| `search_knowledge` | Search the shared knowledge base |
| `share_knowledge` | Publish knowledge for the whole org |
| `read_ancestor_memory` | Read memory from profiles above you in the chain |
| `get_chain_context` | Pull full context from your chain of command |
| `create_profile` | Register a new profile in the hierarchy |

---

## Step 6: Generate Profile Documents

Each profile should have a set of hierarchy-aware documents: `HANDOFF.md`, `WORKFLOWS.md`, `TOOLS.md`, and `PLAYBOOK.md`. Generate them from templates:

```python
from pathlib import Path
from templates import generate_profile_docs, build_variables

# Point at the profile's actual Hermes directory
profile_dir = Path.home() / ".hermes" / "profiles" / "pm-backend"

variables = build_variables(
    profile_name="pm-backend",
    display_name="Backend PM",
    role="project_manager",
    parent_profile="cto",
    department="engineering",
    description="Manages backend API development",
)

generate_profile_docs(profile_dir, "project_manager", variables)
# Writes: HANDOFF.md, WORKFLOWS.md, TOOLS.md, CONTEXT.md, PLAYBOOK.md
```

These files become part of the profile's context — Hermes reads them when starting a session as that profile.

---

## Step 7: Memory Bridge

The memory bridge runs automatically on gateway startup. It:

1. **Imports** entries from the profile's native `MEMORY.md` into the hierarchy's structured SQLite memory (deduped by content hash).
2. **Exports** hot-tier hierarchy memory, ancestor context, and shared knowledge to a `HIERARCHY_CONTEXT.md` file that Hermes reads at session startup.

After a gateway starts for a profile, check that the export file was created:

```bash
cat ~/.hermes/profiles/pm-backend/memories/HIERARCHY_CONTEXT.md
```

You should see sections for **Active Memory**, **Recent Context**, **From [ancestor]**, and **Shared Knowledge**.

### Manual sync

You can also trigger a sync manually without the gateway:

```python
from pathlib import Path
from core.memory.memory_store import MemoryStore
from core.memory.models import MemoryScope
from integrations.hermes.memory_bridge import sync_memory

store = MemoryStore("~/.hermes/hierarchy/memory/pm-backend.db", "pm-backend", MemoryScope.project)

sync_memory(
    profile_name="pm-backend",
    profiles_dir=Path.home() / ".hermes" / "profiles",
    memory_store=store,
)
```

---

## Verify It's Working

```bash
# Check the org chart
python -m core show-org-chart

# Send a test message from hermes to cto
python -m core send-message --from hermes --to cto \
    --type task_request \
    --payload '{"task": "Review the Q2 roadmap"}' \
    --priority normal

# Check cto's inbox
python -m core poll-messages --profile cto

# Check IPC bus stats
python -m core ipc-stats

# View memory for a profile
python -m core inspect-memory pm-backend \
    --memory-db ~/.hermes/hierarchy/memory/pm-backend.db \
    --scope project
```

---

## Database Layout

Everything lives under `~/.hermes/hierarchy/`:

```
~/.hermes/hierarchy/
  registry.db               # Org chart — all profiles, roles, parents
  ipc.db                    # Message bus — all in-flight and delivered messages
  chains.db                 # Delegation chain tracking
  memory/
    <profile>.db            # Per-profile scoped memory (tiered hot→cold)
    knowledge.db            # Shared knowledge base (all profiles can read/write)
  workers/
    <pm>/subagents.db       # Per-PM worker registry
  logs/
    gateway-<profile>.log   # Gateway daemon logs
    gateway-<profile>.pid   # Gateway PID files
```

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `HERMES_PROFILES_DIR` | `~/.hermes/profiles/` | Where Hermes profile directories live |
| `HERMES_DB_BASE_DIR` | `~/.hermes/hierarchy/` | Where hierarchy databases are stored |
| `HERMES_POLL_INTERVAL` | `2.0` | Seconds between IPC message polls |
| `HIERARCHY_PROJECT_ROOT` | Auto-detected | Repo root (for tool resolution) |

---

## Troubleshooting

**Gateway won't start — script not found**

```
ERROR: Gateway script not found at ~/.hermes/hierarchy/hierarchy_gateway.py
```

Run: `cp scripts/hierarchy_gateway.py ~/.hermes/hierarchy/`

**Profiles not discovered**

ProfileBridge looks in `~/.hermes/profiles/` by default. If your profiles are elsewhere, set `HERMES_PROFILES_DIR`:

```bash
export HERMES_PROFILES_DIR=/custom/path/to/profiles
python scripts/sync_hermes_profiles.py
```

**Role guessed wrong**

Override after sync:
```bash
python -m core update-role --name my-profile --role project_manager
```

**`HIERARCHY_CONTEXT.md` not being generated**

The gateway must have started at least once for the profile. Check the gateway log:

```bash
tail -f ~/.hermes/hierarchy/logs/gateway-pm-backend.log
```

**Messages not being delivered**

Make sure the gateway is running for the recipient profile. Check:

```bash
ls ~/.hermes/hierarchy/logs/*.pid   # Running gateways
python -m core poll-messages --profile cto  # What's in inbox
python -m core ipc-stats            # Bus health
```
