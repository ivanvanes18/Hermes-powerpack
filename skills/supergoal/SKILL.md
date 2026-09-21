---
name: supergoal
description: "Principal-grade plan-only workflow for non-trivial software/product work. Builds a verified execution package before handing off to /goal or an implementation agent."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [planning, execution, roadmap, autonomous-work, verification, workshop]
    related_skills: [writing-plans, test-driven-development, requesting-code-review]
---

# SuperGoal

Use this when a project is too large, risky, or ambiguous for a one-shot answer.

SuperGoal is **plan-only**. It creates a rigorous execution package; it does not pretend the work is done.

## Outputs

Create a `.supergoal/` package with:

- `THINKING.md` — objective, constraints, assumptions, risks.
- `ROADMAP.md` — phases with acceptance criteria.
- `STATE.md` — current status, blockers, next action.
- `PROTOCOL.md` — how the implementation agent should work and verify.
- `PHASE-*.md` — bite-sized phase specs.
- `HANDOFF.md` — exact command/prompt for the implementation agent.

## Workflow

1. Clarify the real objective and non-goals.
2. Inspect the repo/system if available.
3. Identify source of truth, deployment surface, secrets boundary, and tests.
4. Split into small phases that can each be verified.
5. Add kill criteria and rollback/stop conditions.
6. Hand off to `/goal`, a coding agent, or a human only after the plan has concrete acceptance tests.

## Done criteria

A SuperGoal is done when another agent can start from `HANDOFF.md` without guessing what to build, where to build it, or how to prove it works.
