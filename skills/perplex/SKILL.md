---
name: perplex
description: "Perplexity/Sonar-style current web research workflow. Use for current facts, source-backed answers, market/vendor/news checks, and research gates before planning."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [research, web-search, perplexity, sources, workshop]
    related_skills: [par, deep]
---

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
