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

## Step 8b — Patch the Hermes gateway for chat integration

The hierarchy system works standalone, but to send messages to profiles from Telegram or Discord (via `/talk`, `/send`, and voice messages), three patches must be applied to the Hermes gateway. Apply them now.

**Find the gateway file:**

   python3 -c "import gateway.run as m; import inspect; print(inspect.getfile(m))"

Store this path as GATEWAY_RUN. If that fails, try:
   find ~/.hermes -name "run.py" -path "*/gateway/*" | head -1

**Check what is already present:**

   grep -n "canonical == .talk.\|canonical == .send.\|_route_to_focused_profile\|_handle_send_command" "$GATEWAY_RUN"

For each item NOT found, apply the corresponding patch below. If all are present, skip to Step 9.

---

### Patch 1 — `/talk` and `/exit` commands (apply if `canonical == "talk"` is missing)

Find the command dispatch block in GATEWAY_RUN (search for `if canonical == "voice":`) and add these two lines immediately after the voice block:

```python
        if canonical == "talk":
            return await self._handle_talk_command(event)

        if canonical == "send":
            return await self._handle_send_command(event)
```

Then find the class that contains `_handle_voice_command` and add these methods to it. Add them as new methods in the class (before any existing `_route_to_focused_profile` if present, or before `_handle_rollback_command` or any similarly-named late method):

```python
    async def _handle_talk_command(self, event) -> str:
        """Handle /talk <profile> — focus this session on a hierarchy profile."""
        import sys as _sys, logging as _logging
        _logger = _logging.getLogger(__name__)
        source = event.source
        session_key = self._session_key_for_source(source)
        if not hasattr(self, '_focus_targets'):
            self._focus_targets = {}
        args = event.get_command_args().strip()
        if not args:
            current = self._focus_targets.get(session_key)
            if current:
                return f"Currently talking to: {current}\nUse /exit to return to CEO."
            return "Usage: /talk <profile-name>\nExample: /talk pm-discord-poker\nUse /exit to return to CEO."
        profile_name = args.split()[0].lower()
        try:
            _project_root = str(__import__("pathlib").Path.home() / "hermes_work" / "projects" / "hierarchical-agents")
            if _project_root not in _sys.path:
                _sys.path.insert(0, _project_root)
            from hierarchy.core.registry.profile_registry import ProfileRegistry
            _reg_path = str(__import__("pathlib").Path.home() / ".hermes" / "hierarchy" / "registry.db")
            reg = ProfileRegistry(_reg_path)
            try:
                profile = reg.get_profile(profile_name)
                if profile is None:
                    profiles = [p.profile_name for p in reg.list_profiles()]
                    return f"Profile '{profile_name}' not found.\nAvailable: {', '.join(profiles)}"
            finally:
                reg.close()
        except Exception as e:
            _logger.warning("Failed to validate profile '%s': %s", profile_name, e)
        self._focus_targets[session_key] = profile_name
        return f"Now talking directly to: {profile_name}\nYour messages will be routed to this profile.\nUse /exit to return to CEO."

    async def _handle_exit_command(self, event) -> str:
        """Handle /exit — return to CEO from a /talk session."""
        source = event.source
        session_key = self._session_key_for_source(source)
        if not hasattr(self, '_focus_targets'):
            self._focus_targets = {}
        previous = self._focus_targets.pop(session_key, None)
        if previous:
            return f"Left conversation with {previous}. Back to CEO."
        return "Not in a /talk session. Already talking to CEO."

    async def _handle_send_command(self, event) -> str:
        """Handle /send <profile> <message> — fire-and-forget to any hierarchy profile."""
        import sys as _sys, logging as _logging
        _logger = _logging.getLogger(__name__)
        args = event.get_command_args().strip()
        if not args or " " not in args:
            return "Usage: /send <profile> <message>\nExample: /send pm-bookmark-analysis look into UI/UX examples"
        profile_name, _, message_text = args.partition(" ")
        profile_name = profile_name.lower().strip()
        message_text = message_text.strip()
        if not message_text:
            return "Usage: /send <profile> <message>"
        source = event.source
        try:
            _project_root = str(__import__("pathlib").Path.home() / "hermes_work" / "projects" / "hierarchical-agents")
            if _project_root not in _sys.path:
                _sys.path.insert(0, _project_root)
            from hierarchy.core.ipc.message_bus import MessageBus
            from hierarchy.core.ipc.models import MessageType
            from hierarchy.core.registry.profile_registry import ProfileRegistry
            from pathlib import Path as _Path
            _hier_dir = _Path.home() / ".hermes" / "hierarchy"
            reg = ProfileRegistry(str(_hier_dir / "registry.db"))
            try:
                profile = reg.get_profile(profile_name)
                if profile is None:
                    profiles = [p.profile_name for p in reg.list_profiles()]
                    return f"Profile '{profile_name}' not found.\nAvailable: {', '.join(profiles)}"
            finally:
                reg.close()
            bus = MessageBus(str(_hier_dir / "ipc.db"))
            bus.send(
                from_profile="hermes",
                to_profile=profile_name,
                message_type=MessageType.TASK_REQUEST,
                payload={
                    "task": message_text,
                    "user_talk": True,
                    "deliver_to": "origin",
                    "origin_platform": source.platform.value if source.platform else "",
                    "origin_chat_id": source.chat_id,
                },
            )
            bus.close()
            _logger.info("Sent direct message to '%s': %.100s", profile_name, message_text)
            return f"Sent to {profile_name}. Response will be delivered here when ready."
        except Exception as e:
            _logger.error("Failed to send to '%s': %s", profile_name, e)
            return f"Failed to send to {profile_name}: {e}"

    async def _route_to_focused_profile(self, event, session_key: str):
        """Route a message to the focused profile and deliver the response."""
        import sys as _sys, logging as _logging
        _logger = _logging.getLogger(__name__)
        if not hasattr(self, '_focus_targets'):
            self._focus_targets = {}
        target = self._focus_targets.get(session_key)
        if not target:
            return None
        source = event.source
        message_text = getattr(event, 'text', '') or ""
        # Transcribe voice/audio before routing so voice works in /talk sessions
        if not message_text.strip() and getattr(event, 'media_urls', None):
            try:
                audio_paths = []
                for i, path in enumerate(event.media_urls):
                    mtype = event.media_types[i] if i < len(event.media_types) else ""
                    if mtype.startswith("audio/") or getattr(event, 'message_type', None) in ('voice', 'audio'):
                        audio_paths.append(path)
                if audio_paths:
                    message_text = await self._enrich_message_with_transcription("", audio_paths)
            except Exception:
                pass
        if not message_text.strip():
            return None
        try:
            _project_root = str(__import__("pathlib").Path.home() / "hermes_work" / "projects" / "hierarchical-agents")
            if _project_root not in _sys.path:
                _sys.path.insert(0, _project_root)
            from hierarchy.core.ipc.message_bus import MessageBus
            from hierarchy.core.ipc.models import MessageType
            from pathlib import Path as _Path
            _hier_dir = _Path.home() / ".hermes" / "hierarchy"
            bus = MessageBus(str(_hier_dir / "ipc.db"))
            bus.send(
                from_profile="hermes",
                to_profile=target,
                message_type=MessageType.TASK_REQUEST,
                payload={
                    "task": message_text,
                    "user_talk": True,
                    "deliver_to": "origin",
                    "origin_platform": source.platform.value if source.platform else "",
                    "origin_chat_id": source.chat_id,
                },
            )
            bus.close()
            _logger.info("Routed /talk message to '%s': %.100s", target, message_text)
            return f"Message sent to {target}. Response will be delivered here when ready."
        except Exception as e:
            _logger.error("Failed to route to '%s': %s", target, e)
            return f"Failed to send to {target}: {e}"
```

---

### Patch 2 — Route `/talk` messages during active session (apply if the main message handler doesn't check `_focus_targets`)

In the main `handle_message` method (or equivalent), find the section that checks for commands. Add this block BEFORE the session claims the agent (look for a comment like "Claim this session" or the `_running_agents` sentinel):

```python
        # Route to focused profile if /talk is active
        if not command and hasattr(self, '_focus_targets'):
            _session_key = self._session_key_for_source(event.source)
            if _session_key in self._focus_targets:
                return await self._route_to_focused_profile(event, _session_key)
```

---

### Patch 3 — Remove response truncation (apply always)

Search GATEWAY_RUN for this pattern:
```python
if len(display) > 3000:
    display = display[:3000] + "\n... (truncated)"
```

If found, delete those two lines. The Telegram delivery hook handles chunking natively — this truncation silently cuts off long PM responses.

---

After applying all patches, restart the Hermes gateway:

   systemctl --user restart hermes-gateway.service

Verify it restarted cleanly:
   systemctl --user is-active hermes-gateway.service

Expected: `active`

---

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
