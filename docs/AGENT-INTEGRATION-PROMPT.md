# Agent Integration Prompt

Copy and paste the block below directly to your Claude Code agent (or any capable AI coding assistant). The agent will read your existing Hermes setup, install the hierarchy layer, configure gateways, and set up systemd services — all automatically.

---

```
You are setting up the hierarchical-agents coordination layer on top of an existing Hermes agent installation. Follow every step below exactly, in order. Do not skip steps or ask for confirmation between them unless an error blocks you.

## What you are installing

hierarchical-agents adds a coordination layer on top of Hermes profiles: an org chart, a SQLite-backed IPC message bus, task delegation chains, tiered per-profile memory, and a web dashboard. It does not replace Hermes — it sits alongside it.

All state is stored in SQLite under ~/.hermes/hierarchy/. The core has zero external dependencies (stdlib only).

## Prerequisites check

Before doing anything else, verify the following. Stop and report if any check fails.

1. Run: hermes --help
   Expected: Hermes CLI usage. If this fails, Hermes is not installed.

2. Run: ls ~/.hermes/profiles/
   Expected: at least one subdirectory. If empty, ask the user to create a Hermes profile first.

3. Run: python3 --version
   Expected: Python 3.10 or later.

4. Store the Python interpreter path as PYTHON_BIN:
   PYTHON_BIN=$(which python3)

## Step 1 — Clone and install

If the repo is not already present, clone it. Ask the user for the target location if they haven't told you:

   git clone https://github.com/GieshBuilds/hierarchical-agents.git
   cd hierarchical-agents
   pip install -e .
   pip install -e ".[ui]"   # optional: adds the web dashboard

Store the absolute repo path as REPO_ROOT. Verify the install:

   python3 -m core --help

If that fails, check pip's output for errors before continuing.

## Step 2 — Set environment variables

Append to ~/.bashrc (or ~/.zshrc) — use real absolute paths, not placeholders:

   export HIERARCHY_PROJECT_ROOT="$REPO_ROOT"
   export HERMES_DB_BASE_DIR="$HOME/.hermes/hierarchy"
   export HERMES_PROFILES_DIR="$HOME/.hermes/profiles"

Then source the file to apply them in the current session.

## Step 3 — Sync existing Hermes profiles

   cd "$REPO_ROOT"
   python3 scripts/sync_hermes_profiles.py --show-chart

Read the output:
- "hermes" is automatically assigned the CEO role
- All other profiles default to parent=hermes, role=project_manager

Collect all profile names from the output — you need them in Step 5.

If sync fails with a path error, check that HERMES_PROFILES_DIR is set correctly.

## Step 4 — Review and fix roles

Show the org chart to the user and ask: "Do any roles or parent assignments look wrong?"

To fix a role:
   python3 -m core update-role --name <profile_name> --role <role>
   # Valid roles: department_head, project_manager, specialist

To change a parent:
   python3 -m core reassign-parent --name <profile_name> --parent <parent_name>

Show the updated chart after any changes:
   python3 -m core show-org-chart

## Step 5 — Install the gateway script and create log directory

   mkdir -p ~/.hermes/hierarchy/logs
   ln -sf "$REPO_ROOT/scripts/hierarchy_gateway.py" ~/.hermes/hierarchy/hierarchy_gateway.py

Verify the symlink:
   ls -la ~/.hermes/hierarchy/hierarchy_gateway.py

## Step 6 — Test one gateway manually

Run the gateway in the background, check that it starts cleanly, then stop it:

   PYTHONPATH="$REPO_ROOT" python3 ~/.hermes/hierarchy/hierarchy_gateway.py start hermes &
   GATEWAY_PID=$!
   sleep 3
   kill $GATEWAY_PID 2>/dev/null

Expected: no ImportError or crash. If you see an ImportError, PYTHONPATH is wrong — double-check that REPO_ROOT contains pyproject.toml.

## Step 7 — Set up systemd user services for persistent gateways

For each profile name collected in Step 3, create a systemd user service file.

   mkdir -p ~/.config/systemd/user

File: ~/.config/systemd/user/hierarchy-gateway-<PROFILE_NAME>.service

Use real values for <PROFILE_NAME>, <REPO_ROOT>, <PYTHON_BIN>, and the user's absolute home directory. Do NOT use ~ in service files.

---
[Unit]
Description=Hierarchy Gateway for Hermes profile '<PROFILE_NAME>'
After=network.target

[Service]
Type=simple
ExecStart=<PYTHON_BIN> /home/<USERNAME>/.hermes/hierarchy/hierarchy_gateway.py start <PROFILE_NAME>
WorkingDirectory=/home/<USERNAME>/.hermes/hierarchy
Environment=PYTHONPATH=<REPO_ROOT>
Environment=HERMES_DB_BASE_DIR=/home/<USERNAME>/.hermes/hierarchy
Environment=HERMES_PROFILES_DIR=/home/<USERNAME>/.hermes/profiles
Environment=HIERARCHY_PROJECT_ROOT=<REPO_ROOT>
Restart=on-failure
RestartSec=5s
StandardOutput=append:/home/<USERNAME>/.hermes/hierarchy/logs/gateway-<PROFILE_NAME>.log
StandardError=append:/home/<USERNAME>/.hermes/hierarchy/logs/gateway-<PROFILE_NAME>.log

[Install]
WantedBy=default.target
---

After writing all service files:

   systemctl --user daemon-reload

For each profile:
   systemctl --user enable --now hierarchy-gateway-<PROFILE_NAME>.service
   systemctl --user status hierarchy-gateway-<PROFILE_NAME>.service

Expected: "active (running)". If "failed", read the log:
   journalctl --user -u hierarchy-gateway-<PROFILE_NAME>.service --no-pager -n 30

Common failures:
- Wrong ExecStart path
- PYTHONPATH not set (ImportError in the log)
- Profile name misspelled or not in the registry

## Step 8 — Patch the Hermes gateway for chat integration

This step enables /talk, /exit, /send commands and voice message routing in your Telegram or Discord chats. A patch script handles everything automatically.

Run from the repo root:

   cd "$REPO_ROOT"
   python3 scripts/patch_hermes_gateway.py

The script will print a status line for each of the four patches:
- [✓] means the patch was applied
- [–] means it was already present and was skipped

It creates a .py.bak backup of gateway/run.py before modifying it.

After it finishes, restart the Hermes gateway:

   systemctl --user restart hermes-gateway.service
   systemctl --user is-active hermes-gateway.service

Expected: `active`

If the patch script cannot find gateway/run.py automatically, pass the path explicitly:
   python3 scripts/patch_hermes_gateway.py --gateway-run /path/to/gateway/run.py

## Step 9 — Register hierarchy tools for each profile

The hierarchy tools file exposes 12 agent-callable functions (messaging, delegation, memory read/write, org visibility). Register it for each profile so agents can call them.

Tool file: $REPO_ROOT/tools/hierarchy_tools.py

To find where Hermes tool configs live for a profile:
   ls ~/.hermes/profiles/<PROFILE_NAME>/

Look for a tools.json, tools.py, toolsets/, or similar. The exact mechanism depends on the Hermes version. If tools are registered via a config file, add the hierarchy_tools.py path to that file. If tools are registered by copying or symlinking files into a tools directory, do that. The tool file reads HIERARCHY_PROJECT_ROOT and HERMES_DB_BASE_DIR at runtime — both are set by the systemd service from Step 7.

If you cannot determine the tool registration mechanism from the profile's directory structure, read ~/.hermes/hermes-agent/hermes_cli/main.py or the Hermes documentation to understand how tools are loaded.

## Step 10 — Verify the full system

Run in order and report each result:

1. Org chart:
   python3 -m core show-org-chart

2. IPC bus health:
   python3 -m core ipc-stats

3. Send a test message to the first non-CEO profile:
   python3 -m core send-message \
       --from hermes \
       --to <first_non_ceo_profile> \
       --type task_request \
       --payload '{"task": "Hierarchy integration test — please acknowledge"}' \
       --priority normal

4. Check the recipient's inbox:
   python3 -m core poll-messages --profile <first_non_ceo_profile>

5. Confirm gateway log files exist:
   ls ~/.hermes/hierarchy/logs/

Expected final state:
- show-org-chart prints all profiles in a tree
- ipc-stats shows healthy bus metrics
- poll-messages shows the test task_request
- A log file exists for every running profile gateway

## Step 11 — Optional: start the web dashboard

If the user installed the ui extras (pip install -e ".[ui]"):

   python3 -m ui
   # Access at http://localhost:5000

## What to report when done

1. Which profiles were synced and their assigned roles
2. Which systemd services are running
3. Whether the test message was delivered
4. Any steps that required manual intervention or produced errors
5. The full REPO_ROOT path for the user's reference

If anything failed, report the exact error and which step it occurred in.
```
