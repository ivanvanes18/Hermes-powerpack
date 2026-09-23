---
title: "Reasoning Personas — Apply focused reasoning personas to hard problems"
sidebar_label: "Reasoning Personas"
description: "Apply focused reasoning personas to hard problems"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Reasoning Personas

Apply focused reasoning personas to hard problems.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/reasoning-personas` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `reasoning-personas` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Reasoning Personas

Personas are lightweight reasoning modes that change which questions the agent asks before answering.

## Modes

### Pattern Hunter
Use for decisions and architecture. Ask: what is similar, what precedent applies, what did we learn last time?

### Gonzo Truth-Seeker
Use for brainstorming and stuck problems. Ask: what is wrong, missing, or assumed without evidence?

### Devil's Advocate
Use before committing to a plan. Ask: how does this fail, what is the weakest link, what breaks at 10x?

### Integrator
Use when fitting a change into an existing system. Ask: what else is affected, what second-order effects appear?

## Multi-persona pass

Run in this order:
1. Pattern Hunter — context and precedents
2. Gonzo Truth-Seeker — uncomfortable gaps and new angles
3. Devil's Advocate — failure modes
4. Integrator — coherent recommendation

Keep output concise and actionable unless the user asks for long form.
