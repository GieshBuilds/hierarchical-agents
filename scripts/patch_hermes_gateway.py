#!/usr/bin/env python3
"""Patch the Hermes gateway (gateway/run.py) to add hierarchy chat integration.

Applies three changes:
  1. /talk, /exit, /send commands and _route_to_focused_profile method
  2. Session focus routing in the main message handler
  3. Removes the 3000-char response truncation in gateway_hook.py (if present)

Safe to run multiple times — each patch is skipped if already applied.

Usage:
    python3 scripts/patch_hermes_gateway.py
    python3 scripts/patch_hermes_gateway.py --gateway-run /path/to/gateway/run.py
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import os
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Locating files
# ---------------------------------------------------------------------------

def find_gateway_run() -> Path:
    """Find the installed gateway/run.py."""
    try:
        import gateway.run as m
        return Path(inspect.getfile(m))
    except ImportError:
        pass
    candidates = [
        Path.home() / ".hermes" / "hermes-agent" / "gateway" / "run.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Last resort: search
    results = list(Path.home().rglob("hermes*/gateway/run.py"))
    if results:
        return results[0]
    raise FileNotFoundError(
        "Could not locate gateway/run.py. "
        "Pass --gateway-run /path/to/gateway/run.py explicitly."
    )


def find_gateway_hook() -> Path | None:
    """Find the installed integrations/hermes/gateway_hook.py (optional)."""
    try:
        import integrations.hermes.gateway_hook as m
        return Path(inspect.getfile(m))
    except ImportError:
        pass
    # Check relative to hermes-agent
    base = Path.home() / ".hermes" / "hermes-agent"
    candidate = base / "integrations" / "hermes" / "gateway_hook.py"
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

MARKER_TALK    = 'canonical == "talk"'
MARKER_SEND    = 'canonical == "send"'
MARKER_FOCUS   = '_focus_targets'
MARKER_TRUNC   = 'display[:3000]'


DISPATCH_PATCH = '''\
        if canonical == "talk":
            return await self._handle_talk_command(event)

        if canonical == "send":
            return await self._handle_send_command(event)

'''

ROUTE_CHECK_PATCH = '''\
        # Route to focused profile if /talk is active
        if not command and hasattr(self, '_focus_targets'):
            _session_key = self._session_key_for_source(event.source)
            if _session_key in self._focus_targets:
                return await self._route_to_focused_profile(event, _session_key)

'''

# Inserted just before the first "_handle_rollback_command" or at end of class
METHODS_PATCH = '''
    async def _handle_talk_command(self, event) -> str:
        """Handle /talk <profile> — focus this session on a hierarchy profile."""
        import sys as _sys, logging as _logging, os as _os
        _logger = _logging.getLogger(__name__)
        source = event.source
        session_key = self._session_key_for_source(source)
        if not hasattr(self, '_focus_targets'):
            self._focus_targets = {}
        args = event.get_command_args().strip()
        if not args:
            current = self._focus_targets.get(session_key)
            if current:
                return f"Currently talking to: {current}\\nUse /exit to return to CEO."
            return "Usage: /talk <profile-name>\\nExample: /talk pm-my-project\\nUse /exit to return to CEO."
        profile_name = args.split()[0].lower()
        try:
            _hier_root = _os.environ.get("HIERARCHY_PROJECT_ROOT", "")
            if _hier_root and _hier_root not in _sys.path:
                _sys.path.insert(0, _hier_root)
            from hierarchy.core.registry.profile_registry import ProfileRegistry
            _reg_path = str(__import__("pathlib").Path.home() / ".hermes" / "hierarchy" / "registry.db")
            reg = ProfileRegistry(_reg_path)
            try:
                if reg.get_profile(profile_name) is None:
                    profiles = [p.profile_name for p in reg.list_profiles()]
                    return f"Profile \'{profile_name}\' not found.\\nAvailable: {', '.join(profiles)}"
            finally:
                reg.close()
        except Exception as e:
            _logger.warning("Could not validate profile \'%s\': %s", profile_name, e)
        self._focus_targets[session_key] = profile_name
        return f"Now talking to: {profile_name}\\nYour messages go directly to this profile.\\nUse /exit to return to CEO."

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
        import sys as _sys, logging as _logging, os as _os
        _logger = _logging.getLogger(__name__)
        args = event.get_command_args().strip()
        if not args or " " not in args:
            return "Usage: /send <profile> <message>\\nExample: /send pm-my-project research competitor landscape"
        profile_name, _, message_text = args.partition(" ")
        profile_name = profile_name.lower().strip()
        message_text = message_text.strip()
        if not message_text:
            return "Usage: /send <profile> <message>"
        source = event.source
        try:
            _hier_root = _os.environ.get("HIERARCHY_PROJECT_ROOT", "")
            if _hier_root and _hier_root not in _sys.path:
                _sys.path.insert(0, _hier_root)
            from hierarchy.core.ipc.message_bus import MessageBus
            from hierarchy.core.ipc.models import MessageType
            from hierarchy.core.registry.profile_registry import ProfileRegistry
            from pathlib import Path as _Path
            _hier_dir = _Path.home() / ".hermes" / "hierarchy"
            reg = ProfileRegistry(str(_hier_dir / "registry.db"))
            try:
                if reg.get_profile(profile_name) is None:
                    profiles = [p.profile_name for p in reg.list_profiles()]
                    return f"Profile \'{profile_name}\' not found.\\nAvailable: {', '.join(profiles)}"
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
            return f"Sent to {profile_name}. Response will be delivered here when ready."
        except Exception as e:
            _logger.error("Failed to send to \'%s\': %s", profile_name, e)
            return f"Failed to send to {profile_name}: {e}"

    async def _route_to_focused_profile(self, event, session_key: str):
        """Route a /talk message to the focused hierarchy profile."""
        import sys as _sys, logging as _logging, os as _os
        _logger = _logging.getLogger(__name__)
        if not hasattr(self, '_focus_targets'):
            self._focus_targets = {}
        target = self._focus_targets.get(session_key)
        if not target:
            return None
        source = event.source
        message_text = getattr(event, 'text', '') or ""
        # Transcribe voice/audio so voice messages work inside /talk sessions
        if not message_text.strip() and getattr(event, 'media_urls', None):
            try:
                audio_paths = []
                for i, path in enumerate(event.media_urls):
                    mtype = event.media_types[i] if i < len(event.media_types) else ""
                    if mtype.startswith("audio/") or str(getattr(event, 'message_type', '')).lower() in ('voice', 'audio'):
                        audio_paths.append(path)
                if audio_paths:
                    message_text = await self._enrich_message_with_transcription("", audio_paths)
            except Exception:
                pass
        if not message_text.strip():
            return None
        try:
            _hier_root = _os.environ.get("HIERARCHY_PROJECT_ROOT", "")
            if _hier_root and _hier_root not in _sys.path:
                _sys.path.insert(0, _hier_root)
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
            return f"Message sent to {target}. Response will be delivered here when ready."
        except Exception as e:
            _logger.error("Failed to route to \'%s\': %s", target, e)
            return f"Failed to send to {target}: {e}"

'''


def backup(path: Path) -> Path:
    backup_path = path.with_suffix(".py.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def patch_dispatch(content: str) -> tuple[str, bool]:
    """Add /talk and /send to the command dispatch block."""
    if MARKER_TALK in content:
        return content, False
    anchor = 'if canonical == "voice":\n            return await self._handle_voice_command(event)\n'
    if anchor not in content:
        # Try without trailing newline variation
        anchor = 'canonical == "voice"'
        idx = content.find(anchor)
        if idx == -1:
            print("  WARNING: Could not find voice command anchor — skipping dispatch patch.")
            return content, False
        # Find end of that if-block line
        end = content.find('\n', idx)
        end = content.find('\n', end + 1)  # skip the return line
        insert_at = end + 1
        return content[:insert_at] + "\n" + DISPATCH_PATCH + content[insert_at:], True

    insert_at = content.find(anchor) + len(anchor)
    return content[:insert_at] + "\n" + DISPATCH_PATCH + content[insert_at:], True


def patch_session_routing(content: str) -> tuple[str, bool]:
    """Add focus-target routing check in the main message handler."""
    if MARKER_FOCUS in content:
        return content, False
    # Insert before the sentinel/agent-claim block
    anchor = "# ── Claim this session before any await"
    if anchor not in content:
        anchor = "_AGENT_PENDING_SENTINEL"
    idx = content.find(anchor)
    if idx == -1:
        print("  WARNING: Could not find session claim anchor — skipping routing patch.")
        return content, False
    # Back up to start of line
    line_start = content.rfind('\n', 0, idx) + 1
    return content[:line_start] + ROUTE_CHECK_PATCH + content[line_start:], True


def patch_methods(content: str) -> tuple[str, bool]:
    """Inject handler methods into the class."""
    if '_handle_talk_command' in content:
        return content, False
    # Insert before _handle_rollback_command if present, else before last class method
    anchor = "    async def _handle_rollback_command"
    if anchor not in content:
        anchor = "    async def _handle_voice_command"
    idx = content.find(anchor)
    if idx == -1:
        print("  WARNING: Could not find method insertion anchor — appending methods.")
        return content + METHODS_PATCH, True
    return content[:idx] + METHODS_PATCH + content[idx:], True


def patch_truncation(content: str) -> tuple[str, bool]:
    """Remove the 3000-char response truncation."""
    if MARKER_TRUNC not in content:
        return content, False
    lines = content.splitlines(keepends=True)
    out = []
    i = 0
    removed = False
    while i < len(lines):
        if 'display[:3000]' in lines[i] or ('len(display) > 3000' in lines[i]):
            # Skip this line and the next if it's the continuation
            if 'len(display) > 3000' in lines[i]:
                i += 1  # skip the truncation line too
            removed = True
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return ''.join(out), removed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-run", help="Path to gateway/run.py")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    # --- gateway/run.py ---
    try:
        run_path = Path(args.gateway_run) if args.gateway_run else find_gateway_run()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Patching: {run_path}")
    content = run_path.read_text(encoding="utf-8")
    original = content
    changed = False

    content, applied = patch_dispatch(content)
    print(f"  [{'✓' if applied else '–'}] Dispatch: /talk and /send commands")
    changed = changed or applied

    content, applied = patch_session_routing(content)
    print(f"  [{'✓' if applied else '–'}] Session routing: /talk focus check")
    changed = changed or applied

    content, applied = patch_methods(content)
    print(f"  [{'✓' if applied else '–'}] Methods: _handle_talk/exit/send/_route_to_focused_profile")
    changed = changed or applied

    content, applied = patch_truncation(content)
    print(f"  [{'✓' if applied else '–'}] Truncation: removed 3000-char response limit")
    changed = changed or applied

    if changed and not args.dry_run:
        bak = backup(run_path)
        print(f"  Backup saved: {bak}")
        run_path.write_text(content, encoding="utf-8")
        print(f"  Written: {run_path}")
    elif not changed:
        print("  All patches already applied — nothing to do.")

    # --- gateway_hook.py truncation (optional) ---
    hook_path = find_gateway_hook()
    if hook_path:
        print(f"\nPatching: {hook_path}")
        hook_content = hook_path.read_text(encoding="utf-8")
        hook_content, applied = patch_truncation(hook_content)
        print(f"  [{'✓' if applied else '–'}] Truncation: removed 3000-char response limit")
        if applied and not args.dry_run:
            bak = backup(hook_path)
            print(f"  Backup saved: {bak}")
            hook_path.write_text(hook_content, encoding="utf-8")
            print(f"  Written: {hook_path}")

    print("\nDone. Restart hermes-gateway for changes to take effect:")
    print("  systemctl --user restart hermes-gateway.service")


if __name__ == "__main__":
    main()
