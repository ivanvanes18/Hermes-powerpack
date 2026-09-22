import threading
import time

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
    assert store.events()[0]["verdict"] == "shadow_fail_open"
    transport = FakeTransport(payload=response_payload())
    runtime = JevShadowRuntime(ShadowRuntimeConfig(pilot_id="p2", max_queue=128), ShadowEventStore(tmp_path / "pilot2", "p2"), transport)
    for i in range(100): runtime.enqueue(f"v{i}", [ShadowTurn("u", "a")], explicit_memory_request=False)
    runtime.shutdown(3)
    assert runtime.store.snapshot().valid_evaluations == 100
    assert runtime.enqueue("v100", [ShadowTurn("u", "a")], explicit_memory_request=False) is False
