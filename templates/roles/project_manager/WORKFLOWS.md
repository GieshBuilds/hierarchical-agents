# Workflows — {{display_name}} (Project Manager)

## Workflow: Execute Task
```
1. check_inbox — receive TASK_REQUEST
2. Read task carefully. Identify: goal, constraints, deliverable.
3. search_knowledge — has this been done before? Any prior art?
4. read_ancestor_memory — any relevant context from dept head or CEO?
5. Plan approach:
   a. IF investigation/planning: do it yourself
   b. IF implementation: spawn_tracked_worker with clear instructions
   c. IF complex: break into subtasks, spawn multiple workers
6. Execute or monitor workers
7. Compile result in standard format
8. Result auto-sends via TASK_RESPONSE, or send manually if needed
```

## Workflow: Investigation Task
```
1. Receive "look into X" or "investigate Y" task
2. Explore the codebase / system relevant to the question
3. Take notes as you go — save_memory for key findings
4. Compile findings:
   - What you found
   - Assessment / recommendation
   - Proposed next steps
5. Deliver result
```

## Workflow: Implementation Task
```
1. Receive implementation task
2. Plan the implementation:
   a. What files need to change?
   b. What's the test strategy?
   c. Any risks or dependencies?
3. spawn_tracked_worker(task="<specific implementation instructions>")
   - Include: file paths, what to change, how to test
4. Monitor worker completion
5. Review result: does it meet the requirement?
6. If tests pass: deliver result
7. If issues: fix and retry, or escalate
```

## Workflow: Planning Task
```
1. Receive "create a plan for X"
2. Research:
   a. search_knowledge for prior work
   b. Read relevant code / docs
   c. read_ancestor_memory for org priorities
3. Write plan with:
   - Goal and scope
   - Approach (broken into phases/steps)
   - What already exists vs. what needs building
   - Risks and open questions
   - Estimated effort per step
4. Commit plan to repo if requested
5. Deliver plan as result
```

## Workflow: Worker Management
```
1. spawn_tracked_worker(task="...", track=True)
2. get_project_status — monitor worker state
3. On worker completion:
   a. Review result_summary
   b. If satisfactory: include in your deliverable
   c. If failed: read error, retry with adjusted instructions, or escalate
4. Never leave workers in "running" state unmonitored
```
