# Tasks 1–3 correction-wave evidence

Status: CORRECTED_FROZEN_CANDIDATE

## Commit

- `22e6c4448dc5bf4eedca8d476d8e94f644c06fb5` — fix(memory): harden Jev shadow Tasks 1-3

## Scope

Changed only the six permitted implementation/test paths and removed tracked `.task-reports/tasks-1-3.md`. No Hindsight `__init__.py`/provider wiring, live config, Zoya, gateway, or restart changes.

## Accepted findings fixed

1. Event identity is `(turn_id, event_kind)`. Durable reservation events prevent duplicate work; evaluation and retain-outcome events for one turn correlate deterministically.
2. POSIX `fcntl.flock` serializes reservation, append, count, state verification, and publication across threads/processes. Descriptor-relative `O_NOFOLLOW` access, fsync, atomic state publication, and exact valid-event capping preserve the <=100 invariant.
3. Root, parents, regular files, modes, inode identity, and descriptor-relative paths are validated. Rebound roots, symlink files, and unsafe modes fail closed.
4. Report validates event kinds/schemas, counts only validated evaluations, reconciles ordinals/counts, joins retain outcomes, and emits retain, fact, fact-type, failure, and eligibility cross-tabs.
5. Startup/report CLI verifies state pilot id, target, valid count, event count, terminal state, modes, and SHA256 over the exact held event bytes. Tampering returns CLI exit 2.
6. Verdict derivation consumes every `ShadowPolicy` threshold; evaluation events persist the exact policy values used.
7. CLI output validates parent/file safety and uses descriptor-relative `O_NOFOLLOW` atomic publication with mode 0600.
8. Persistence `ValueError`/`OSError` is no longer silently swallowed: runtime disables shadow persistence and remains live fail-open.

## Causal RED evidence

Before production fixes, the new focused regressions were run with:

```text
PYTHONDONTWRITEBYTECODE=1 /home/ivan/worktrees/hermes-topic-credential-affinity/.venv/bin/python -m pytest -p no:cacheprovider --basetemp=/tmp/jev-shadow-red-fix tests/plugins/memory/test_hindsight_jev_shadow.py::test_verdict_uses_every_policy_threshold tests/plugins/memory/test_hindsight_jev_shadow.py::test_event_kinds_join_on_turn_id_without_rejecting_retain_outcome tests/plugins/memory/test_hindsight_jev_shadow_runtime.py::test_store_allows_evaluation_and_retain_outcome_for_same_turn tests/plugins/memory/test_hindsight_jev_shadow_runtime.py::test_store_is_exactly_bounded_under_threads_and_processes tests/plugins/memory/test_hindsight_jev_shadow_runtime.py::test_store_rejects_rebound_root_and_digest_tampering -q
34 failed, 1 passed
```

The prior failures were causal: missing policy parameter, duplicate turn-id rejection, non-atomic state publication/races, and missing digest verification.

## Residual closure RED evidence

The four new causal regressions were run before their fixes:

```text
uv run --with pytest python -m pytest -p no:cacheprovider --basetemp=/tmp/jev-shadow-red-closure tests/plugins/memory/test_hindsight_jev_shadow.py::test_report_uses_retain_metadata_and_distinguishes_missing_from_failed tests/plugins/memory/test_hindsight_jev_shadow.py::test_output_publication_never_clobbers_final_file tests/plugins/memory/test_hindsight_jev_shadow_runtime.py::test_store_parent_substitution_during_root_creation_is_rejected tests/plugins/memory/test_hindsight_jev_shadow_runtime.py::test_runtime_exposes_sanitized_closed_persistence_status_and_logs -q
4 failed
```

## Closure fixes

- Replaced pathname parent/root preflight with descriptor-relative component walking using `O_DIRECTORY|O_NOFOLLOW`, `mkdirat`-style creation, and held root descriptors.
- Joined report facts exclusively from retain outcomes; added nonnegative metadata validation and explicit eligible `succeeded`/`failed`/`missing` cross-tabs.
- Replaced output `os.replace` publication with fsynced, descriptor-relative temp creation and hard-link no-replace publication; cleanup only removes this operation's temp inode.
- Added sanitized runtime `status_snapshot()`, redacted warning logging, and invalid-report status on persistence closure while preserving fail-open enqueue behavior.

## GREEN/final evidence

```text
uv run --with pytest python -m pytest -p no:cacheprovider --basetemp=/tmp/jev-shadow-final-closure3 tests/plugins/memory/test_hindsight_jev_shadow.py tests/plugins/memory/test_hindsight_jev_shadow_runtime.py -q
27 passed in 1.14s

python3 -m compileall -q plugins/memory/hindsight scripts/jev_hindsight_shadow_report.py
exit 0

git diff --check
exit 0
```

## Self-review / remaining concerns

- The four residual findings are covered by causal tests and the permitted files only.
- Fresh verification initially reproduced a same-process reservation/worker race as `JSONDecodeError`; a per-root in-process lock was added as the causal repair.
- Repeated provider-failure/concurrency regression: 5 consecutive passes. Final focused suites: 27 passed; compileall and diff checks passed.
- The correction wave remains intentionally limited to Tasks 1–3; provider-level causal proof and live runtime activation are out of scope and were not claimed.
