# Task 5 — adversarial acceptance

Baseline: `7e2ba9076`.

## TDD evidence

- RED: acceptance suite initially failed on excluded egress, 101 Jev calls, 99/39 restart counts, orphan retain report reconciliation, and malformed-provider paths.
- GREEN: focused acceptance/owning shadow suites pass after the smallest direct fixes.

## Changes

- Created `tests/plugins/memory/test_hindsight_jev_shadow_acceptance.py`.
- Extended direct shadow egress blocking for memory/tool context, Telegram/session identifiers, API-key assignments, and credentialized URLs.
- Made Jev target admission globally durable: reservations now atomically count outstanding claims under the existing `fcntl` store lock, include the owning PID, and are terminally consumed by evaluation events. Dead-process claims reconcile to fail-open terminal events without a provider retry; queue-full and target-reached paths also close claims.
- Added multi-runtime/process acceptance proving one shared store permits no more than 100 provider calls and reaches `pending_analysis`.
- Replaced the duplicate-key fake transport with raw duplicate-key JSON through `TypeSafeTransport`, asserting `duplicate_key` fail-open behavior while Hindsight retain continues.
- Normalized absent Hindsight retain fact metadata to `0` for strict report validation.

## Verification

- `.venv/bin/python -m pytest tests/plugins/memory/test_hindsight_jev_shadow_acceptance.py tests/plugins/memory/test_hindsight_jev_shadow.py tests/plugins/memory/test_hindsight_jev_shadow_runtime.py -q` — exit 0, 43 passed.
- `.venv/bin/python -m compileall -q plugins/memory/hindsight scripts/jev_hindsight_shadow_report.py tests/plugins/memory/test_hindsight_jev_shadow.py tests/plugins/memory/test_hindsight_jev_shadow_runtime.py tests/plugins/memory/test_hindsight_jev_shadow_acceptance.py` — exit 0.
- `git diff --check` — exit 0.
- Acceptance proves excluded fixtures do not invoke fake TypeSafe, Russian decision does, malformed/HTML/duplicate responses fail open while retain runs, exact 100 Jev calls with 101 retain calls and `pending_analysis`, report reconciliation, 60+40 restart/resume, duplicate idempotency, private modes, and no plaintext markers.

## Owning-suite note

The requested combined command reached 118 passed but had 8 unrelated environment/dependency failures in pre-existing Hindsight provider tests because this worktree `.venv` lacks the Hindsight client dependencies and lazy installs are disabled. The focused Task 5 and shadow runtime suites are green; no live config, restart, Zoya, or embeddings were touched.
