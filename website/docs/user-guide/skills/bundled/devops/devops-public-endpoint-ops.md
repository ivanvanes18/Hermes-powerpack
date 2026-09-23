---
title: "Public Endpoint Ops — Operate public HTTP endpoints safely"
sidebar_label: "Public Endpoint Ops"
description: "Operate public HTTP endpoints safely"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Public Endpoint Ops

Operate public HTTP endpoints safely.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/devops/public-endpoint-ops` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `endpoint`, `nginx`, `tls`, `dns`, `deploy`, `devops`, `workshop` |
| Related skills | [`server-doctor`](../../bundled/devops/devops-server-doctor.md), [`porkbun-api-dns`](../../bundled/infrastructure/infrastructure-porkbun-api-dns.md) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Public Endpoint Ops

Use this when a local service must become reachable through a public domain or when an existing public endpoint fails.

## Workflow

1. Identify the app port and health route locally.
2. Verify the service on localhost first.
3. Configure reverse proxy only for the requested host/path.
4. Add DNS only after the target IP/host is confirmed.
5. Configure TLS and redirects.
6. Verify from outside the server.
7. Record rollback steps.

## Verification commands

```bash
curl -fsS http://127.0.0.1:<port>/health || true
curl -I https://example.com
curl -fsS https://example.com/health || true
```

## Safety

Public exposure is a real side effect. Confirm exact host, app, port, and desired visibility before changing DNS/nginx/firewall.
