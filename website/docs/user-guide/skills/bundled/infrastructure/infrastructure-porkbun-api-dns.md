---
title: "Porkbun Api Dns — Manage Porkbun DNS records safely"
sidebar_label: "Porkbun Api Dns"
description: "Manage Porkbun DNS records safely"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Porkbun Api Dns

Manage Porkbun DNS records safely.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/infrastructure/porkbun-api-dns` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `dns`, `porkbun`, `domains`, `infrastructure`, `workshop` |
| Related skills | [`server-doctor`](../../bundled/devops/devops-server-doctor.md), [`public-endpoint-ops`](../../bundled/devops/devops-public-endpoint-ops.md) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Porkbun API DNS

Use this for domains hosted at Porkbun.

## Credential boundary

Expected environment variables:

```bash
PORKBUN_API_KEY=...
PORKBUN_SECRET_KEY=...
```

Keep them in `.env` or a secret store outside git. Never print raw key values.

## Workflow

1. Confirm the domain is actually managed by Porkbun.
2. Retrieve existing records before writing.
3. Create/edit/delete only the requested record.
4. Re-read Porkbun records through the API.
5. Verify authoritative nameservers and public resolvers.

## Verification

```bash
for ns in maceio.ns.porkbun.com salvador.ns.porkbun.com curitiba.ns.porkbun.com fortaleza.ns.porkbun.com; do
  dig +short @"$ns" <host> <TYPE>
done

for r in 1.1.1.1 8.8.8.8 9.9.9.9; do
  dig +short @"$r" <host> <TYPE>
done
```

## Output contract

- record: domain, name, type, target, TTL;
- credential status: found/missing/invalid, no raw values;
- API result;
- authoritative DNS proof;
- public resolver proof;
- propagation caveat if needed.
