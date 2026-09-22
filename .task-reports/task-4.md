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
- Added queued/succeeded/failed retain correlation hooks whose failures cannot affect retain execution.
- Reset clears only in-memory shadow window and salt; shutdown drains Jev with a bounded timeout before client close.
- Added causal tests for shadow false/exception and success paths, plus configuration defaults.

Verification:
- Focused provider/config plus Tasks 1–3 suites: 113 passed, 60 existing multiprocessing deprecation warnings.
- `compileall` passed for all changed Python files.
- `git diff --check` passed.

Concerns:
- The existing Task 3 runtime suppresses duplicate retain-outcome events for one turn ID; provider hooks still issue queued and terminal correlation calls, but runtime persistence may retain only its first correlation event until that accepted runtime behavior is revised in its owning task.
