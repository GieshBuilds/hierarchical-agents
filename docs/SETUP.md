# Setup Guide

This guide covers everything needed to add the hierarchy coordination layer on top of an existing Hermes installation — from first install through persistent production gateways.

## Prerequisites

- **Hermes** installed and working (`hermes --help` runs without errors)
- At least one Hermes profile in `~/.hermes/profiles/`
- Python 3.10 or later

## Installation

Clone the repository and install the package:

```bash
git clone https://github.com/GieshBuilds/hierarchical-agents.git
cd hierarchical-agents
pip install -e .
```

The core has zero external dependencies — it uses Python's stdlib only. If you also want the web dashboard:

```bash
pip install -e ".[ui]"
```

> If you run Hermes through a dedicated virtualenv, install hierarchical-agents into that same environment so the gateway can import both packages without path gymnastics.

---

## Environment Variables

Set these before running any hierarchy commands. Add them to your shell profile (`.bashrc`, `.zshrc`, etc.) so they persist across sessions.

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_PROFILES_DIR` | `~/.hermes/profiles/` | Where Hermes profile directories live |
| `HERMES_DB_BASE_DIR` | `~/.hermes/hierarchy/` | Where all hierarchy SQLite databases are stored |
| `HERMES_POLL_INTERVAL` | `2.0` | Seconds between IPC message polls in the gateway daemon |
| `HIERARCHY_PROJECT_ROOT` | Auto-detected | Absolute path to the repo root — needed for tool resolution |

Typical shell config:

```bash
export HIERARCHY_PROJECT_ROOT="$HOME/projects/hierarchical-agents"
export HERMES_DB_BASE_DIR="$HOME/.hermes/hierarchy"
export HERMES_PROFILES_DIR="$HOME/.hermes/profiles"
```

`HERMES_DB_BASE_DIR` and `HERMES_PROFILES_DIR` only need to be set explicitly if your installation deviates from the defaults.

---

## Step 1 — Sync Existing Hermes Profiles

Run the sync script once to bring your existing profiles into the hierarchy registry:

```bash
python scripts/sync_hermes_profiles.py --show-chart
```

This scans `$HERMES_PROFILES_DIR`, reads each profile's `SOUL.md` to guess a role, registers every profile in the hierarchy database at `~/.hermes/hierarchy/registry.db`, and prints the resulting org chart.

Sample output:

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

Re-running is safe — profiles that already exist are skipped.

### How role detection works

The sync script scans each profile's `SOUL.md` for keywords:

| Keyword match | Assigned role |
|---|---|
| `hermes` (profile name) | `ceo` (always) |
| "ceo", "chief executive" | `ceo` |
| "department head", "director" | `department_head` |
| "project manager", "pm" | `project_manager` |
| Anything else | `department_head` |

---

## Step 2 — Understand the Four Roles

Every profile in the hierarchy has exactly one role:

| Role | Description |
|---|---|
| `ceo` | Top of the hierarchy. Auto-created as the profile named `hermes`. Exactly one allowed. |
| `department_head` | Domain owner (e.g., CTO, CMO, Head of Research). Optional layer between CEO and PMs. |
| `project_manager` | Manages a specific project or workstream. Spawns and tracks worker subagents. |
| `specialist` | Persistent expert agent for a focused area (e.g., security review, code analysis). |

The CEO (`hermes`) is created automatically when the registry initializes — you do not create it manually. Every other profile must have a parent, and the hierarchy can be as flat or as layered as you want.

### Adjusting roles and parents after sync

After the initial sync, every non-CEO profile defaults to `parent=hermes`. If you want a layered hierarchy, reassign parents:

```bash
# PM reports to CTO instead of directly to CEO
python -m core reassign-parent --name pm-backend --parent cto

# Correct a misdetected role
python -m core update-role --name cto --role department_head

# View the updated org chart
python -m core show-org-chart
```

### Adding profiles that don't have Hermes directories yet

```bash
python -m core create-profile \
    --name cto \
    --display-name "CTO" \
    --role department_head \
    --parent hermes \
    --department engineering
```

---

## Step 3 — Install the Gateway Script

The gateway is a background daemon that listens for IPC messages on behalf of a profile. Copy it (or symlink it) to the hierarchy directory:

```bash
mkdir -p ~/.hermes/hierarchy

# Copy (static snapshot)
cp scripts/hierarchy_gateway.py ~/.hermes/hierarchy/hierarchy_gateway.py

# Or symlink (picks up future updates automatically)
ln -sf "$(pwd)/scripts/hierarchy_gateway.py" ~/.hermes/hierarchy/hierarchy_gateway.py
```

The script self-bootstraps its import path — as long as `HIERARCHY_PROJECT_ROOT` is set (or the script can resolve the repo root from its own location), no additional `PYTHONPATH` manipulation is required at runtime.

---

## Step 4 — Start Gateways

Each active profile needs a running gateway to receive IPC messages. Gateways can be started manually or configured to run as persistent systemd services.

### Manual start (foreground, useful for testing)

```bash
# Run the gateway in the foreground — Ctrl-C to stop
PYTHONPATH=/path/to/hierarchical-agents \
    python ~/.hermes/hierarchy/hierarchy_gateway.py start hermes
```

### Manual start (background daemon)

```bash
# Start gateways for all active profiles, detached from your terminal
PYTHONPATH=/path/to/hierarchical-agents \
    python ~/.hermes/hierarchy/hierarchy_gateway.py start hermes &

PYTHONPATH=/path/to/hierarchical-agents \
    python ~/.hermes/hierarchy/hierarchy_gateway.py start cto &

PYTHONPATH=/path/to/hierarchical-agents \
    python ~/.hermes/hierarchy/hierarchy_gateway.py start pm-backend &
```

Logs go to `~/.hermes/hierarchy/logs/gateway-<profile>.log`.  
PID files go to `~/.hermes/hierarchy/logs/gateway-<profile>.pid`.

### Stop a gateway

```bash
python ~/.hermes/hierarchy/hierarchy_gateway.py stop cto
```

### One-shot mode (cron-friendly)

If you prefer not to run persistent daemons, you can process a profile's inbox on demand and exit:

```bash
# Process all pending messages for cto, then exit
PYTHONPATH=/path/to/hierarchical-agents \
    python ~/.hermes/hierarchy/hierarchy_gateway.py process cto
```

---

## Step 5 — Systemd Service for Persistent Gateways

For production use, run each gateway as a systemd user service so it starts on login and restarts on failure.

### Service file template

Create one service file per profile. Save it as `~/.config/systemd/user/hierarchy-gateway-<profile>.service`:

```ini
[Unit]
Description=Hierarchy Gateway for Hermes profile '%i'
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/env python3 %h/.hermes/hierarchy/hierarchy_gateway.py start %i
WorkingDirectory=%h/.hermes/hierarchy
Environment=PYTHONPATH=__REPO_ROOT__
Environment=HERMES_DB_BASE_DIR=%h/.hermes/hierarchy
Environment=HERMES_PROFILES_DIR=%h/.hermes/profiles
Environment=HIERARCHY_PROJECT_ROOT=__REPO_ROOT__
Restart=on-failure
RestartSec=5s
StandardOutput=append:%h/.hermes/hierarchy/logs/gateway-%i.log
StandardError=append:%h/.hermes/hierarchy/logs/gateway-%i.log

[Install]
WantedBy=default.target
```

Replace `__REPO_ROOT__` with the absolute path to your `hierarchical-agents` clone, e.g. `/home/yourname/projects/hierarchical-agents`.

The `%i` specifier is filled in by systemd from the instance name (the part after `@` in the service name), and `%h` expands to your home directory.

### Install and enable (per profile)

```bash
# Substituting the actual repo path and profile name:
REPO=/home/yourname/projects/hierarchical-agents
PROFILE=hermes

sed \
    -e "s|__REPO_ROOT__|$REPO|g" \
    -e "s|%i|$PROFILE|g" \
    > ~/.config/systemd/user/hierarchy-gateway-${PROFILE}.service << 'EOF'
[Unit]
Description=Hierarchy Gateway for Hermes profile '%i'
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/env python3 %h/.hermes/hierarchy/hierarchy_gateway.py start %i
WorkingDirectory=%h/.hermes/hierarchy
Environment=PYTHONPATH=__REPO_ROOT__
Environment=HERMES_DB_BASE_DIR=%h/.hermes/hierarchy
Environment=HERMES_PROFILES_DIR=%h/.hermes/profiles
Environment=HIERARCHY_PROJECT_ROOT=__REPO_ROOT__
Restart=on-failure
RestartSec=5s
StandardOutput=append:%h/.hermes/hierarchy/logs/gateway-%i.log
StandardError=append:%h/.hermes/hierarchy/logs/gateway-%i.log

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now hierarchy-gateway-${PROFILE}.service
```

Repeat the enable/now step for each profile you want always running.

### Useful systemd commands

```bash
# Check status
systemctl --user status hierarchy-gateway-hermes.service

# View live logs
journalctl --user -u hierarchy-gateway-hermes.service -f

# Restart after a config change
systemctl --user restart hierarchy-gateway-hermes.service

# Stop permanently
systemctl --user disable --now hierarchy-gateway-hermes.service
```

---

## Step 6 — Configure Hierarchy Tools for Each Profile

Each profile needs access to the 12 hierarchy tools (`send_to_profile`, `check_inbox`, `org_chart`, etc.). Register `tools/hierarchy_tools.py` as a tool provider in your Hermes profile configuration.

The tool file expects two environment variables to be set — the same ones you added in the environment section:

```bash
export HIERARCHY_PROJECT_ROOT=/path/to/hierarchical-agents
export HERMES_DB_BASE_DIR=~/.hermes/hierarchy
```

Refer to the [Hermes Integration Guide](HERMES-INTEGRATION.md) for the full steps on registering tools with a Hermes profile, generating profile documents (`HANDOFF.md`, `WORKFLOWS.md`, `TOOLS.md`), and configuring the memory bridge.

---

## Step 7 — Start the Dashboard (Optional)

The web dashboard provides a browser UI for monitoring the org chart, message bus, worker lifecycle, delegation chains, and memory.

```bash
# Install dashboard dependencies if you haven't already
pip install -e ".[ui]"

# Launch
python -m ui
# HTTP: http://localhost:5000
# WebSocket (real-time updates): ws://localhost:5001/ws
```

Custom ports:

```bash
python -m ui --port 8080 --ws-port 8081
python -m ui --no-realtime   # Disable WebSocket, polling only
```

---

## Step 8 — Verify Everything Works

```bash
# 1. Check the org chart
python -m core show-org-chart

# 2. List all registered profiles
python -m core list-profiles --json

# 3. Send a test message
python -m core send-message \
    --from hermes \
    --to cto \
    --type task_request \
    --payload '{"task": "Ping — confirm hierarchy is running"}' \
    --priority normal

# 4. Check the recipient's inbox
python -m core poll-messages --profile cto

# 5. Check bus health
python -m core ipc-stats

# 6. Confirm gateway PID files exist for running profiles
ls ~/.hermes/hierarchy/logs/*.pid
```

If `show-org-chart` prints your profiles and `poll-messages` shows the test message, the hierarchy is working.

---

## Database Layout Reference

All state lives in SQLite under `~/.hermes/hierarchy/`:

```
~/.hermes/hierarchy/
  registry.db               # Org chart — profiles, roles, parents
  ipc.db                    # Message bus — in-flight and delivered messages
  chains.db                 # Delegation chain tracking (task → worker → result)
  memory/
    <profile>.db            # Per-profile scoped memory, tiered hot→cold
    knowledge.db            # Shared knowledge base (all profiles read/write)
  workers/
    <pm>/subagents.db       # Per-PM worker registry
  logs/
    gateway-<profile>.log   # Gateway daemon output
    gateway-<profile>.pid   # Gateway process IDs
  delivery/                 # File-based result delivery queue
```

---

## Troubleshooting

**`python -m core` not found**

Make sure you ran `pip install -e .` from the repo root and that the same Python environment is active.

**Profiles not discovered by sync**

The script looks in `$HERMES_PROFILES_DIR` (default `~/.hermes/profiles/`). If your profiles are elsewhere:

```bash
export HERMES_PROFILES_DIR=/custom/path
python scripts/sync_hermes_profiles.py --show-chart
```

**Role guessed incorrectly**

Override after sync:

```bash
python -m core update-role --name my-profile --role project_manager
```

**Gateway won't start — import error**

The gateway needs to import from the `hierarchical-agents` package. Make sure either:
- `pip install -e .` was run in the active Python environment, or
- `PYTHONPATH` is set to the repo root in your systemd service / shell

**`HIERARCHY_CONTEXT.md` not being generated**

The gateway must have started at least once for the profile. Check its log:

```bash
tail -f ~/.hermes/hierarchy/logs/gateway-pm-backend.log
```

**Messages not delivered**

Confirm the recipient profile's gateway is running:

```bash
ls ~/.hermes/hierarchy/logs/*.pid          # Running gateways
python -m core poll-messages --profile cto  # Messages in inbox
python -m core ipc-stats                    # Bus-wide health
```
