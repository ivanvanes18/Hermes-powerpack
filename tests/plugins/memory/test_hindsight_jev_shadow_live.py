"""Explicit opt-in E2E for the production Hindsight/TypeSafe Jev wiring.

The test is intentionally skipped during normal suites. It uses the real
TypeSafeTransport and credential resolver, while the Hindsight retain call is
injected so no Hindsight bank is touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent.secret_scope import get_secret
from plugins.memory import hindsight as hindsight_module
from plugins.memory.hindsight import HindsightMemoryProvider
from plugins.memory.hindsight.jev_shadow_runtime import TypeSafeTransport


_RUN_LIVE = os.environ.get("HERMES_RUN_LIVE_JEV_E2E") == "1"
# Capture only the credential value needed by the explicitly opted-in test
# before the repository's hermetic pytest fixture removes credential env vars.
_LIVE_TYPESAFE_API_KEY = get_secret("TYPESAFE_API_KEY", "")
pytestmark = pytest.mark.skipif(
    not _RUN_LIVE,
    reason="set HERMES_RUN_LIVE_JEV_E2E=1 to run the live TypeSafe Jev E2E",
)


class _FakeRetainResponse:
    operation_ids = ("shadow-e2e-operation",)


class _FakeHindsightClient:
    def __init__(self, retained: list[dict]):
        self.retained = retained

    def aretain_batch(self, **kwargs):
        self.retained.append(kwargs)
        return _FakeRetainResponse()


def _config(root: Path) -> dict:
    return {
        "mode": "cloud",
        "api_key": "unused-hindsight-key",
        "bank_id": "hermes",
        "auto_retain": True,
        "retain_async": True,
        "retain_every_n_turns": 1,
        "jev_shadow_enabled": True,
        "jev_shadow_root": str(root),
        "jev_shadow_model": "jev-latest",
        "jev_shadow_timeout": 20.0,
        "jev_shadow_max_queue": 4,
        "typesafe_endpoint": "https://api.typesafe.ai",
    }


def _provider(monkeypatch: pytest.MonkeyPatch, root: Path) -> tuple[HindsightMemoryProvider, list[dict]]:
    monkeypatch.setattr(hindsight_module, "_load_config", lambda: _config(root))
    provider = HindsightMemoryProvider()
    retained: list[dict] = []

    # Keep the production provider's retain queue and shadow enqueue path, but
    # replace only the external Hindsight operation with an in-process backend.
    provider._client = _FakeHindsightClient(retained)
    provider._run_hindsight_operation = lambda operation: operation(provider._client)
    provider._resolve_retain_target = lambda fallback_document_id: (fallback_document_id, None)
    provider.initialize(
        "shadow-e2e-session",
        hermes_home=str(root.parent / "hermes-home"),
        agent_identity="Reina",
        platform="test",
        user_id="opaque-user",
        chat_id="opaque-chat",
    )
    return provider, retained


def _terminal_events(provider: HindsightMemoryProvider) -> list[dict]:
    runtime = provider._jev_shadow_runtime
    assert runtime is not None
    provider.shutdown()
    events = runtime.store.events()
    return [event for event in events if event.get("kind") == "evaluation"]


def test_live_provider_jev_success_and_fail_open_without_live_memory_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api_key = _LIVE_TYPESAFE_API_KEY
    if not api_key:
        pytest.skip("TYPESAFE_API_KEY is unavailable through get_secret")
    monkeypatch.setenv("TYPESAFE_API_KEY", api_key)

    success, success_retains = _provider(monkeypatch, tmp_path / "success")
    success.sync_turn(
        "Я предпочитаю еженедельный план проекта и хочу, чтобы это учитывалось.",
        "Понял, учту это предпочтение.",
    )
    success_events = _terminal_events(success)
    assert len(success_events) == 1
    success_event = success_events[0]
    assert success_event["valid"] is True
    assert success_event["counts_toward_target"] is True
    assert success_event["model"].startswith("jev-")
    assert success_event["model"] != "jev-latest"
    assert len(success_retains) == 1

    # The private event store is metadata-only: neither synthetic user text nor
    # retain payload fields may cross into its JSONL artifact.
    success_runtime = success._jev_shadow_runtime
    assert success_runtime is not None
    serialized = json.dumps(success_runtime.store.events(), ensure_ascii=False)
    assert "еженедельный план проекта" not in serialized
    assert not any(key in serialized for key in ("user_text", "assistant_text", "content", "prompt"))
    assert set(success_event) <= {
        "kind", "pilot_id", "turn_id", "valid", "counts_toward_target",
        "verdict", "model", "policy", "latency_ms", "usage", "error_code",
        "fact_count", "fact_types", "ordinal",
    }

    failed, failed_retains = _provider(monkeypatch, tmp_path / "fail-open")
    failed_runtime = failed._jev_shadow_runtime
    assert failed_runtime is not None
    failed_runtime.transport = TypeSafeTransport("http://127.0.0.1:1", api_key, 0.2)
    failed.sync_turn(
        "Я предпочитаю ежемесячный обзор проекта.",
        "Понял.",
    )
    failed_events = _terminal_events(failed)
    assert len(failed_events) == 1
    failed_event = failed_events[0]
    assert failed_event["valid"] is False
    assert failed_event["counts_toward_target"] is False
    assert failed_event["verdict"] == "shadow_fail_open"
    assert failed_event["error_code"] in {"transport_error", "timeout"}
    assert len(failed_retains) == 1

    failed_serialized = json.dumps(failed_runtime.store.events(), ensure_ascii=False)
    assert "ежемесячный обзор проекта" not in failed_serialized
    assert not failed_runtime._thread or not failed_runtime._thread.is_alive()
    assert not success_runtime._thread or not success_runtime._thread.is_alive()
