"""Per-topic credential affinity: ``channel_overrides.<id>.credential_id``.

A Telegram forum topic may declare which CredentialPool row it prefers, by
non-secret stable id. The override carries the id only — never token material —
and it reaches runtime resolution as a pool affinity, without displacing the
session ``/model`` override that already outranks channel overrides.
"""

from __future__ import annotations

import logging
import threading

import pytest

from unittest.mock import patch

from gateway.config import (
    ChannelOverride, GatewayConfig, Platform, PlatformConfig, load_gateway_config,
)
from gateway.run import GatewayRunner
from gateway.session import SessionSource

TOPIC_A, TOPIC_B = "topic-a", "topic-b"


# ── config parse / serialize ────────────────────────────────────────────────


class TestChannelOverrideCredentialId:
    def test_parses_and_round_trips_credential_id(self):
        ov = ChannelOverride.from_dict({"model": "m", "credential_id": "cred-a1b2"})
        assert ov.credential_id == "cred-a1b2"
        assert ov.to_dict() == {"model": "m", "credential_id": "cred-a1b2"}

    def test_absent_credential_id_is_none_and_not_serialized(self):
        ov = ChannelOverride.from_dict({"model": "m"})
        assert ov.credential_id is None
        assert "credential_id" not in ov.to_dict()
        assert ChannelOverride().credential_id is None

    def test_platform_config_round_trip_preserves_credential_id_only(self):
        raw = {
            "enabled": True,
            "channel_overrides": {
                "9001": {"model": "m", "provider": "deepseek", "credential_id": "cred-a1b2"},
            },
        }
        restored = PlatformConfig.from_dict(raw).to_dict()
        entry = restored["channel_overrides"]["9001"]
        assert entry["credential_id"] == "cred-a1b2"
        assert PlatformConfig.from_dict(restored).channel_overrides["9001"].credential_id == "cred-a1b2"
        # The override is a pointer, not a secret: no token-shaped field may appear.
        assert set(entry) <= {"model", "provider", "system_prompt", "credential_id"}


# ── malformed values never reach selection ─────────────────────────────────


class TestCredentialIdValidation:
    @pytest.mark.parametrize(
        "raw",
        [
            [], {}, ["cred-a"], {"id": "cred-a"},          # containers
            "", "   ", "\n\t ",                            # blank scalars
            0, 1, 123456, 1.5, True, False,                # non-string scalars
            None,
        ],
        ids=repr,
    )
    def test_malformed_credential_id_becomes_none(self, raw):
        assert ChannelOverride.from_dict({"credential_id": raw}).credential_id is None
        # Direct construction (API server, tests, plugins) normalizes identically.
        assert ChannelOverride(credential_id=raw).credential_id is None

    def test_credential_id_is_stripped(self):
        assert ChannelOverride.from_dict({"credential_id": "  cred-a1b2\n"}).credential_id == "cred-a1b2"

    def test_malformed_credential_id_is_not_echoed_into_the_log(self, caplog):
        """The field is operator-editable; a mis-pasted token must not be logged."""
        with caplog.at_level(logging.DEBUG):
            ChannelOverride.from_dict({"credential_id": ["sk-mispasted-secret-value"]})
        assert "sk-mispasted-secret-value" not in caplog.text

    def test_malformed_credential_id_never_reaches_runtime_resolution(self):
        runner = _runner({TOPIC_A: ChannelOverride.from_dict({"credential_id": ["cred-a"]})})
        with patch("gateway.run._resolve_gateway_model", return_value="global/model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs", return_value=_runtime()) as base:
            runner._resolve_session_agent_runtime(source=_source(TOPIC_A))
        assert base.call_args.kwargs.get("preferred_credential_id") is None


# ── the documented YAML path, through the real loader ──────────────────────


class TestRawYamlLoader:
    def test_documented_telegram_topic_path_reaches_runtime_preference(self, tmp_path, monkeypatch):
        """``platforms.telegram.channel_overrides.<thread_id>.credential_id`` —
        exactly as documented — must survive the real config loader and arrive
        at runtime resolution as this topic's pool preference."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            "platforms:\n"
            "  telegram:\n"
            "    enabled: true\n"
            "    channel_overrides:\n"
            '      "3391":\n'
            "        credential_id: cred-topic-3391\n"
            '      "-1001234567890":\n'
            "        model: forum-wide-model\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        config = load_gateway_config()

        overrides = config.platforms[Platform.TELEGRAM].channel_overrides
        assert overrides["3391"].credential_id == "cred-topic-3391"
        assert overrides["-1001234567890"].credential_id is None

        runner = object.__new__(GatewayRunner)
        runner.config = config
        runner.adapters = {}
        runner._session_model_overrides = {}
        runner._last_resolved_model = {}
        runner._service_tier = None
        runner._agent_cache = {}
        runner._agent_cache_lock = threading.Lock()
        source = SessionSource(
            platform=Platform.TELEGRAM, chat_id="-1001234567890", chat_type="thread",
            user_id="u1", thread_id="3391",
        )
        with patch("gateway.run._resolve_gateway_model", return_value="global/model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs", return_value=_runtime()) as base:
            model, _runtime_kwargs = runner._resolve_session_agent_runtime(source=source)

        assert base.call_args.kwargs.get("preferred_credential_id") == "cred-topic-3391"
        # The topic declared no model, so the topic override (not the forum's) still wins lookup.
        assert model == "global/model"


# ── runtime wiring ──────────────────────────────────────────────────────────


def _runner(overrides: dict[str, ChannelOverride]) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(
            enabled=True, token="fake", channel_overrides=overrides)},
    )
    runner.adapters = {}
    runner._session_model_overrides = {}
    runner._last_resolved_model = {}
    runner._service_tier = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    return runner


def _source(thread_id: str | None = None) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM, chat_id="chat-1", chat_type="thread",
        user_id="u1", thread_id=thread_id,
    )


def _runtime(**extra):
    return {"provider": "deepseek", "api_key": "sk-fake-default", "api_mode": "chat_completions",
            "base_url": "https://api.deepseek.com", **extra}


class TestChannelCredentialReachesRuntimeResolution:
    def test_credential_id_only_pins_the_current_provider(self):
        """No ``provider:`` in the override — resolve the provider we already
        resolve, just pinned to the topic's row. No provider is invented."""
        runner = _runner({TOPIC_A: ChannelOverride(credential_id="cred-a1b2")})
        with patch("gateway.run._resolve_gateway_model", return_value="global/model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs",
                   return_value=_runtime(api_key="sk-fake-row-a")) as base, \
             patch("gateway.run._resolve_runtime_agent_kwargs_for_provider") as for_provider:
            model, runtime = runner._resolve_session_agent_runtime(source=_source(TOPIC_A))

        assert base.call_args.kwargs.get("preferred_credential_id") == "cred-a1b2"
        for_provider.assert_not_called()
        assert model == "global/model"
        assert runtime["api_key"] == "sk-fake-row-a"

    def test_credential_id_with_provider_pins_that_providers_pool(self):
        runner = _runner({TOPIC_A: ChannelOverride(provider="novita", credential_id="cred-a1b2")})
        with patch("gateway.run._resolve_gateway_model", return_value="global/model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs", return_value=_runtime()), \
             patch("gateway.run._resolve_runtime_agent_kwargs_for_provider",
                   return_value=_runtime(provider="novita", api_key="sk-fake-novita")) as for_provider:
            _model, runtime = runner._resolve_session_agent_runtime(source=_source(TOPIC_A))

        assert for_provider.call_args.args[0] == "novita"
        assert for_provider.call_args.kwargs.get("preferred_credential_id") == "cred-a1b2"
        assert runtime["provider"] == "novita"

    def test_override_without_credential_id_passes_no_preference(self):
        runner = _runner({TOPIC_A: ChannelOverride(model="channel/model")})
        with patch("gateway.run._resolve_gateway_model", return_value="global/model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs", return_value=_runtime()) as base:
            model, _runtime_kwargs = runner._resolve_session_agent_runtime(source=_source(TOPIC_A))

        assert base.call_args.kwargs.get("preferred_credential_id") is None
        assert model == "channel/model"

    def test_two_topics_resolve_to_their_own_credentials(self):
        """Each topic's runtime key follows its own id — that is what gives the
        two topics separate cached agents."""
        runner = _runner({
            TOPIC_A: ChannelOverride(credential_id="cred-a1b2"),
            TOPIC_B: ChannelOverride(credential_id="cred-c3d4"),
        })
        keys = {"cred-a1b2": "sk-fake-row-a", "cred-c3d4": "sk-fake-row-b", None: "sk-fake-default"}

        def _resolve(preferred_credential_id=None):
            return _runtime(api_key=keys[preferred_credential_id])

        with patch("gateway.run._resolve_gateway_model", return_value="global/model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs", side_effect=_resolve):
            _m_a, runtime_a = runner._resolve_session_agent_runtime(source=_source(TOPIC_A))
            _m_b, runtime_b = runner._resolve_session_agent_runtime(source=_source(TOPIC_B))
            _m_n, runtime_none = runner._resolve_session_agent_runtime(source=_source("topic-plain"))

        assert runtime_a["api_key"] == "sk-fake-row-a"
        assert runtime_b["api_key"] == "sk-fake-row-b"
        assert runtime_none["api_key"] == "sk-fake-default"

        signature = GatewayRunner._agent_config_signature
        assert signature("global/model", runtime_a, [], "") != signature("global/model", runtime_b, [], "")
        assert signature("global/model", runtime_a, [], "") == signature("global/model", dict(runtime_a), [], "")


class TestSessionModelOverridePrecedence:
    def test_session_override_with_api_key_outranks_channel_credential(self):
        runner = _runner({TOPIC_A: ChannelOverride(provider="novita", credential_id="cred-a1b2")})
        runner._session_model_overrides = {}
        source = _source(TOPIC_A)
        session_key = "agent:main:telegram:thread:chat-1:topic-a"
        runner._session_state(session_key).conversation.model_override = {
            "model": "session/model", "provider": "deepseek", "api_key": "sk-fake-session",
            "base_url": "https://api.deepseek.com", "api_mode": "chat_completions",
        }
        with patch("gateway.run._resolve_gateway_model", return_value="global/model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs", return_value=_runtime()) as base, \
             patch("gateway.run._resolve_runtime_agent_kwargs_for_provider") as for_provider, \
             patch("gateway.run._credential_pool_for_provider", return_value=None):
            model, runtime = runner._resolve_session_agent_runtime(
                source=source, session_key=session_key)

        assert model == "session/model"
        assert runtime["api_key"] == "sk-fake-session"
        base.assert_not_called()
        for_provider.assert_not_called()

    def test_session_override_without_api_key_still_wins_over_channel(self):
        """The credential-less override path runs channel resolution first, then
        layers /model on top — the topic pin must not survive that."""
        runner = _runner({TOPIC_A: ChannelOverride(model="channel/model", credential_id="cred-a1b2")})
        session_key = "agent:main:telegram:thread:chat-1:topic-a"
        runner._session_state(session_key).conversation.model_override = {"model": "session/model"}
        with patch("gateway.run._resolve_gateway_model", return_value="global/model"), \
             patch("gateway.run._resolve_runtime_agent_kwargs",
                   return_value=_runtime(api_key="sk-fake-row-a")) as base:
            model, _runtime_kwargs = runner._resolve_session_agent_runtime(
                source=_source(TOPIC_A), session_key=session_key)

        assert base.call_args.kwargs.get("preferred_credential_id") == "cred-a1b2"
        assert model == "session/model"
