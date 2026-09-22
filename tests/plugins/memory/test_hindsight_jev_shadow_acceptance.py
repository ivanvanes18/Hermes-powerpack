import json
import math
import os
import stat
from copy import deepcopy
from pathlib import Path

import pytest

from plugins.memory.hindsight import HindsightMemoryProvider
from plugins.memory.hindsight.jev_shadow import ShadowContractError, ShadowTurn, validate_shadow_response
from plugins.memory.hindsight.jev_shadow_report import build_report
from plugins.memory.hindsight.jev_shadow_runtime import (
    JevShadowRuntime,
    RetainOutcome,
    ShadowEventStore,
    ShadowRuntimeConfig,
    TypeSafeTransport,
)

def response_payload():
    return {"model": "jev-latest", "answers": {
        "should_retain": {"noul": 0.9},
        "memory_kind": {"choice": "preference", "probabilities": {"preference": 1.0, "decision": 0.0, "constraint": 0.0, "fact": 0.0, "status": 0.0, "correction": 0.0, "none": 0.0}},
        "user_grounded": {"noul": 0.9}, "standalone_meaning": {"noul": 0.9}, "likely_duplicate": {"noul": 0.0},
        "retention_priority": {"choice": "retain_now", "probabilities": {"skip": 0.0, "buffer": 0.0, "retain_now": 1.0}}, "sensitive": {"noul": 0.0},
    }}


class CountingTransport:
    def __init__(self, payload=None):
        self.payload = payload or response_payload()
        self.calls = []

    def evaluate(self, request):
        self.calls.append(request)
        return deepcopy(self.payload)


class ProcessCountingTransport:
    def __init__(self, counter):
        self.counter = counter

    def evaluate(self, request):
        with self.counter.get_lock():
            self.counter.value += 1
        return response_payload()


def _run_process_runtime(root, counter, offset):
    runtime = JevShadowRuntime(
        ShadowRuntimeConfig(pilot_id="reina", max_queue=128),
        ShadowEventStore(root, "reina"),
        ProcessCountingTransport(counter),
    )
    for index in range(60):
        runtime.enqueue(f"process-{offset + index}", [ShadowTurn("u", "a")], explicit_memory_request=False)
    runtime.shutdown(5)


class FakeRetainResponse:
    operation_ids = ("op-1",)


def _runtime(root, transport, pilot_id="reina"):
    store = ShadowEventStore(root, pilot_id)
    return JevShadowRuntime(ShadowRuntimeConfig(pilot_id=pilot_id, max_queue=128), store, transport)


def _provider(runtime, retain_calls):
    provider = HindsightMemoryProvider()
    provider._jev_shadow_runtime = runtime
    provider._auto_retain = True
    provider._retain_every_n_turns = 1
    provider._retain_async = True
    provider._session_id = "session-opaque"
    provider._document_id = "document-opaque"
    provider._bank_id = "hermes"
    provider._retain_context = "conversation"
    provider._observation_scopes = []
    provider._shutting_down.clear()
    provider._resolve_retain_target = lambda fallback: (fallback, None)

    def run(operation):
        retain_calls.append(True)
        return FakeRetainResponse()

    provider._run_hindsight_operation = run
    return provider


def _wait_and_shutdown(provider):
    provider.shutdown()


def test_adversarial_egress_never_reaches_typesafe_but_russian_decision_does(tmp_path):
    excluded = [
        "api key=sk-test-secret-123456",
        "Authorization: Bearer abcdefghijkl",
        "-----BEGIN RSA PRIVATE KEY-----\\nsecret\\n-----END RSA PRIVATE KEY-----",
        "https://user:password@example.test/private",
        "Telegram chat id 123456789 and session id sess-123",
        "<memory-context>old recalled preference</memory-context>",
        "tool output: command completed with token jv_live_secret-123456",
        "договор и акт № 4",
    ]
    for index, text in enumerate(excluded):
        transport = CountingTransport()
        runtime = _runtime(tmp_path / f"excluded-{index}", transport)
        retain_calls = []
        provider = _provider(runtime, retain_calls)
        provider.sync_turn(text, "ack")
        _wait_and_shutdown(provider)
        assert transport.calls == []
        assert len(retain_calls) == 1
        event = next(e for e in runtime.store.events() if e["kind"] == "evaluation")
        assert event["verdict"] == "shadow_fail_open"
        assert event["counts_toward_target"] is False

    transport = CountingTransport()
    runtime = _runtime(tmp_path / "ordinary", transport)
    retain_calls = []
    provider = _provider(runtime, retain_calls)
    provider.sync_turn("Я решил перейти на еженедельный план проекта.", "Понял.")
    _wait_and_shutdown(provider)
    assert len(transport.calls) == 1
    assert len(retain_calls) == 1
    assert runtime.store.snapshot().valid_evaluations == 1


@pytest.mark.parametrize("mutator", [
    lambda payload: payload.update(model="other-model"),
    lambda payload: payload["answers"]["should_retain"].update(noul=True),
    lambda payload: payload["answers"]["should_retain"].update(noul=10**10000),
    lambda payload: payload["answers"]["memory_kind"]["probabilities"].update({1: 0.0}),
    lambda payload: payload["answers"]["memory_kind"]["probabilities"].update({"preference": math.inf}),
    lambda payload: payload["answers"]["memory_kind"]["probabilities"].update({"preference": 0.5}),
    lambda payload: payload["answers"].update(unknown={"noul": 0.1}),
])
def test_malformed_provider_answers_are_fail_open_and_retain_still_runs(tmp_path, mutator):
    payload = response_payload()
    mutator(payload)
    transport = CountingTransport(payload)
    runtime = _runtime(tmp_path / "malformed", transport)
    retain_calls = []
    provider = _provider(runtime, retain_calls)
    provider.sync_turn("Я принял решение по проекту.", "Понял.")
    _wait_and_shutdown(provider)
    assert len(retain_calls) == 1
    event = next(e for e in runtime.store.events() if e["kind"] == "evaluation")
    assert event["verdict"] == "shadow_fail_open"
    assert event["counts_toward_target"] is False
    assert event["error_code"] != ""
    assert "shadow_skip" not in json.dumps(event)


def test_duplicate_json_response_is_rejected_without_semantic_skip(tmp_path, monkeypatch):
    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"model":"jev-latest","model":"jev-latest","answers":{}}'

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Reply())
    transport = TypeSafeTransport("http://typesafe.invalid", "key", 1.0)
    runtime = _runtime(tmp_path / "duplicate", transport)
    retain_calls = []
    provider = _provider(runtime, retain_calls)
    provider.sync_turn("Я утвердил решение.", "Понял.")
    _wait_and_shutdown(provider)
    assert retain_calls == [True]
    event = next(e for e in runtime.store.events() if e["kind"] == "evaluation")
    assert event["verdict"] == "shadow_fail_open"
    assert event["counts_toward_target"] is False
    assert event["error_code"] == "duplicate_key"


def test_html_provider_error_is_fail_open_and_retains(tmp_path, monkeypatch):
    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b"<html>upstream failure</html>"

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Reply())
    transport = TypeSafeTransport("http://typesafe.invalid", "key", 1.0)
    runtime = _runtime(tmp_path / "html", transport)
    retain_calls = []
    provider = _provider(runtime, retain_calls)
    provider.sync_turn("Я принял решение.", "Понял.")
    _wait_and_shutdown(provider)
    assert retain_calls == [True]
    event = next(e for e in runtime.store.events() if e["kind"] == "evaluation")
    assert event["verdict"] == "shadow_fail_open"
    assert event["error_code"] == "invalid_json"


def test_exact_100_evaluations_101_retains_stop_and_reconcile(tmp_path):
    transport = CountingTransport()
    runtime = _runtime(tmp_path / "exact-stop", transport)
    retain_calls = []
    provider = _provider(runtime, retain_calls)
    for index in range(101):
        marker = f"PLAINTEXT-TURN-MARKER-{index}"
        provider.sync_turn(f"Я принял решение {index}: {marker}.", "Понял.")
    _wait_and_shutdown(provider)

    assert len(transport.calls) == 100
    assert len(retain_calls) == 101
    assert runtime.store.snapshot().terminal_state == "pending_analysis"
    events = runtime.store.events()
    report = build_report(events)
    assert report["valid_evaluations"] == 100
    assert report["status"] == "pending_analysis"
    assert report["retain_outcomes"] == {"failed": 0, "missing": 0, "queued": 0, "succeeded": 101}
    assert all(f"PLAINTEXT-TURN-MARKER-{i}" not in json.dumps(events) for i in range(101))


def test_multi_process_runtime_admission_is_globally_bounded(tmp_path):
    import multiprocessing

    root = tmp_path / "multi-process"
    counter = multiprocessing.Value("i", 0)
    processes = [
        multiprocessing.Process(target=_run_process_runtime, args=(root, counter, offset))
        for offset in (0, 60)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0

    store = ShadowEventStore(root, "reina")
    assert counter.value == 100
    assert store.snapshot().valid_evaluations == 100
    assert store.snapshot().terminal_state == "pending_analysis"
    assert len([event for event in store.events() if event["kind"] == "reservation"]) == 100


def test_restart_resume_60_plus_40_duplicate_is_idempotent(tmp_path):
    root = tmp_path / "resume"
    transport1 = CountingTransport()
    runtime1 = _runtime(root, transport1)
    retain_calls1 = []
    provider1 = _provider(runtime1, retain_calls1)
    for index in range(60):
        provider1.sync_turn(f"решение {index}", "принято")
    provider1.shutdown()

    transport2 = CountingTransport()
    runtime2 = _runtime(root, transport2)
    retain_calls2 = []
    provider2 = _provider(runtime2, retain_calls2)
    for index in range(60, 100):
        provider2.sync_turn(f"решение {index}", "принято")
    provider2.shutdown()

    assert len(transport1.calls) == 60
    assert len(transport2.calls) == 40
    before = len(runtime2.store.events())
    duplicate_id = next(e["turn_id"] for e in runtime2.store.events() if e["kind"] == "evaluation")
    assert runtime2.enqueue(duplicate_id, [ShadowTurn("duplicate", "duplicate")], explicit_memory_request=False) is False
    runtime2.shutdown()
    assert len(runtime2.store.events()) == before
    assert runtime2.store.snapshot().valid_evaluations == 100
    assert runtime2.store.snapshot().terminal_state == "pending_analysis"
    assert len(transport2.calls) == 40
    report = build_report(runtime2.store.events())
    assert report["valid_evaluations"] == 100
    assert report["status"] == "pending_analysis"


def test_artifact_files_remain_private(tmp_path):
    store = ShadowEventStore(tmp_path / "private", "reina")
    store.append({"kind": "evaluation", "pilot_id": "reina", "turn_id": "opaque", "valid": False, "counts_toward_target": False, "verdict": "shadow_fail_open", "model": None})
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    for path in (store.events_path, store.state_path, store.lock_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.is_file()
        assert "TYPESAFE_API_KEY" not in path.read_text()
