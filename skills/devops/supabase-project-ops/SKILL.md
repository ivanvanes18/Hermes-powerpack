---
name: supabase-project-ops
description: "Plan and operate Supabase-backed app features: auth, tables, migrations, RLS, local/dev/prod separation, and safe credential handling."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [supabase, backend, database, auth, rls, migrations, workshop]
    related_skills: [server-doctor, public-endpoint-ops]
---

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
