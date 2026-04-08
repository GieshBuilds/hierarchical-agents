# Workflows — {{display_name}} (Specialist)

## Workflow: Execute Task
```
1. check_inbox — receive TASK_REQUEST
2. Read task: what's the goal, what's the deliverable?
3. IF unclear: send_to_profile(to=parent, message="Clarification needed: ...")
4. search_knowledge — any relevant prior work?
5. Do the work:
   a. Read relevant code/docs
   b. Make changes / produce output
   c. Test / verify your work
6. save_memory — key findings or decisions
7. Result auto-sends on completion
```

## Workflow: Code Implementation
```
1. Receive implementation task with specific requirements
2. Read the target files and understand current state
3. Plan changes: what to modify, what to add, what to test
4. Implement changes
5. Run tests — ensure they pass
6. Summarize: what changed, why, how to verify
```

## Workflow: Analysis / Review
```
1. Receive analysis or review task
2. Gather information:
   a. Read relevant code, docs, configs
   b. search_knowledge for context
3. Analyze against criteria given in the task
4. Write structured findings:
   - Summary
   - Detailed findings (with evidence)
   - Recommendations
5. share_knowledge if findings are broadly useful
```

## Workflow: Investigation
```
1. Receive "look into X" task
2. Systematic search:
   a. Find relevant files
   b. Trace code paths
   c. Check logs, configs, dependencies
3. Document findings as you go
4. Deliver: what you found, what it means, what to do next
```
