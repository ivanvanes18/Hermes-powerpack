# Task 4 — Jev shadow Hindsight provider wiring

Status: implemented and verified.

Changed only the requested Task 4 implementation/test paths plus this private report:
- `plugins/memory/hindsight/__init__.py`
- `tests/plugins/memory/test_hindsight_provider.py`
- `.task-reports/task-4.md`

Implementation:
- After `aretain_batch` returns successfully, operation tracking and operation-id counting are auxiliary, nonfatal steps.
- Malformed operation metadata is logged at debug level and uses `operation_ids_count=0`.
- The accepted retain still records exactly one terminal `succeeded` Jev outcome; retain exceptions still record the existing terminal `failed` outcome and re-raise to the writer boundary.
- Existing Hindsight retain dispatch, payload, async tracking semantics, and logging remain unchanged except that malformed auxiliary metadata cannot fail the writer job.

Regression:
- Added a causal provider test with a successful `aretain_batch` response whose `operation_ids` metadata is malformed.
- Asserts `aretain_batch` is awaited once, exactly one `succeeded` outcome is recorded with count zero, no failure outcome is recorded, and the writer remains alive.

Verification:
- Causal RED: the new test failed before the fix because `_track_retain_ops` raised `TypeError` and no Jev outcome was recorded.
- GREEN/final provider, config, and Tasks 1–3 suites: `115 passed in 5.72s`.
- `python3 -m compileall -q plugins/memory/hindsight tests/plugins/memory/test_hindsight_provider.py`: passed.
- `git diff --check`: passed.

Concerns:
- No live config or restart was performed.
- The checkout `.venv` lacks pytest and Hindsight dependencies; verification used an ephemeral `uv run` environment without changing the worktree environment.
