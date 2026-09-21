---
name: public-endpoint-ops
description: "Expose, verify, diagnose, or remove public HTTP endpoints safely: DNS, nginx/reverse proxy, TLS, ports, health checks, and rollback notes."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [endpoint, nginx, tls, dns, deploy, devops, workshop]
    related_skills: [server-doctor, porkbun-api-dns]
---

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
