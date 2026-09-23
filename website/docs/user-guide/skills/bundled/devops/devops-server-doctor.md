---
title: "Server Doctor — Diagnose VPS and server health safely"
sidebar_label: "Server Doctor"
description: "Diagnose VPS and server health safely"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Server Doctor

Diagnose VPS and server health safely.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/devops/server-doctor` |
| Version | `1.0.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `server`, `vps`, `ssh`, `devops`, `diagnostics`, `systemd`, `nginx`, `docker`, `workshop` |
| Related skills | [`public-endpoint-ops`](../../bundled/devops/devops-public-endpoint-ops.md), [`porkbun-api-dns`](../../bundled/infrastructure/infrastructure-porkbun-api-dns.md) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Server Doctor

Use this for server diagnostics and safe operational checks.

## Safety rules

- Read-only first: identify host, user, services, ports, disk, memory, logs.
- Never print secrets from `.env`, process env, config files, shell history, or logs.
- Do not restart, delete, reset, upgrade, or change firewall/DNS without explicit approval.
- Separate evidence from guesses.

## First-pass checklist

```bash
hostname && uname -a
whoami && pwd
df -h
free -h
ss -ltnp
systemctl --failed || true
```

For app servers, run non-mutating checks first. Use elevated privileges only outside this skill after the operator explicitly approves the target and action.

```bash
systemctl status <service> --no-pager || true
journalctl -u <service> -n 120 --no-pager || true
docker ps --format 'table {{.Names}}	{{.Status}}	{{.Ports}}' || true
nginx -t || true
```

## Output contract

```text
Target:
Evidence:
Health:
Likely cause:
Safe next action:
Approval needed for:
```

## Done criteria

The user knows whether the server is healthy, degraded, or blocked, and the next mutation is explicitly scoped before it happens.
