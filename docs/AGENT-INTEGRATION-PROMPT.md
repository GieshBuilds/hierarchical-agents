# Agent Integration Prompt

Copy and paste the block below directly to your Claude Code agent (or any capable AI coding assistant). The agent will read your existing Hermes setup, install the hierarchy layer, configure gateways, and set up systemd services — all automatically.

---

```
You are setting up the hierarchical-agents coordination layer on top of an existing Hermes agent installation. Follow every step below exactly, in order. Do not skip steps or ask for confirmation between them unless an error blocks you.

## What you are installing

hierarchical-agents adds a coordination layer on top of Hermes profiles: an org chart, a SQLite-backed IPC message bus, task delegation chains, tiered per-profile memory, and a web dashboard. It does not replace Hermes — it sits alongside it.

All state is stored in SQLite under ~/.hermes/hierarchy/. The core has zero external dependencies (stdlib only).

## Prerequisites check

Before doing anything else, verify the following. If any check fails, stop and report what is missing.

1. Run: hermes --help
   - Expected: Hermes CLI usage information. If this fails, Hermes is not installed or not on PATH. Do not continue.

2. Run: ls ~/.hermes/profiles/
   - Expected: at least one subdirectory (a Hermes profile). If the directory is empty or does not exist, ask the user to create at least one Hermes profile before continuing.

3. Run: python3 --version
   - Expected: Python 3.10 or later. If the version is below 3.10, stop and report it.

4. Determine the active Python interpreter path:
   Run: which python3
   Store this as PYTHON_BIN. You will need it for systemd service files.

## Step 1 — Clone and install

If the hierarchical-agents repo is not already present on this machine, clone it now. Ask the user where they want it if they have not already told you:

   git clone https://github.com/GieshBuilds/hierarchical-agents.git
   cd hierarchical-agents

Install the package in editable mode:

   pip install -e .

If the user also wants the web dashboard:

   pip install -e ".[ui]"

Store the absolute path of the repo as REPO_ROOT. You will need it throughout.
Example: REPO_ROOT=/home/yourname/projects/hierarchical-agents

Verify the install worked:
   python3 -m core --help

If that command fails, the package is not installed correctly. Check pip's output for errors before continuing.

## Step 2 — Set environment variables

Determine the correct values for these variables. Use the actual paths on this machine, not placeholders.

   HIERARCHY_PROJECT_ROOT=<absolute path to the repo>
   HERMES_DB_BASE_DIR=$HOME/.hermes/hierarchy
   HERMES_PROFILES_DIR=$HOME/.hermes/profiles

Write these to the user's shell profile. Check which shell is active:
   echo $SHELL

Then append (do not overwrite) to the appropriate file (~/.bashrc for bash, ~/.zshrc for zsh):

   export HIERARCHY_PROJECT_ROOT="$REPO_ROOT"
   export HERMES_DB_BASE_DIR="$HOME/.hermes/hierarchy"
   export HERMES_PROFILES_DIR="$HOME/.hermes/profiles"

Source the file so the variables are active in the current session:
   source ~/.bashrc   # or ~/.zshrc

## Step 3 — Sync existing Hermes profiles

Run the sync script to import all existing Hermes profiles into the hierarchy registry:

   cd "$REPO_ROOT"
   python3 scripts/sync_hermes_profiles.py --show-chart

Read the output carefully:
- The org chart will be printed showing all discovered profiles and their auto-detected roles.
- The profile named "hermes" is always assigned the CEO role.
- All other profiles default to parent=hermes.

Collect the list of all profile names from the output — you will need it in Step 5.

If the sync fails with a path error, check that HERMES_PROFILES_DIR is set correctly and that the profiles directory exists.

## Step 4 — Review and fix roles

After the sync, report the org chart to the user and ask: "Do any roles or parent assignments look wrong?"

If the user wants to fix a role:
   python3 -m core update-role --name <profile_name> --role <role>
   # Valid roles: department_head, project_manager, specialist

If the user wants to change a parent (to create a layered hierarchy instead of flat):
   python3 -m core reassign-parent --name <profile_name> --parent <parent_profile_name>

After any changes, show the updated chart:
   python3 -m core show-org-chart

## Step 5 — Install the gateway script

Copy the gateway script to the hierarchy directory so it is accessible regardless of where the repo lives:

   mkdir -p ~/.hermes/hierarchy
   ln -sf "$REPO_ROOT/scripts/hierarchy_gateway.py" ~/.hermes/hierarchy/hierarchy_gateway.py

Verify the symlink is correct:
   ls -la ~/.hermes/hierarchy/hierarchy_gateway.py

## Step 6 — Create the logs directory

   mkdir -p ~/.hermes/hierarchy/logs

## Step 7 — Test one gateway manually

Before setting up systemd, verify the gateway works for one profile. Run this in the foreground and let it run for about 5 seconds, then interrupt it (Ctrl-C):

   PYTHONPATH="$REPO_ROOT" python3 ~/.hermes/hierarchy/hierarchy_gateway.py start hermes

Expected output:
   INFO hierarchy_gateway: Gateway starting for profile 'hermes' (pid=...)
   INFO hierarchy_gateway: Gateway running for 'hermes' — waiting for messages

If you see an ImportError, the PYTHONPATH is not set correctly. Double-check that REPO_ROOT points to the directory containing pyproject.toml.

## Step 8 — Set up systemd user services for persistent gateways

For each active profile, create a systemd user service so the gateway starts on login and restarts if it crashes.

First, ensure the systemd user directory exists:
   mkdir -p ~/.config/systemd/user

For EACH profile name you collected in Step 3, create a service file. Use the actual profile name and the actual REPO_ROOT and PYTHON_BIN values — do not use placeholders:

File path: ~/.config/systemd/user/hierarchy-gateway-<PROFILE_NAME>.service

Content (substitute <PROFILE_NAME>, <REPO_ROOT>, and <PYTHON_BIN> with real values):

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

Do NOT use ~ in the service file. Use the absolute home directory path (e.g., /home/yourname or /root).

After writing all service files, reload and enable them:

   systemctl --user daemon-reload

For each profile:
   systemctl --user enable --now hierarchy-gateway-<PROFILE_NAME>.service

Then verify each one started:
   systemctl --user status hierarchy-gateway-<PROFILE_NAME>.service

Expected: the status should show "active (running)". If it shows "failed", read the log:
   journalctl --user -u hierarchy-gateway-<PROFILE_NAME>.service --no-pager -n 30

Common causes of failure:
- Wrong path to the Python interpreter in ExecStart
- PYTHONPATH not set correctly (ImportError in the log)
- The profile name is misspelled or does not exist in the registry

## Step 9 — Configure hierarchy tools for each profile

Each Hermes profile needs access to the 12 hierarchy tools. The tools are in:
   <REPO_ROOT>/tools/hierarchy_tools.py

Register this file as a tool provider for each profile according to how Hermes handles tool configuration for that installation. The tool file reads HIERARCHY_PROJECT_ROOT and HERMES_DB_BASE_DIR at runtime — both are already set in the environment from Step 2.

If you are unsure how to register tools in this particular Hermes installation, read the Hermes documentation or check the profile's existing tools configuration files.

## Step 10 — Verify the full system

Run these checks in order. Report each result:

1. Org chart:
   python3 -m core show-org-chart

2. IPC bus health:
   python3 -m core ipc-stats

3. Send a test message from hermes to the first non-CEO profile:
   python3 -m core send-message \
       --from hermes \
       --to <first_non_ceo_profile> \
       --type task_request \
       --payload '{"task": "Hierarchy integration test — please acknowledge"}' \
       --priority normal

4. Check the recipient's inbox:
   python3 -m core poll-messages --profile <first_non_ceo_profile>

5. Confirm gateway PID files exist:
   ls ~/.hermes/hierarchy/logs/*.pid

Expected final state:
- show-org-chart prints all profiles in a tree
- ipc-stats shows healthy bus metrics
- poll-messages shows the test task_request message
- A .pid file exists for every profile whose gateway service is running

## Step 11 — Optional: start the dashboard

If the user installed the ui extras (pip install -e ".[ui]"), the dashboard can be started:

   python3 -m ui
   # Access at http://localhost:5000

This is optional. The hierarchy works fully without the dashboard.

## What to report when done

Summarize:
1. Which profiles were synced and what roles were assigned
2. Which systemd services are running (list each one)
3. Whether the test message was delivered successfully
4. Any steps that required manual intervention or that produced errors
5. The full path to the repo (REPO_ROOT) for the user's reference

If anything failed, report the exact error output and which step it occurred in.
```
