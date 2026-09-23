---
title: "Superpowers — Apply a rigorous end-to-end workflow"
sidebar_label: "Superpowers"
description: "Apply a rigorous end-to-end workflow"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Superpowers

Apply a rigorous end-to-end workflow.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/superpowers` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `execution`, `planning`, `tdd`, `debugging`, `code-review`, `research` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /superpowers — Rigorous Execution Mode

Use this skill when the user invokes `/superpowers` or asks you to bring a stronger, more systematic workflow to a difficult task.

The goal is not theatrics. The goal is to stop winging it and execute with the right process.

## Core behavior

1. Identify the task type.
2. Choose the right workflow.
3. Gather the missing context with tools when possible.
4. Create a compact plan only if the task needs one.
5. Execute instead of merely advising.
6. Verify with tests, checks, source reads, or concrete evidence.
7. Report what changed and what remains risky.

## Workflow router

### Coding feature or refactor

Use a plan-first workflow:

- inspect the repo and conventions;
- identify files likely to change;
- make small, reviewable edits;
- run focused tests first, then broader tests if feasible;
- include changed files and test commands in the final report.

If available, follow `software-development/writing-plans` before large implementation work.

### Bug or production failure

Use systematic debugging:

- reproduce or find the failing signal;
- read logs/errors before editing;
- identify the narrow root cause;
- make the smallest durable fix;
- add or run a regression test;
- verify the failure mode is gone.

If available, follow `software-development/systematic-debugging`.

### New code behavior

Use test-driven development when practical:

- write or identify the failing test;
- make the minimal implementation pass;
- refactor only after green;
- do not skip verification because the change “looks obvious”.

If available, follow `software-development/test-driven-development`.

### Code review

Use review mode:

- inspect diff and affected contracts;
- look for correctness, security, data loss, concurrency, migrations, and UX regressions;
- distinguish blocking issues from nits;
- propose concrete fixes.

If available, follow `software-development/requesting-code-review`.

### Large independent workstreams

Use subagents only when parallelism genuinely helps:

- split into independent tasks;
- pass each subagent complete context;
- verify their claims before reporting success;
- do not delegate user interaction or irreversible side effects.

If available, follow `software-development/subagent-driven-development`.

### Research or strategy

Use evidence-first synthesis:

- search current sources when facts may be stale;
- separate observations from judgment;
- name confidence and gaps;
- finish with a recommendation or next action.

## Output contract

For non-trivial work, final response should include:

- result;
- files or artifacts changed, if any;
- commands/checks run;
- verification result;
- remaining risks or next step, if any.

Keep it compact. Strong process should reduce noise, not create ceremony.

## Short invocation

If the user writes only `/superpowers`, ask:

`What task should I run in rigorous execution mode? Send the goal and any repo/path/context.`
