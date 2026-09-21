---
name: porkbun-api-dns
description: "Manage DNS records through Porkbun API: read records, add/edit/delete A/AAAA/CNAME/TXT, and verify authoritative/public resolver propagation."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [dns, porkbun, domains, infrastructure, workshop]
    related_skills: [server-doctor, public-endpoint-ops]
---

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
