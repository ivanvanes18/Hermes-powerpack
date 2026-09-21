---
name: server-doctor
description: "Public-safe VPS/server diagnostic workflow for SSH access, disk/memory/process checks, Docker/nginx/systemd triage, service health, and deployment readiness."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [server, vps, ssh, devops, diagnostics, systemd, nginx, docker, workshop]
    related_skills: [public-endpoint-ops, porkbun-api-dns]
---

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
