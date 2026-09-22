# Task 4 — Jev shadow Hindsight provider wiring

Status: implemented and verified.

Changed only the approved Task 4 files:
- `plugins/memory/hindsight/__init__.py`
- `tests/plugins/memory/test_hindsight_provider.py`
- `tests/plugins/memory/test_hindsight_config_schema.py`

Implementation:
- Added disabled-by-default Jev shadow configuration defaults.
- Initializes Jev only when enabled, identity is default/Reina, `TYPESAFE_API_KEY` is available, the private event root is accepted, and fewer than 100 valid evaluations exist.
- Maintains a salted opaque turn ID and bounded three-turn shadow window.
- Enqueues shadow work in a broad nonfatal boundary before the unchanged Hindsight retain cadence/path.
- Added terminal-only retain correlation: Hindsight writes exactly one Jev retain outcome per dispatched turn, from `_do_retain`, with `succeeded` or `failed` status.
- Shadow enqueue and retain-outcome recording remain nonfatal; Hindsight retain dispatch and `aretain_batch` behavior are unchanged.
- Added a causal provider regression covering succeeded metadata, failed metadata, absence of `queued`, unchanged `aretain_batch` calls, and the existing shadow-error nonfatal path.
- Reset clears only in-memory shadow window and salt; shutdown drains Jev with a bounded timeout before client close.
- Added causal tests for shadow false/exception and success paths, plus configuration defaults.

Verification:
- Causal RED: the new provider test failed before the fix because it observed `queued` then `succeeded` for one turn.
- Focused provider/config plus Tasks 1–3 suites: 114 passed. The checkout `.venv` is the gateway interpreter but lacks pytest and Hindsight dependencies, so the run used `uv run --with pytest --with hindsight-client==0.6.1 --python .venv/bin/python` without changing the worktree environment.
- `compileall` passed for Hindsight and the changed tests.
- `git diff --check` passed.

Concerns:
- No live config or restart was performed. The accepted Tasks 1–3 runtime/report modules were not modified.
