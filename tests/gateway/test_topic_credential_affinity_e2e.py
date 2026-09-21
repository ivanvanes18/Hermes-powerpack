"""End-to-end topic credential affinity: config.yaml → gateway → pool → runtime.

The unit tests either stub the pool or stub runtime resolution. This one runs
the whole chain against a temporary ``HERMES_HOME`` with a real ``auth.json``
credential pool (placeholder tokens, no network): two Telegram topics declare
different ``credential_id``s and must each be served their own row, with a 429
on one topic staying local to that topic's pool.
"""

from __future__ import annotations

import json
import threading

import pytest
import yaml

from gateway.config import ChannelOverride, GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.session import SessionSource

PROVIDER = "deepseek"
MODEL = "deepseek-chat"
ROWS = {
    "cred-alpha": "sk-fake-placeholder-alpha",
    "cred-beta": "sk-fake-placeholder-beta",
    "cred-gamma": "sk-fake-placeholder-gamma",
}
CHAT_ID = "-1001234567890"
TOPIC_ALPHA, TOPIC_BETA, TOPIC_PLAIN = "11", "22", "33"


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": MODEL, "provider": PROVIDER}}), encoding="utf-8")
    (home / "auth.json").write_text(json.dumps({
        "version": 1,
        "providers": {},
        "credential_pool": {PROVIDER: [
            {"id": row_id, "label": row_id, "auth_type": "api_key", "priority": idx,
             "source": "manual", "access_token": token}
            for idx, (row_id, token) in enumerate(ROWS.items())
        ]},
    }), encoding="utf-8")
    return home


def _runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(
        enabled=True, token="fake-bot-token",
        channel_overrides={
            TOPIC_ALPHA: ChannelOverride(credential_id="cred-alpha"),
            TOPIC_BETA: ChannelOverride(credential_id="cred-beta"),
            TOPIC_PLAIN: ChannelOverride(system_prompt="No credential pin here."),
        },
    )})
    runner.adapters = {}
    runner._session_model_overrides = {}
    runner._last_resolved_model = {}
    runner._service_tier = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    return runner


def _source(thread_id: str) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM, chat_id=CHAT_ID, chat_type="thread",
        user_id="u1", thread_id=thread_id,
    )


def _resolve(runner: GatewayRunner, thread_id: str):
    return runner._resolve_session_agent_runtime(
        source=_source(thread_id), user_config={"model": {"default": MODEL, "provider": PROVIDER}})


def test_each_topic_resolves_its_own_pooled_credential(hermes_home):
    runner = _runner()

    model_alpha, runtime_alpha = _resolve(runner, TOPIC_ALPHA)
    _model_beta, runtime_beta = _resolve(runner, TOPIC_BETA)
    _model_plain, runtime_plain = _resolve(runner, TOPIC_PLAIN)

    assert model_alpha == MODEL
    assert runtime_alpha["api_key"] == ROWS["cred-alpha"]
    assert runtime_beta["api_key"] == ROWS["cred-beta"]
    # No pin: the pool's normal strategy (fill_first) serves the first row.
    assert runtime_plain["api_key"] == ROWS["cred-alpha"]

    signature = GatewayRunner._agent_config_signature
    assert signature(MODEL, runtime_alpha, [], "") != signature(MODEL, runtime_beta, [], "")


def test_pinned_row_is_not_globally_reordered_by_another_topic(hermes_home):
    """Beta's pin must not promote its row for the unpinned topic."""
    runner = _runner()
    _resolve(runner, TOPIC_BETA)
    _model, runtime_plain = _resolve(runner, TOPIC_PLAIN)
    assert runtime_plain["api_key"] == ROWS["cred-alpha"]


def test_429_rotation_is_local_in_memory_but_exhaustion_is_account_wide(hermes_home):
    """Exactly what "stays local to the topic" does and does not mean.

    LOCAL: the pool object and cursor a sibling topic is already holding are not
    rewritten, so its live turn keeps the credential it selected.

    NOT LOCAL: the failed row's exhaustion is persisted to the shared auth
    store, so every pool loaded afterwards — any topic, any process — sees that
    row benched. A rate limit belongs to the credential, not to the topic.
    """
    import json as _json

    runner = _runner()
    _model_alpha, runtime_alpha = _resolve(runner, TOPIC_ALPHA)
    _model_beta, runtime_beta = _resolve(runner, TOPIC_BETA)

    pool_alpha, pool_beta = runtime_alpha["credential_pool"], runtime_beta["credential_pool"]
    assert pool_alpha is not pool_beta, "each topic resolution holds its own pool object"
    assert pool_beta.current().id == "cred-beta"

    rotated = pool_alpha.mark_exhausted_and_rotate(
        status_code=429, credential_id="cred-alpha", api_key_hint=ROWS["cred-alpha"])
    assert rotated is not None and rotated.id != "cred-alpha"

    # LOCAL half — beta's already-loaded pool object and cursor are untouched.
    assert {row.id: row.last_status for row in pool_beta.entries()}["cred-beta"] is None
    assert pool_beta.current().id == "cred-beta"
    assert pool_beta.select("cred-beta").runtime_api_key == ROWS["cred-beta"]

    # ACCOUNT-WIDE half — the bench is on disk in the shared auth store…
    stored = _json.loads((hermes_home / "auth.json").read_text())["credential_pool"][PROVIDER]
    assert {row["id"]: row.get("last_status") for row in stored}["cred-alpha"] == "exhausted"
    # …so alpha's NEXT turn falls back, while beta's pin is still served.
    _model, runtime_alpha_again = _resolve(runner, TOPIC_ALPHA)
    assert runtime_alpha_again["api_key"] != ROWS["cred-alpha"]
    _model, runtime_beta_again = _resolve(runner, TOPIC_BETA)
    assert runtime_beta_again["api_key"] == ROWS["cred-beta"]


def test_exhausted_pin_falls_back_to_a_healthy_row(hermes_home):
    """Alpha's row benched on disk: the topic keeps serving on the pool's strategy."""
    runner = _runner()
    _model, runtime_alpha = _resolve(runner, TOPIC_ALPHA)
    runtime_alpha["credential_pool"].mark_exhausted_and_rotate(
        status_code=429, credential_id="cred-alpha", api_key_hint=ROWS["cred-alpha"])

    _model_again, runtime_again = _resolve(runner, TOPIC_ALPHA)

    assert runtime_again["api_key"] in {ROWS["cred-beta"], ROWS["cred-gamma"]}


def test_unknown_pin_falls_back_without_breaking_the_topic(hermes_home):
    runner = _runner()
    runner.config.platforms[Platform.TELEGRAM].channel_overrides[TOPIC_ALPHA] = ChannelOverride(
        credential_id="cred-does-not-exist")

    _model, runtime = _resolve(runner, TOPIC_ALPHA)

    assert runtime["api_key"] == ROWS["cred-alpha"]


def test_no_credential_material_is_persisted_into_the_channel_override(hermes_home):
    runner = _runner()
    _resolve(runner, TOPIC_ALPHA)
    serialized = runner.config.platforms[Platform.TELEGRAM].to_dict()["channel_overrides"]
    blob = json.dumps(serialized)
    assert "cred-alpha" in blob
    for token in ROWS.values():
        assert token not in blob
