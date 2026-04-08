"""Claude Code integration adapter.

Bridges the Hierarchical Agent Architecture to Claude Code (Anthropic's
official CLI agent) by mapping core system primitives to Claude Code
equivalents:

  ProfileRegistry  →  ClaudeCodeProfileAdapter  (CLAUDE.md + .claude/ layout)
  MessageBus       →  ClaudeCodeMessageBridge    (task-queue JSON files)
  MemoryStore      →  ClaudeCodeMemoryAdapter    (CLAUDE.md memory sections)

These adapters let teams that use Claude Code as their primary AI coding
agent participate in the hierarchy without modifying any core code.

Typical usage
-------------
::

    from integrations.claude_code import ClaudeCodeAdapter, ClaudeCodeConfig

    config = ClaudeCodeConfig.from_env()
    adapter = ClaudeCodeAdapter(registry=registry, config=config)

    # Generate a CLAUDE.md for a profile
    adapter.profile_adapter.generate_claude_md("pm-hier-arch")

    # Export pending IPC messages as .claude/tasks/*.json
    adapter.message_bridge.export_pending_messages("pm-hier-arch")

    # Import task results back into IPC
    adapter.message_bridge.import_results("pm-hier-arch")

Stdlib only — no external dependencies.
"""

from __future__ import annotations

from integrations.claude_code.adapter import ClaudeCodeAdapter
from integrations.claude_code.config import ClaudeCodeConfig
from integrations.claude_code.memory_adapter import ClaudeCodeMemoryAdapter
from integrations.claude_code.message_bridge import ClaudeCodeMessageBridge
from integrations.claude_code.profile_adapter import ClaudeCodeProfileAdapter

__all__ = [
    "ClaudeCodeAdapter",
    "ClaudeCodeConfig",
    "ClaudeCodeMemoryAdapter",
    "ClaudeCodeMessageBridge",
    "ClaudeCodeProfileAdapter",
]
