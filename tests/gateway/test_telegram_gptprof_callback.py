"""Tests for public gptprof Telegram callbacks."""

import hashlib
import json
import stat
import sys
import types
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


import pytest


_telegram = types.ModuleType("telegram")
for _name in ("Update", "Bot", "Message", "InlineKeyboardButton", "InlineKeyboardMarkup"):
    setattr(_telegram, _name, MagicMock())
_telegram_ext = types.ModuleType("telegram.ext")
for _name in ("Application", "CommandHandler", "CallbackQueryHandler", "MessageHandler", "filters"):
    setattr(_telegram_ext, _name, MagicMock())
_telegram_ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=type(None))
_telegram_constants = types.ModuleType("telegram.constants")
_telegram_constants.ParseMode = SimpleNamespace(
    MARKDOWN="Markdown",
    MARKDOWN_V2="MarkdownV2",
    HTML="HTML",
)
_telegram_constants.ChatType = SimpleNamespace(
    PRIVATE="private",
    GROUP="group",
    SUPERGROUP="supergroup",
    CHANNEL="channel",
)
_telegram_request = types.ModuleType("telegram.request")
_telegram_request.HTTPXRequest = MagicMock()
_telegram_error = types.ModuleType("telegram.error")
_telegram_error.NetworkError = type("NetworkError", (OSError,), {})
_telegram_error.TimedOut = type("TimedOut", (OSError,), {})
_telegram_error.BadRequest = type("BadRequest", (Exception,), {})
_telegram.error = _telegram_error


@pytest.fixture(autouse=True)
def _install_telegram_modules(monkeypatch):
    monkeypatch.setitem(sys.modules, "telegram", _telegram)
    monkeypatch.setitem(sys.modules, "telegram.ext", _telegram_ext)
    monkeypatch.setitem(sys.modules, "telegram.constants", _telegram_constants)
    monkeypatch.setitem(sys.modules, "telegram.request", _telegram_request)
    monkeypatch.setitem(sys.modules, "telegram.error", _telegram_error)


from gateway.config import PlatformConfig


def _make_adapter():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = MagicMock()
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
    assert auth["gptprof"]["active_profile"] == "profile1"
    selected = auth["credential_pool"]["openai-codex"][0]
    assert selected["id"] == "gptprof:profile1"
    assert selected["source"] == "manual:device_code"
    assert selected["auth_type"] == "oauth"
    assert selected["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert stat.S_IMODE((home / "auth.json").stat().st_mode) == 0o600

    monkeypatch.setattr("hermes_cli.auth._import_codex_cli_tokens", lambda: None)
    from agent.credential_pool import load_pool

    runtime_entry = load_pool("openai-codex").select()
    assert runtime_entry is not None
    assert runtime_entry.id == "gptprof:profile1"
    assert runtime_entry.access_token == "test-access-token"

    config = (home / "config.yaml").read_text(encoding="utf-8")
    assert "default: gpt-5.5" in config
    assert "provider: openai-codex" in config


def test_gptprof_reselect_prefers_runtime_pool_tokens(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "profile1.json").write_text(
        json.dumps({"access_token": "stale-access", "refresh_token": "stale-refresh"}),
        encoding="utf-8",
    )
    (home / "auth.json").write_text(
        json.dumps(
            {
                "providers": {},
                "credential_pool": {
                    "openai-codex": [
                        {
                            "id": "gptprof:profile1",
                            "source": "manual:device_code",
                            "label": "profile1",
                            "auth_type": "oauth",
                            "priority": 0,
                            "access_token": "fresh-runtime-access",
                            "refresh_token": "fresh-runtime-refresh",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (home / "config.yaml").write_text("model: MiniMax-M2.7\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    ok, message = _make_adapter()._gptprof_switch_profile("profile1", "gpt-5.5")

    assert ok, message
    auth = json.loads((home / "auth.json").read_text(encoding="utf-8"))
    selected = auth["credential_pool"]["openai-codex"][0]
    assert selected["access_token"] == "fresh-runtime-access"
    assert selected["refresh_token"] == "fresh-runtime-refresh"


def test_gptprof_reselect_rejects_unavailable_runtime_entry(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "profile1.json").write_text(
        json.dumps({"access_token": "stale-access", "refresh_token": "stale-refresh"}),
        encoding="utf-8",
    )
    (home / "auth.json").write_text(
        json.dumps(
            {
                "gptprof": {
                    "imported_profiles": {
                        "profile1": {
                            "refresh_fingerprint": hashlib.sha256(
                                b"stale-refresh"
                            ).hexdigest()[:16]
                        }
                    }
                },
                "credential_pool": {
                    "openai-codex": [
                        {
                            "id": "gptprof:profile1",
                            "source": "manual:device_code",
                            "label": "profile1",
                            "auth_type": "oauth",
                            "priority": 0,
                            "access_token": "runtime-access",
                            "refresh_token": "runtime-refresh",
                            "last_status": "exhausted",
                            "last_status_at": 4_000_000_000.0,
                            "last_error_code": 429,
                            "request_count": 7,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (home / "config.yaml").write_text("model: MiniMax-M2.7\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    original_auth = (home / "auth.json").read_text(encoding="utf-8")
    original_config = (home / "config.yaml").read_text(encoding="utf-8")

    ok, message = _make_adapter()._gptprof_switch_profile("profile1", "gpt-5.5")

    assert not ok
    assert "unavailable" in message.lower()
    assert (home / "auth.json").read_text(encoding="utf-8") == original_auth
    assert (home / "config.yaml").read_text(encoding="utf-8") == original_config


def test_gptprof_does_not_resurrect_consumed_bootstrap_tokens(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "profile1.json").write_text(
        json.dumps({"access_token": "stale-access", "refresh_token": "stale-refresh"}),
        encoding="utf-8",
    )
    fingerprint = hashlib.sha256(b"stale-refresh").hexdigest()[:16]
    auth_path = home / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "providers": {},
                "gptprof": {
                    "imported_profiles": {
                        "profile1": {"refresh_fingerprint": fingerprint}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config_path = home / "config.yaml"
    config_path.write_text("model: MiniMax-M2.7\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    original_auth = auth_path.read_text(encoding="utf-8")

    ok, message = _make_adapter()._gptprof_switch_profile("profile1", "gpt-5.5")

    assert not ok
    assert "stale bootstrap" in message.lower()
    assert auth_path.read_text(encoding="utf-8") == original_auth
    assert config_path.read_text(encoding="utf-8") == "model: MiniMax-M2.7\n"


def test_gptprof_allows_fresh_reauth_bootstrap_after_pool_entry_disappears(
    tmp_path, monkeypatch
):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "profile1.json").write_text(
        json.dumps({"access_token": "new-access", "refresh_token": "new-refresh"}),
        encoding="utf-8",
    )
    old_fingerprint = hashlib.sha256(b"old-refresh").hexdigest()[:16]
    (home / "auth.json").write_text(
        json.dumps(
            {
                "gptprof": {
                    "imported_profiles": {
                        "profile1": {"refresh_fingerprint": old_fingerprint}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (home / "config.yaml").write_text("model: MiniMax-M2.7\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    ok, message = _make_adapter()._gptprof_switch_profile("profile1", "gpt-5.5")

    assert ok, message
    selected = json.loads((home / "auth.json").read_text(encoding="utf-8"))[
        "credential_pool"
    ]["openai-codex"][0]
    assert selected["refresh_token"] == "new-refresh"


def test_gptprof_rejects_rollback_to_any_consumed_bootstrap_token(
    tmp_path, monkeypatch
):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    profile_path = profiles / "profile1.json"
    auth_path = home / "auth.json"
    config_path = home / "config.yaml"
    auth_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    config_path.write_text("model: MiniMax-M2.7\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    adapter = _make_adapter()

    profile_path.write_text(
        json.dumps({"access_token": "access-A", "refresh_token": "refresh-A"}),
        encoding="utf-8",
    )
    assert adapter._gptprof_switch_profile("profile1", "gpt-5.5")[0]

    profile_path.write_text(
        json.dumps({"access_token": "access-B", "refresh_token": "refresh-B"}),
        encoding="utf-8",
    )
    assert adapter._gptprof_switch_profile("profile1", "gpt-5.5")[0]

    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["credential_pool"]["openai-codex"] = []
    auth_path.write_text(json.dumps(auth), encoding="utf-8")
    profile_path.write_text(
        json.dumps({"access_token": "access-A", "refresh_token": "refresh-A"}),
        encoding="utf-8",
    )
    original_auth = auth_path.read_text(encoding="utf-8")

    ok, message = adapter._gptprof_switch_profile("profile1", "gpt-5.5")

    assert not ok
    assert "stale bootstrap" in message.lower()
    assert auth_path.read_text(encoding="utf-8") == original_auth


def test_gptprof_ignores_noncanonical_hermes_auth_override(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "profile1.json").write_text(
        json.dumps({"access_token": "access", "refresh_token": "refresh"}),
        encoding="utf-8",
    )
    auth_path = home / "auth.json"
    auth_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    (home / "config.yaml").write_text("model: MiniMax-M2.7\n", encoding="utf-8")
    override_path = tmp_path / "override-auth.json"
    override_payload = json.dumps({"providers": {}, "sentinel": "unchanged"})
    override_path.write_text(override_payload, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_AUTH", str(override_path))

    ok, message = _make_adapter()._gptprof_switch_profile("profile1", "gpt-5.5")

    assert ok, message
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    assert auth["credential_pool"]["openai-codex"][0]["id"] == "gptprof:profile1"
    assert override_path.read_text(encoding="utf-8") == override_payload


def test_gptprof_switch_fails_closed_in_managed_mode(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profiles = home / "gptprof" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "profile1.json").write_text(
        json.dumps({"access_token": "access", "refresh_token": "refresh"}),
        encoding="utf-8",
    )
    auth_path = home / "auth.json"
    auth_path.write_text("{}\n", encoding="utf-8")
    config_path = home / "config.yaml"
    config_path.write_text("model: MiniMax-M2.7\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.config.is_managed", lambda: True)

    ok, message = _make_adapter()._gptprof_switch_profile("profile1", "gpt-5.5")

    assert not ok
    assert "managed" in message.lower()
    assert auth_path.read_text(encoding="utf-8") == "{}\n"
    assert config_path.read_text(encoding="utf-8") == "model: MiniMax-M2.7\n"


def test_gptprof_switch_rejects_missing_profile(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    (home / "gptprof" / "profiles").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    adapter = _make_adapter()
    ok, message = adapter._gptprof_switch_profile("missing", "gpt-5.5")

    assert not ok
    assert "missing" in message


@pytest.mark.parametrize(
    ("slug", "model"),
    [
        ("../outside", "gpt-5.5"),
        ("profile1", "gpt-5.5\nmalformed"),
    ],
)
def test_gptprof_switch_rejects_unsafe_identifiers(slug, model):
    adapter = _make_adapter()

    ok, message = adapter._gptprof_switch_profile(slug, model)

    assert not ok
    assert "Invalid" in message


@pytest.mark.asyncio
async def test_gptprof_callback_rejects_unauthorized_user():
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = MagicMock(return_value=False)
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42, first_name="Tester"),
        message=SimpleNamespace(
            chat_id=7,
            chat=SimpleNamespace(type="private"),
            message_thread_id=None,
        ),
        answer=AsyncMock(),
    )

    await adapter._handle_gptprof_callback(query, "gptprof:profile1:gpt-5.5")

    query.answer.assert_awaited_once()
    assert "not authorized" in query.answer.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_callback_router_dispatches_gptprof_callbacks():
    adapter = _make_adapter()
    adapter._handle_gptprof_callback = AsyncMock()
    query = SimpleNamespace(
        data="gptprof:profile1:gpt-5.5",
        message=SimpleNamespace(
            chat_id=7,
            chat=SimpleNamespace(type="private"),
            message_thread_id=None,
        ),
        from_user=SimpleNamespace(id=42, first_name="Tester"),
    )

    await adapter._handle_callback_query(SimpleNamespace(callback_query=query), None)

    adapter._handle_gptprof_callback.assert_awaited_once_with(query, query.data)


@pytest.mark.asyncio
async def test_gptprof_openclaw_actions_are_not_executed(monkeypatch):
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = MagicMock(return_value=True)
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=AssertionError("must not execute OpenClaw manager")),
    )
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42, first_name="Tester"),
        message=SimpleNamespace(
            chat_id=7,
            chat=SimpleNamespace(type="private"),
            message_thread_id=None,
        ),
        answer=AsyncMock(),
    )

    await adapter._handle_gptprof_callback(query, "gptprof:new_auth")

    assert "Invalid" in query.answer.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_gptprof_helper_kills_process_on_timeout(monkeypatch):
    adapter = _make_adapter()
    proc = SimpleNamespace(
        communicate=AsyncMock(side_effect=[asyncio.TimeoutError(), (b"", b"")]),
        kill=MagicMock(),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    query = SimpleNamespace(answer=AsyncMock())

    await adapter._run_gptprof_helper(query, ["helper"], "empty", timeout=1)

    proc.kill.assert_called_once_with()
    assert proc.communicate.await_count == 2
    assert "timed out" in query.answer.await_args.kwargs["text"]
