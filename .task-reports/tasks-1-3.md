# Tasks 1–3 Evidence Report

Status: DONE_WITH_CONCERNS

## Commits

- `04d486c71` — feat(memory): define Jev shadow admission contract
- `a90ea6ed9` — feat(memory): add private Jev shadow event reporting
- `b862805b3` — feat(memory): run bounded Jev shadow evaluations

## Changed files

- `plugins/memory/hindsight/jev_shadow.py`
- `plugins/memory/hindsight/jev_shadow_runtime.py`
- `plugins/memory/hindsight/jev_shadow_report.py`
- `scripts/jev_hindsight_shadow_report.py`
- `tests/plugins/memory/test_hindsight_jev_shadow.py`
- `tests/plugins/memory/test_hindsight_jev_shadow_runtime.py`

No provider wiring, live configuration, Zoya files, gateway, embeddings, or existing banks were changed.

## RED evidence

Task 1:

```text
PYTHONDONTWRITEBYTECODE=1 /home/ivan/worktrees/hermes-topic-credential-affinity/.venv/bin/python -m pytest -p no:cacheprovider --basetemp=/tmp/jev-shadow-red-task1 tests/plugins/memory/test_hindsight_jev_shadow.py -q
ModuleNotFoundError: No module named 'plugins.memory.hindsight.jev_shadow'
```

Task 2:

```text
PYTHONDONTWRITEBYTECODE=1 /home/ivan/worktrees/hermes-topic-credential-affinity/.venv/bin/python -m pytest -p no:cacheprovider --basetemp=/tmp/jev-shadow-red-task2 tests/plugins/memory/test_hindsight_jev_shadow.py -q -k 'store or report'
ModuleNotFoundError: No module named 'plugins.memory.hindsight.jev_shadow_report'
```

Task 3:

```text
PYTHONDONTWRITEBYTECODE=1 /home/ivan/worktrees/hermes-topic-credential-affinity/.venv/bin/python -m pytest -p no:cacheprovider --basetemp=/tmp/jev-shadow-red-task3 tests/plugins/memory/test_hindsight_jev_shadow_runtime.py -q
ImportError: cannot import name 'JevShadowRuntime' from plugins.memory.hindsight.jev_shadow_runtime
```

## GREEN evidence

- Task 1 focused suite: `10 passed`
- Task 2 focused/full pure contract-store-report suite: `13 passed`
- Task 3 runtime suite: `3 passed`
- Combined Tasks 1–3 suites: `16 passed in 0.79s`
- Compile and whitespace checks: `compileall -q ...` and `git diff --check` both exited `0`
- Report CLI help: exited `0`; exposes required `--root` and optional `--output`

## Implemented behavior

- Bounded three-turn redacted request state and seven typed questions.
- Closed response validation for model identity, answer IDs/types, choices, finite probabilities, ranges, and probability sums.
- Fail-open analytical verdict derivation with explicit-memory/correction/decision/constraint protection.
- Private `0700` pilot root, `0600` event/state/lock files, append/fsync JSONL metadata, duplicate/ordinal/plaintext/secret-like rejection, durable count snapshot, and exact target `100` enforcement.
- Deterministic report aggregation with verdict counts, model list, latency percentiles, usage totals, error counts, and candidate false skips.
- Narrow sanitized report CLI.
- Stdlib `/v1/systemone` transport with bearer auth, absolute deadline, one bounded retry for retryable failures, closed transport error codes, one daemon worker, bounded queue, non-blocking enqueue, fail-open provider events, duplicate suppression, shutdown timeout, and retain-outcome metadata correlation.

## Self-review

- Production changes stayed within the explicitly allowed production files; tests stayed within the two explicitly allowed test files.
- No plaintext conversation or response/request payload is written by the event store; event validation recursively rejects forbidden field names and secret-like values.
- Provider failures are persisted as non-counting `shadow_fail_open` events and do not become semantic skips.
- Existing Hindsight retain behavior was not touched because Task 4 provider wiring is intentionally out of scope.

## Concerns

- The implementation is intentionally limited to Tasks 1–3 and is not wired into `HindsightMemoryProvider`; no live or provider causal proof is claimed.
- The store uses a private lock file but does not yet implement cross-process `fcntl.flock`; Task 4/acceptance hardening should add that before any live pilot.
- The runtime/report surface is focused on the approved tests and should receive the planned full HTTP-server, restart/resume, and 100-turn acceptance tests in later tasks.
- The report CLI currently trusts the event file directly rather than validating the persisted digest/state file; strengthen this before operational use.
