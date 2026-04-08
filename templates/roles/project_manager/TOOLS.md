# Tools — {{display_name}} (Project Manager)

## Primary Tools

### check_inbox
Your work queue. Always check first.
```
check_inbox()
```

### spawn_tracked_worker
Your main execution tool. Spawns a worker to do implementation work.
```
spawn_tracked_worker(task="Add input validation to the /api/users endpoint. Update tests.", track=True)
```
- Be **very specific** in the task description. Include file paths, expected behavior, test requirements.
- Vague tasks produce vague results.

### get_project_status
Monitor your spawned workers and delegated chains.
```
get_project_status()
```

### send_to_profile
Send results, ask questions, or escalate to your department head.
```
send_to_profile(to="{{parent_profile}}", message="[RESULT] Completed the auth investigation...", track=False)
```

## Knowledge Tools

### search_knowledge
Always search before starting work.
```
search_knowledge(query="auth endpoint validation")
```

### save_memory
Save investigation findings, decisions, and context.
```
save_memory(content="The auth module uses JWT with RSA256, tokens in httpOnly cookies", type="learning")
```

### share_knowledge
Share findings that other PMs might need.
```
share_knowledge(title="Auth module architecture", content="...", category="architecture")
```

### read_ancestor_memory
Get context from your department head or CEO.
```
read_ancestor_memory(ancestor="{{parent_profile}}")
```

### get_chain_context
Pull full context from your chain of command in one call.
```
get_chain_context()
```

## Less Common Tools

### profile_status
Check if a specialist or peer PM is available.
```
profile_status(profile="specialist-testing")
```

### org_chart
Understand the hierarchy when coordinating across teams.
```
org_chart()
```
