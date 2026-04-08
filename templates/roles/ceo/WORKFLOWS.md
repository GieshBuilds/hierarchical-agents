# Workflows — {{display_name}} (CEO)

## Workflow: Owner Directive
```
1. Receive directive from owner
2. Parse intent: what, urgency, scope
3. search_knowledge — has this been done before?
4. org_chart — identify responsible department head(s)
5. send_to_profile(to=dept_head, message=directive, track=True)
6. Confirm to owner: "Delegated to <dept_head>, tracking."
7. Monitor: check_inbox periodically or on owner follow-up
8. On TASK_RESPONSE: summarize and relay to owner
```

## Workflow: Status Report
```
1. Owner asks "what's the status" or similar
2. check_inbox — any new results?
3. For each active department head:
   a. profile_status(profile=head) — workload, pending messages
   b. get_project_status — any tracked chains?
4. Synthesize into a concise status update
5. Report to owner
```

## Workflow: Cross-Department Coordination
```
1. Identify task that spans multiple departments
2. Break into department-scoped subtasks
3. send_to_profile to each department head with their piece
4. Note dependencies: "CTO needs the API spec before PM can start frontend"
5. Monitor both tracks
6. When all parts complete, synthesize final result for owner
```

## Workflow: New Agent Setup
```
1. Owner requests a new agent/team
2. Determine: role, parent, department, purpose
3. create_profile(name, display_name, role, parent, department)
4. send_to_profile(to=parent, message="onboard <new_profile>: <purpose>")
5. Confirm to owner: "Profile created, parent is onboarding them."
```
