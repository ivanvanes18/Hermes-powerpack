---
title: "Perplex — Research current topics with cited web sources"
sidebar_label: "Perplex"
description: "Research current topics with cited web sources"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Perplex

Research current topics with cited web sources.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/perplex` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `research`, `web-search`, `perplexity`, `sources`, `workshop` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# perplex

Use this skill when current web facts matter.

## Workflow

1. State the research question in one sentence.
2. Search the web with a current-source backend available in this Hermes install.
3. Prefer primary sources, official docs, pricing pages, changelogs, GitHub repos, and recent posts over SEO summaries.
4. Extract the best sources before synthesis.
5. Answer with citations/links and separate facts from judgment.

## Output contract

```text
Короткий вывод.

Источники:
- <source> — what it proves
- <source> — what it proves

Answer / recommendation:
...

Uncertainty:
...
```

## API boundary

If a local Perplexity/Sonar key is configured, use it through the environment or the configured Hermes web backend. Never commit keys, paste keys into chat, or hardcode provider credentials in this skill.
