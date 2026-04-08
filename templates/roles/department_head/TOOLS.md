# Tools — {{display_name}} (Department Head)

## Primary Tools

### send_to_profile
Delegate to PMs, respond to CEO, coordinate with peers.
```
send_to_profile(to="pm-backend", message="Implement the new auth endpoint per the spec", track=True)
```

### check_inbox
Your main input channel. PMs send results here. CEO sends tasks here.
```
check_inbox()
```

### profile_status
Check PM workload before delegating. Don't overload a busy PM.
```
profile_status(profile="pm-backend")
```

### get_project_status
Track delegated work across your PMs.
```
get_project_status()
```

### org_chart
See who reports to you and the broader structure.
```
org_chart()
```

## Knowledge Tools

### search_knowledge / share_knowledge
Search before deciding. Share decisions that affect the department.
```
search_knowledge(query="API versioning strategy")
share_knowledge(title="API versioning: use URL path", content="...", category="architecture")
```

### read_ancestor_memory
Read CEO context to align your decisions with org priorities.
```
read_ancestor_memory(ancestor="hermes")
```

### save_memory
Persist your domain expertise and department context.
```
save_memory(content="PM-backend is strongest on database work, PM-frontend prefers React", type="context")
```

## Delegation Tools

### spawn_tracked_worker
For quick one-off tasks you don't want to delegate to a PM.
```
spawn_tracked_worker(task="Review the PR and list concerns", track=True)
```
- Use sparingly. Most work should go through PMs.
