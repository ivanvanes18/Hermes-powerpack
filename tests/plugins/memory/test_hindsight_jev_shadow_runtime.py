import threading
import time
import multiprocessing
import os
import logging

import pytest

from plugins.memory.hindsight.jev_shadow import ShadowPolicy, ShadowTurn
from plugins.memory.hindsight.jev_shadow_runtime import (
    JevShadowRuntime,
    ShadowRuntimeConfig,
    TypeSafeTransport,
    RetainOutcome,
)


def response_payload():
    return {"model": "jev-latest", "answers": {
        "should_retain": {"noul": 0.9},
        "memory_kind": {"choice": "preference", "probabilities": {"preference": 1.0, "decision": 0.0, "constraint": 0.0, "fact": 0.0, "status": 0.0, "correction": 0.0, "none": 0.0}},
        "user_grounded": {"noul": 0.9}, "standalone_meaning": {"noul": 0.9}, "likely_duplicate": {"noul": 0.0},
        "retention_priority": {"choice": "retain_now", "probabilities": {"skip": 0.0, "buffer": 0.0, "retain_now": 1.0}}, "sensitive": {"noul": 0.0},
    }}

class FakeTransport:
    def __init__(self, payload=None, error=None): self.payload, self.error, self.calls = payload, error, 0
    def evaluate(self, request):
        self.calls += 1
        if self.error: raise self.error
        return self.payload


def _leave_reservation(root):
    from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore
    ShadowEventStore(root, "p1").reserve("crashed-turn")


def test_transport_uses_systemone_and_bearer(monkeypatch):
    seen = {}
    class Reply:
        status = 200
        def read(self): return b'{"model":"jev-latest","answers":{}}'
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def urlopen(request, timeout):
        seen.update(url=request.full_url, method=request.method, auth=request.get_header("Authorization"), content=request.get_header("Content-type"), timeout=timeout)
        return Reply()
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    transport = TypeSafeTransport("http://example.test", "secret-key", 1.0)
    transport.evaluate({"model": "jev-latest"})
    assert seen["url"].endswith("/v1/systemone")
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer secret-key"
    assert seen["content"] == "application/json"


def test_runtime_enqueue_is_nonblocking_and_fail_open(tmp_path):
    from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore
    store = ShadowEventStore(tmp_path / "pilot", "p1")
    started = threading.Event(); release = threading.Event()
    class Blocking:
        def evaluate(self, request): started.set(); release.wait(2); return response_payload()
    runtime = JevShadowRuntime(ShadowRuntimeConfig(pilot_id="p1", max_queue=1), store, Blocking())
    assert runtime.enqueue("t1", [ShadowTurn("u", "a")], explicit_memory_request=False)
    assert started.wait(1)
    assert runtime.enqueue("t2", [ShadowTurn("u2", "a2")], explicit_memory_request=False)
    assert not runtime.enqueue("t3", [ShadowTurn("u3", "a3")], explicit_memory_request=False)
    release.set(); runtime.shutdown(2)
    assert store.snapshot().valid_evaluations == 2


def test_runtime_stops_at_one_hundred_and_records_provider_failure(tmp_path):
    from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore
    store = ShadowEventStore(tmp_path / "pilot", "p1")
    transport = FakeTransport(error=TimeoutError())
    runtime = JevShadowRuntime(ShadowRuntimeConfig(pilot_id="p1"), store, transport)
    assert runtime.enqueue("failure", [ShadowTurn("u", "a")], explicit_memory_request=False)
    runtime.shutdown(2)
    assert store.snapshot().valid_evaluations == 0
    assert next(e for e in store.events() if e["kind"] == "evaluation")["verdict"] == "shadow_fail_open"
    transport = FakeTransport(payload=response_payload())
    runtime = JevShadowRuntime(ShadowRuntimeConfig(pilot_id="p2", max_queue=128), ShadowEventStore(tmp_path / "pilot2", "p2"), transport)
    for i in range(100): runtime.enqueue(f"v{i}", [ShadowTurn("u", "a")], explicit_memory_request=False)
    runtime.shutdown(3)
    assert runtime.store.snapshot().valid_evaluations == 100
    assert runtime.enqueue("v100", [ShadowTurn("u", "a")], explicit_memory_request=False) is False


def test_runtime_reconciles_dead_process_claim_without_provider_call(tmp_path):
    import multiprocessing
    from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore

    root = tmp_path / "stale"
    process = multiprocessing.Process(target=_leave_reservation, args=(root,))
    process.start()
    process.join(5)
    assert process.exitcode == 0

    transport = FakeTransport(payload=response_payload())
    runtime = JevShadowRuntime(ShadowRuntimeConfig(pilot_id="p1"), ShadowEventStore(root, "p1"), transport)
    assert transport.calls == 0
    assert runtime.store.snapshot().valid_evaluations == 0
    stale = next(event for event in runtime.store.events() if event.get("turn_id") == "crashed-turn" and event.get("kind") == "evaluation")
    assert stale["error_code"] == "stale_reservation"
    runtime.shutdown()


def test_store_allows_evaluation_and_retain_outcome_for_same_turn(tmp_path):
    from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore
    store = ShadowEventStore(tmp_path / "pilot", "p1")
    assert store.reserve("same")
    store.append({"kind": "evaluation", "pilot_id": "p1", "turn_id": "same",
                  "counts_toward_target": True, "valid": True,
                  "verdict": "shadow_retain", "model": "jev-latest"})
    store.append({"kind": "retain_outcome", "pilot_id": "p1", "turn_id": "same",
                  "counts_toward_target": False, "status": "succeeded",
                  "operation_ids_count": 1})
    assert len(store.events()) == 3  # durable reservation + two event kinds


def test_store_is_exactly_bounded_under_threads_and_processes(tmp_path):
    from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore
    root = tmp_path / "pilot"
    ShadowEventStore(root, "p1")
    def add(i):
        store = ShadowEventStore(root, "p1")
        if store.reserve(f"t{i}"):
            store.append({"kind": "evaluation", "pilot_id": "p1", "turn_id": f"t{i}",
                          "counts_toward_target": True, "valid": True,
                          "verdict": "shadow_retain", "model": "jev-latest"})
    threads = [threading.Thread(target=add, args=(i,)) for i in range(60)]
    for t in threads: t.start()
    for t in threads: t.join()
    procs = [multiprocessing.Process(target=add, args=(60 + i,)) for i in range(60)]
    for p in procs: p.start()
    for p in procs: p.join()
    store = ShadowEventStore(root, "p1")
    evaluations = [e for e in store.events() if e.get("kind") == "evaluation"]
    assert len(evaluations) == 100
    assert store.snapshot().valid_evaluations == 100
    assert store.snapshot().terminal_state == "pending_analysis"


def test_store_rejects_rebound_root_and_digest_tampering(tmp_path):
    from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore
    root = tmp_path / "pilot"
    store = ShadowEventStore(root, "p1")
    store.append({"kind": "evaluation", "pilot_id": "p1", "turn_id": "t1",
                  "counts_toward_target": True, "valid": True,
                  "verdict": "shadow_retain", "model": "jev-latest"})
    state = root / "state.json"
    state.write_text(state.read_text().replace('"event_count":1', '"event_count":99'))
    with pytest.raises(ValueError): ShadowEventStore(root, "p1")
    other = tmp_path / "other"; other.mkdir()
    root.rename(tmp_path / "moved")
    root.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError): ShadowEventStore(root, "p1")


def test_store_parent_substitution_during_root_creation_is_rejected(tmp_path, monkeypatch):
    from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore
    parent = tmp_path / "parent"; parent.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    root = parent / "pilot"
    from pathlib import Path
    original_exists = Path.exists

    def swap_parent(path):
        if path == root:
            parent.rename(tmp_path / "moved")
            parent.symlink_to(outside, target_is_directory=True)
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", swap_parent)
    ShadowEventStore(root, "p1")
    assert not (outside / "pilot").exists()


def test_runtime_exposes_sanitized_closed_persistence_status_and_logs(caplog):
    class BrokenStore:
        pilot_id = "p1"
        def reserve(self, turn_id):
            raise OSError("/secret/path: disk failed")

    runtime = JevShadowRuntime(ShadowRuntimeConfig(pilot_id="p1"), BrokenStore(), FakeTransport())
    with caplog.at_level(logging.WARNING):
        assert runtime.enqueue("t1", [ShadowTurn("u", "a")], explicit_memory_request=False) is False
    assert runtime.status_snapshot() == {"disabled": True, "error_type": "OSError", "error_code": "persistence_error", "report_valid": False}
    assert "/secret" not in caplog.text
    assert "disk failed" not in caplog.text
    assert runtime.enqueue("t2", [ShadowTurn("u", "a")], explicit_memory_request=False) is False
