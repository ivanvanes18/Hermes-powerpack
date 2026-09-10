"""Tests for public gptprof Telegram callbacks."""

import json
import stat
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def test_gptprof_switch_profile_updates_auth_and_config(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "profile1.json").write_text(
        json.dumps(
            {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "email": "user@example.com",
                "plan": "Pro",
            }
        ),
        encoding="utf-8",
    )
    (home / "auth.json").write_text("{}\n", encoding="utf-8")
    (home / "config.yaml").write_text("model:\n  default: MiniMax-M2.7\n  provider: minimax\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    adapter = _make_adapter()
    ok, message = adapter._gptprof_switch_profile("profile1", "gpt-5.5")

    assert ok, message
    auth = json.loads((home / "auth.json").read_text(encoding="utf-8"))
    assert auth["codex"]["profile"] == "profile1"
    assert auth["codex"]["access_token"] == "test-access-token"
    assert auth["providers"]["openai-codex"]["tokens"]["profile"] == "profile1"
    assert auth["credential_pool"]["openai-codex"][0]["source"] == "manual:device_code"
    assert auth["credential_pool"]["openai-codex"][0]["id"] == "gptprof:profile1"
    assert stat.S_IMODE((home / "auth.json").stat().st_mode) == 0o600
    assert not (home / "auth.json.tmp").exists()

    config = (home / "config.yaml").read_text(encoding="utf-8")
    assert "default: gpt-5.5" in config
    assert "provider: openai-codex" in config


def test_legacy_callback_prefers_newer_canonical_pool_tokens(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "profile1.json").write_text(
        json.dumps({"access_token": "stale-a", "refresh_token": "stale-r"}),
        encoding="utf-8",
    )
    auth_path = home / "auth.json"
    auth_path.write_text(
        json.dumps({
            "credential_pool": {"openai-codex": [{
                "id": "gptprof:profile1",
                "source": "manual:device_code",
                "profile": "profile1",
                "access_token": "fresh-a",
                "refresh_token": "fresh-r",
                "priority": 10,
            }]}
        }),
        encoding="utf-8",
    )
    (home / "config.yaml").write_text("model:\n  default: old\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    ok, message = _make_adapter()._gptprof_switch_profile("profile1", "gpt-5.5")

    assert ok, message
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    assert auth["codex"]["access_token"] == "fresh-a"
    assert auth["codex"]["refresh_token"] == "fresh-r"
    assert len(auth["credential_pool"]["openai-codex"]) == 1
    assert auth["credential_pool"]["openai-codex"][0]["id"] == "gptprof:profile1"


def test_removed_pool_entry_cannot_be_resurrected_from_stale_bootstrap(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "profile1.json").write_text(
        json.dumps({"access_token": "stale-a", "refresh_token": "stale-r"}),
        encoding="utf-8",
    )
    auth_path = home / "auth.json"
    auth_path.write_text(
        json.dumps({
            "gptprof": {"imported_profiles": {"profile1": {"imported": True}}},
            "credential_pool": {"openai-codex": []},
        }),
        encoding="utf-8",
    )
    config_path = home / "config.yaml"
    config_path.write_text("model:\n  default: old\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    before_auth = auth_path.read_bytes()
    before_config = config_path.read_bytes()

    ok, message = _make_adapter()._gptprof_switch_profile("profile1", "gpt-5.5")

    assert not ok
    assert "stale bootstrap" in message
    assert auth_path.read_bytes() == before_auth
    assert config_path.read_bytes() == before_config


def test_gptprof_switch_rejects_missing_profile(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    (home / "gptprof" / "profiles").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    adapter = _make_adapter()
    ok, message = adapter._gptprof_switch_profile("missing", "gpt-5.5")

    assert not ok
    assert "missing" in message


@pytest.mark.asyncio
async def test_gptprof_pool_callback_selects_exact_pool_row_without_bootstrap(
    tmp_path, monkeypatch
):
    home = tmp_path / "hermes-home"
    home.mkdir()
    auth_path = home / "auth.json"
    config_path = home / "config.yaml"
    auth_path.write_text(
        json.dumps(
            {
                "active_provider": "openai-codex",
                "credential_pool": {
                    "openai-codex": [
                        {
                            "id": "acct-a1",
                            "source": "manual:device_code",
                            "label": "Work Account",
                            "access_token": "access-a",
                            "refresh_token": "refresh-a",
                            "priority": 10,
                        },
                        {
                            "id": "acct-b2",
                            "source": "manual:device_code",
                            "label": "Backup",
                            "access_token": "access-b",
                            "refresh_token": "refresh-b",
                            "priority": 0,
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        "model:\n  default: MiniMax-M2.7\n  provider: minimax\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_AUTH", str(auth_path))
    monkeypatch.setenv("HERMES_CONFIG", str(config_path))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "*")

    adapter = _make_adapter()
    loads = []
    original_load = adapter._gptprof_load_json

    def tracking_load(path, default):
        loads.append(path)
        return original_load(path, default)

    monkeypatch.setattr(adapter, "_gptprof_load_json", tracking_load)
    query = AsyncMock()
    query.from_user = SimpleNamespace(id="42", first_name="Alice")
    query.message = SimpleNamespace(
        chat_id="42", chat=SimpleNamespace(type="private"), message_thread_id=None
    )

    await adapter._handle_gptprof_callback(
        query, "gptprof:pool:acct-a1:gpt-5.6-sol"
    )

    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    pool = auth["credential_pool"]["openai-codex"]
    assert next(item for item in pool if item["id"] == "acct-a1")["priority"] == 0
    assert next(item for item in pool if item["id"] == "acct-b2")["priority"] == 10
    config = config_path.read_text(encoding="utf-8")
    assert "default: gpt-5.6-sol" in config
    assert "provider: openai-codex" in config
    assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
    assert not any("gptprof" in str(path) and "profiles" in str(path) for path in loads)


def test_gptprof_pool_switch_rejects_duplicate_exact_ids(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    auth_path = home / "auth.json"
    config_path = home / "config.yaml"
    auth_path.write_text(
        json.dumps(
            {
                "credential_pool": {
                    "openai-codex": [
                        {"id": "acct-dup", "source": "manual:device_code", "priority": 0},
                        {"id": "acct-dup", "source": "manual:device_code", "priority": 10},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text("model:\n  default: old\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_AUTH", str(auth_path))
    monkeypatch.setenv("HERMES_CONFIG", str(config_path))

    adapter = _make_adapter()
    before_auth = auth_path.read_text(encoding="utf-8")
    before_config = config_path.read_text(encoding="utf-8")
    ok, message = adapter._gptprof_switch_profile("pool:acct-dup", "gpt-5.6-sol")

    assert not ok
    assert "ambiguous" in message
    assert auth_path.read_text(encoding="utf-8") == before_auth
    assert config_path.read_text(encoding="utf-8") == before_config


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"access_token": ""}, "access token"),
        ({"refresh_token": None}, "refresh token"),
        ({"last_status": "dead"}, "dead"),
        (
            {
                "last_status": "exhausted",
                "last_status_at": time.time(),
                "last_error_code": 429,
                "last_error_reset_at": time.time() + 3600,
            },
            "cooldown",
        ),
    ],
    ids=("missing-access", "missing-refresh", "dead", "active-exhausted"),
)
def test_gptprof_pool_switch_rejects_ineligible_entry_without_mutation(
    tmp_path, monkeypatch, updates, reason
):
    home = tmp_path / "hermes-home"
    home.mkdir()
    auth_path = home / "auth.json"
    config_path = home / "config.yaml"
    entry = {
        "id": "acct-ineligible",
        "source": "manual:device_code",
        "access_token": "access-a",
        "refresh_token": "refresh-a",
        "priority": 0,
        **updates,
    }
    auth_path.write_text(
        json.dumps({"credential_pool": {"openai-codex": [entry]}}),
        encoding="utf-8",
    )
    config_path.write_text("model:\n  default: old\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_AUTH", str(auth_path))
    monkeypatch.setenv("HERMES_CONFIG", str(config_path))

    adapter = _make_adapter()
    before_auth = auth_path.read_bytes()
    before_config = config_path.read_bytes()

    ok, message = adapter._gptprof_switch_profile(
        "pool:acct-ineligible", "gpt-5.6-sol"
    )

    assert not ok
    assert reason in message
    assert auth_path.read_bytes() == before_auth
    assert config_path.read_bytes() == before_config


def test_gptprof_pool_switch_allows_expired_exhausted_entry(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    auth_path = home / "auth.json"
    config_path = home / "config.yaml"
    auth_path.write_text(
        json.dumps(
            {
                "credential_pool": {
                    "openai-codex": [
                        {
                            "id": "acct-expired",
                            "source": "manual:device_code",
                            "access_token": "access-a",
                            "refresh_token": "refresh-a",
                            "priority": 10,
                            "last_status": "exhausted",
                            "last_status_at": time.time() - 3600,
                            "last_error_code": 429,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text("model:\n  default: old\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_AUTH", str(auth_path))
    monkeypatch.setenv("HERMES_CONFIG", str(config_path))

    adapter = _make_adapter()
    ok, message = adapter._gptprof_switch_profile(
        "pool:acct-expired", "gpt-5.6-sol"
    )

    assert ok, message
    assert "default: gpt-5.6-sol" in config_path.read_text(encoding="utf-8")
