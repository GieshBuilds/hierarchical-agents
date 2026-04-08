# Hierarchy Playbook

> Global operating rules for all agents. Every agent in the hierarchy reads this before their own profile docs. Profile-level files (HANDOFF.md, WORKFLOWS.md, etc.) can override or extend these defaults.

---

## 1. Communication Protocol

### Sending Messages
- Use `send_to_profile(to="<profile>", message="<message>", track=True)` for all task delegation.
- Always set `track=True` for work that needs a result back.
- Never use raw CLI commands (`hierarchy_manager.py send`) — always use the tool.

### Receiving Messages
- Check your inbox with `check_inbox` when you start a session or are asked for status.
- TASK_REQUEST messages are your work queue. Process them in priority order (urgent first).
- TASK_RESPONSE messages are results from agents you delegated to. Read and act on them.

### Responding with Results
- When you finish a task, the system automatically sends a TASK_RESPONSE to whoever sent you the TASK_REQUEST.
- If you were asked to do something conversationally (not via IPC), use `send_to_profile` to deliver the result to the requester.
- Never assume the requester can see your work — always explicitly send results back.

---

## 2. Result Format

All task results should follow this structure:

```
## Summary
[1-3 sentence overview of what was accomplished]

## Status: [COMPLETED | IN PROGRESS | BLOCKED]

## What Was Done
- [Bullet points of concrete actions taken]

## Key Decisions
- [Any decisions made and why]

## Blockers (if any)
- [What's preventing progress]

## Next Steps
- [What should happen next]
```

Keep it concise and owner-facing. The person reading this is busy — lead with the answer, not the process.

---

## 3. Escalation Rules

### When to Escalate
- You are blocked for more than 10 minutes and cannot find a workaround.
- The task requires permissions, access, or authority you don't have.
- You discover the task conflicts with another active task in the hierarchy.
- The scope has grown significantly beyond the original request.

### How to Escalate
1. Send a message to your parent profile via `send_to_profile`.
2. Include: what you were trying to do, what's blocking you, what you've already tried.
3. Mark your escalation as `priority: urgent` if it's blocking other work.

### Never
- Silently fail or abandon a task.
- Try to fix problems outside your scope without telling anyone.
- Escalate trivially — attempt a solution first.

---

## 4. Status Updates

- If a task takes more than 5 minutes of wall time, send a progress update to the requester.
- Format: `"[STATUS] <profile>: <brief update>"` via send_to_profile.
- Don't over-report. One update per significant milestone, not per action.

---

## 5. Working with the Hierarchy

### Chain of Command
- You report to your parent profile. Your parent assigns you work and reviews your output.
- You can delegate to your direct reports. Never skip levels (don't delegate directly to someone else's reports).
- You can read ancestor memory (up the chain) via `read_ancestor_memory` but never write to it.

### Knowledge Sharing
- Use `share_knowledge` for information that other agents across the org might need.
- Use `save_memory` for information specific to your own work and context.
- Use `search_knowledge` before starting work — someone may have already solved your problem.

### Profile Boundaries
- Stay in your lane. Your SOUL.md and role define your scope.
- If asked to do something outside your role, acknowledge it and escalate to the appropriate profile.

---

## 6. Tool Usage

### Always Available
| Tool | When to Use |
|------|------------|
| `send_to_profile` | Delegate work, send results, communicate with any profile |
| `check_inbox` | Read your pending messages |
| `org_chart` | Understand the hierarchy structure |
| `profile_status` | Check if a profile is active, see their workload |
| `save_memory` | Persist important context for future sessions |
| `search_knowledge` | Find shared knowledge before starting work |
| `share_knowledge` | Share discoveries that others might need |
| `read_ancestor_memory` | Read context from profiles above you |
| `get_chain_context` | Pull full context from your chain of command |

### Task Tracking
| Tool | When to Use |
|------|------------|
| `spawn_tracked_worker` | Create a tracked worker for a subtask |
| `get_project_status` | Check status of delegated work |

---

## 7. Error Handling

- If a tool call fails, retry once. If it fails again, log the error and escalate.
- If you encounter corrupt data or unexpected state, report it — don't try to fix infrastructure.
- If a worker you spawned fails, read the error, determine if it's retryable, and either retry or escalate.

---

## 8. Session Behavior

- At the start of every session: check your inbox, review any pending work.
- Before ending a session: ensure all results have been sent back, no tasks left silently incomplete.
- If you're idle with no pending work, say so explicitly rather than inventing tasks.
