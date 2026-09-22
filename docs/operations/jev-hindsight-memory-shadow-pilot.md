# Jev/Hindsight memory-shadow pilot runbook

## Status and scope

This runbook is for the **Reina-only, observational Jev shadow pilot**. It is an
operator procedure, not activation approval. The source candidate is ready for a
separate owner decision; the commands below do not apply configuration or restart
the gateway unless an operator deliberately runs the activation steps after that
approval.

The shadow observer is fail-open and must not change the live Hindsight retain
path. It evaluates bounded, redacted turn state asynchronously and records only
sanitized metadata. Hindsight retains continue to be the source of live memory.
The runtime accepts the empty agent identity and `reina` internally, but the
production activation scope is explicitly **Reina only**; do not enable it for
any other agent.

The pilot has a fixed target of exactly 100 valid Jev evaluations. Once the
shared durable store reaches 100 valid evaluations, admission stops. The store
and report then show `pending_analysis`; that is the stopping point for the
pilot, not permission to start another batch.

### Explicit exclusions

This pilot does **not** include Zoya, embeddings, reranking, recall changes,
active memory gating, MiniMax/provider changes, bank-id changes, retain cadence
changes, or any change to Hindsight's live retain decision. It does not publish
conversation text, recalled memory, tool output, document contents, Telegram or
session identifiers, API keys, or response bodies into the artifacts. Do not
broaden the scope while operating this runbook.

## Configuration contract

The Hindsight provider reads these fields from its provider configuration in the
active profile's `/home/ivan/.hermes/hindsight/config.json`:

| Field | Pilot value | Meaning |
| --- | ---: | --- |
| `jev_shadow_enabled` | `false` by default; `true` for approved activation | Master switch. Disabled by default. |
| `jev_shadow_model` | `"jev-latest"` | Exact accepted Jev model identity. |
| `jev_shadow_target` | `100` | Fixed target; do not change. |
| `jev_shadow_timeout` | `10.0` | Per-request timeout in seconds. |
| `jev_shadow_max_queue` | `32` | Maximum queued asynchronous evaluations. |
| `jev_shadow_root` | `""` by default; approved path below | Private artifact root. Empty resolves to `$HERMES_HOME/hindsight/jev-shadow`. |

The TypeSafe credential is read from the existing secret store entry
`TYPESAFE_API_KEY`; never put it in `config.json`, a command argument, a report,
or a log. The endpoint is the existing `typesafe_endpoint` setting when
present, otherwise `TYPESAFE_API_URL`, otherwise the code default
`https://api.typesafe.dev`.

### Prepared configuration diff — **NOT APPLIED**

This is the exact proposed addition for the Reina Hindsight provider config.
It is documentation only. Do not paste or apply it without the separate
activation approval and the pre-activation gates.

```diff
--- /home/ivan/.hermes/hindsight/config.json
+++ /home/ivan/.hermes/hindsight/config.json (prepared; NOT APPLIED)
@@
+  "jev_shadow_enabled": true,
+  "jev_shadow_model": "jev-latest",
+  "jev_shadow_target": 100,
+  "jev_shadow_timeout": 10.0,
+  "jev_shadow_max_queue": 32,
+  "jev_shadow_root": "/home/ivan/.hermes/hindsight/jev-shadow"
```

The diff is additive JSON content: preserve the existing surrounding object and
commas. Do not change MiniMax, embeddings, recall, bank ID, retain cadence, or
any Zoya setting.

## Private artifacts and permissions

With the prepared path, the artifact root is:

```text
/home/ivan/.hermes/hindsight/jev-shadow/
```

The current implementation uses this directory directly (there is no additional
pilot-id directory). It writes:

- `state.json` — authenticated/reconciled counters and terminal state;
- `events.jsonl` — sanitized reservation, evaluation, and retain-outcome metadata;
- `.lock` — the cross-process admission lock.

The directory must be mode `0700`; each file must be a regular file with mode
`0600`. The store verifies the event-byte SHA-256 and counters on startup. Do
not copy these files to a shared directory, commit them, upload them, or expose
them in tickets. A failed integrity or permission check disables the shadow
observer without blocking Hindsight retain.

## Pre-activation checks (read-only)

Run from the accepted checkout. These commands do not read or print the
TypeSafe secret and do not modify the live profile:

```bash
cd /home/ivan/worktrees/hermes-jev-memory-shadow

git status --short --branch
git show --stat --oneline HEAD

# Gateway and memory-provider health/status; output is diagnostic only.
hermes gateway status --deep
hermes memory status
hermes logs gateway --level WARNING -n 100
```

`hermes memory status` and logs must be reviewed for secret redaction. If the
installed Hermes command is not the accepted checkout, use the same profile-bound
operator command that owns the gateway; do not switch `HERMES_HOME` on a live
service as a workaround.

After approved activation, inspect only safe artifact metadata:

```bash
ROOT=/home/ivan/.hermes/hindsight/jev-shadow
stat -c '%A %a %n' "$ROOT" "$ROOT/state.json" "$ROOT/events.jsonl" "$ROOT/.lock"
jq '{pilot_id,target,valid_evaluations,event_count,terminal_state}' "$ROOT/state.json"
```

The `jq` projection deliberately excludes event payloads and credentials. A
healthy pre-completion state is `terminal_state: "collecting"`; the completed
pilot must be `pending_analysis` with `valid_evaluations: 100`.

## Report command

After the store reaches `pending_analysis`, generate the deterministic report
from the accepted checkout. Printing to stdout is safe because the report is
metadata-only; writing a report file requires a private destination with mode
`0600`:

```bash
cd /home/ivan/worktrees/hermes-jev-memory-shadow
ROOT=/home/ivan/.hermes/hindsight/jev-shadow
.venv/bin/python scripts/jev_hindsight_shadow_report.py --root "$ROOT"
```

To publish a private local report without exposing it in a shell argument or
logs, create the destination first with restrictive permissions and pass only
that path:

```bash
REPORT="$ROOT/report.json"
umask 077
.venv/bin/python scripts/jev_hindsight_shadow_report.py \
  --root "$ROOT" --output "$REPORT"
stat -c '%A %a %n' "$REPORT"
jq '{status,target,valid_evaluations,event_count,verdict_counts,models,latency_ms,usage,error_counts,retain_outcomes,fact_count,fact_type_counts,eligibility_cross_tab}' "$REPORT"
```

The report must say `status: "pending_analysis"`, `target: 100`, and
`valid_evaluations: 100`. Treat any exit code other than zero, a state-integrity
error, a non-`pending_analysis` result, or a count above 100 as a failed
verification; do not analyze or retry by changing the target.

## One-switch rollback / disable

Rollback is deliberately one switch and does not touch Hindsight/MiniMax,
embeddings, recall, banks, retain cadence, or Zoya. After approval to roll back,
edit only this provider field in the active profile's
`/home/ivan/.hermes/hindsight/config.json`:

```json
"jev_shadow_enabled": false
```

Preserve the artifact directory for investigation. A new provider/gateway
lifecycle is required for configuration changes to take effect. Use the
owner-approved gateway lifecycle command; for the managed service this is:

```bash
hermes gateway restart
hermes gateway status --deep
hermes logs gateway --level WARNING -n 100
```

Do not delete the artifact directory as part of rollback. Do not run
`hermes memory off`, change the active memory provider, alter the Hindsight
configuration wholesale, or restart Zoya. If the gateway is already unhealthy,
stop at the switch and use the normal gateway incident procedure rather than
making additional memory changes.

## Completion and handoff

The pilot is complete only when the report is generated from the verified store
and reaches `pending_analysis` at exactly 100 valid evaluations. Handoff must
include the accepted source commit, focused/acceptance verification receipts,
the private report path, the exact applied config diff (if activation was
approved), and the disable switch. A source commit or a prepared diff alone is
not live activation and must not be reported as such.
