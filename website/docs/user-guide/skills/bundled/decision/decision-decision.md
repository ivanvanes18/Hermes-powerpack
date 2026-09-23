---
title: "Decision — Structure high-stakes decisions and tradeoffs"
sidebar_label: "Decision"
description: "Structure high-stakes decisions and tradeoffs"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Decision

Structure high-stakes decisions and tradeoffs.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/decision` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `decision-making`, `strategy`, `tradeoffs`, `planning` |
| Related skills | [`reasoning-personas`](../../bundled/reasoning-personas/reasoning-personas-reasoning-personas.md), [`rp`](../../bundled/rp/rp-rp.md) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /decision — Decision Framework

Use this skill when the user invokes `/decision` or asks for help making a meaningful choice.

Do not use it for trivial preferences unless the user explicitly asks for `/decision`.

## Operating rules

- Build the decision frame before recommending.
- Challenge the user's framing if the question is too narrow or misleading.
- Separate facts, assumptions, guesses, and judgment.
- If critical information is missing, say so instead of pretending certainty.
- Expand the option set when the user's options are artificially limited.
- Prefer reversible tests, thresholds, and kill criteria over vague advice.
- Be direct. Optimize for decision quality, not comfort.

## Mandatory sequence

### Phase 1: Decision deconstruction

1. Restate the decision as a precise yes/no or A/B/C question.
2. Explain what makes it hard: competing values, missing information, irreversibility, timing, resources, reputation risk, opportunity cost.
3. List the user's unstated assumptions.
4. Ask exactly 3 targeted questions if the missing answers materially affect the recommendation.

If the missing answers determine the decision, stop after Phase 1 and say:

`I should not recommend yet. These answers determine the decision.`

Continue only after the user answers, unless they explicitly ask you to proceed with stated assumptions.

### Phase 2: Options mapping

Map every realistic option, including options the user did not name.

For each option, cover:

- best realistic outcome;
- worst realistic outcome;
- most likely outcome;
- reversibility;
- whether it hides multiple decisions inside one choice.

### Phase 3: Second-order analysis

For the top options, analyze:

- what happens 6 months after the decision;
- what new decisions it creates;
- what doors it closes;
- who else is affected and how incentives shift.

### Phase 4: Decision filters

Apply these filters:

- goal alignment;
- public test;
- comfort trap;
- advice-to-other test;
- downside containment.

If the user's goal is absent or contradictory, name that and use a provisional goal explicitly labeled as an assumption.

### Phase 5: Recommendation

Give:

1. Recommendation with confidence: `High`, `Medium`, or `Low`.
2. The single biggest risk.
3. Kill criteria with a concrete threshold, date, or observable trigger.
4. A 72-hour action plan: Day 1 / Day 2 / Day 3.

## Short invocation

If the user writes only `/decision`, ask:

`What decision are we making? Give me the choice in one sentence, the options you see, and what outcome matters most.`

## Output style

Use clear headings. Be concise but not shallow. Avoid generic strategy prose. Do not say “it depends” unless you immediately say exactly what it depends on.
