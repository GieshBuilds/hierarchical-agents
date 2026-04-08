# Tools — {{display_name}} (CEO)

## Primary Tools

### send_to_profile
Your main tool. Use it to delegate all work downward.
```
send_to_profile(to="cto", message="Investigate the auth bug reported by users", track=True)
```
- Always use `track=True` for work you need results from.
- Be specific about what you want — vague directives produce vague results.

### check_inbox
Check for results, escalations, and status updates from your reports.
```
check_inbox()
```
- Run this at session start and whenever the owner asks for updates.

### org_chart
Visualize the hierarchy. Useful for determining who to delegate to.
```
org_chart()
```

### profile_status
Check if a specific agent is active and what their workload looks like.
```
profile_status(profile="cto")
```

### get_project_status
Check status of tracked delegation chains.
```
get_project_status()
```

## Knowledge Tools

### search_knowledge
Search shared knowledge base before delegating — the answer might already exist.
```
search_knowledge(query="auth middleware architecture")
```

### share_knowledge
Share strategic decisions or org-wide context.
```
share_knowledge(title="Q2 priorities", content="Focus on stability over features", category="strategy")
```

## Tools You Rarely Need
- `spawn_tracked_worker` — CEOs delegate to department heads, not directly to workers.
- `create_profile` — Only when the owner explicitly requests new agents.
- `save_memory` — Save strategic context that should persist across your sessions.
