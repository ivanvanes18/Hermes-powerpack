---
title: "Supabase Project Ops — Operate Supabase projects safely"
sidebar_label: "Supabase Project Ops"
description: "Operate Supabase projects safely"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Supabase Project Ops

Operate Supabase projects safely.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/devops/supabase-project-ops` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `supabase`, `backend`, `database`, `auth`, `rls`, `migrations`, `workshop` |
| Related skills | [`server-doctor`](../../bundled/devops/devops-server-doctor.md), [`public-endpoint-ops`](../../bundled/devops/devops-public-endpoint-ops.md) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Supabase Project Ops

Use this when a workshop project needs a real backend: auth, profiles, CRM-like data, admin screens, or app state.

## Workflow

1. Define user stories before schema.
2. Sketch tables, relationships, and access rules.
3. Write migrations instead of manual dashboard-only changes when the repo is source-of-truth.
4. Add Row Level Security policies deliberately.
5. Keep anon/service keys out of git and chat.
6. Test with at least two roles: ordinary user and admin/operator.

## Minimum schema checklist

- profiles linked to auth users;
- created_at/updated_at fields;
- owner/user foreign keys where data is private;
- RLS enabled for user-owned tables;
- admin path separated from public user path;
- seed/test data clearly marked as sample.

## Output contract

Return schema, RLS policy plan, migration path, verification queries, and any secrets/config that must be set locally.
