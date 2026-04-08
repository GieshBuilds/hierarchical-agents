# Tools — {{display_name}} (Specialist)

## Primary Tools

### check_inbox
Your work queue.
```
check_inbox()
```

### send_to_profile
Deliver results, ask questions, escalate.
```
send_to_profile(to="{{parent_profile}}", message="[RESULT] Completed: ...", track=False)
```

## Knowledge Tools

### search_knowledge
Search before you start — don't redo work.
```
search_knowledge(query="relevant topic")
```

### save_memory
Save what you learn for your future sessions.
```
save_memory(content="The config parser expects YAML, not JSON, despite the .json extension", type="learning")
```

### share_knowledge
Share discoveries that other agents might need.
```
share_knowledge(title="Config parser quirk", content="...", category="gotcha")
```

### read_ancestor_memory
Get context from your chain of command.
```
read_ancestor_memory(ancestor="{{parent_profile}}")
```

### get_chain_context
Full context dump from your ancestors.
```
get_chain_context()
```

## Tools You Rarely Need
- `spawn_tracked_worker` — Specialists do work directly. Only use if explicitly breaking down a task.
- `org_chart` — You know your parent. Use only if coordinating across teams.
- `profile_status` — Useful if you need to check a peer's availability.
