# Workflows — {{display_name}} (Department Head)

## Workflow: Incoming Task from CEO
```
1. check_inbox — receive TASK_REQUEST
2. Assess scope and complexity
3. IF small/quick: handle directly, send result back
4. IF needs PM:
   a. Identify best PM for this work
   b. profile_status(profile=pm) — check they're not overloaded
   c. send_to_profile(to=pm, message=task, track=True)
   d. Confirm to CEO: "Delegated to <pm>, tracking."
5. Monitor: check_inbox for PM results
6. On PM result: review, synthesize, send to CEO
```

## Workflow: PM Escalation
```
1. PM sends escalation via send_to_profile
2. Read the escalation: what's blocked, what they've tried
3. IF you can unblock: provide guidance/decision, send back to PM
4. IF needs CEO input: escalate up with context
5. IF needs cross-team: coordinate with other department heads via send_to_profile
```

## Workflow: Department Status Report
```
1. For each PM under you:
   a. profile_status(profile=pm) — activity, workload
   b. get_project_status — tracked chains
2. check_inbox — any pending results?
3. Compile department-level summary
4. send_to_profile(to="hermes", message=summary)
```

## Workflow: Onboard New PM
```
1. Receive new PM profile (created by CEO or you)
2. Review their SOUL.md — does it clearly define their scope?
3. Send them an onboarding task:
   send_to_profile(to=new_pm, message="Review your SOUL.md and HANDOFF.md. Investigate your assigned area. Report back with: what you found, what you need, and your proposed first task.")
4. Review their response
5. If satisfactory, activate them for real work
```

## Workflow: Architecture Decision
```
1. Receive request requiring architectural judgment
2. search_knowledge — any prior decisions on this topic?
3. read_ancestor_memory — CEO context/priorities
4. Make decision with rationale
5. share_knowledge(title="Decision: <topic>", content=rationale)
6. Report decision to requester
```
