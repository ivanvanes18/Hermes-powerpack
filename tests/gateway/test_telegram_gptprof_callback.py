"""Tests for public gptprof Telegram callbacks."""

import asyncio
import json
import importlib.util
from pathlib import Path
import stat
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from plugins.platforms.telegram import adapter as telegram_adapter_module
from plugins.platforms.telegram.adapter import TelegramAdapter, _gptprof_refresh_completed


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def _make_query():
    query = SimpleNamespace(
        from_user=SimpleNamespace(id="42", first_name="Alice"),
        message=SimpleNamespace(
            chat_id="42", chat=SimpleNamespace(type="private"), message_thread_id=None
        ),
        answer=AsyncMock(),
    )
    return query


def _load_send_buttons_module():
    path = Path(__file__).parents[2] / "skills" / "gptprof-hermes" / "bin" / "send_buttons.py"
    spec = importlib.util.spec_from_file_location("gptprof_send_buttons_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_gptprof_refresh_forces_pool_refresh_before_rendering_card(monkeypatch):
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = lambda *_args, **_kwargs: True
    adapter._run_gptprof_helper = AsyncMock(side_effect=[True, True])
    query = _make_query()

    await adapter._handle_gptprof_callback(query, "gptprof:refresh")

    calls = adapter._run_gptprof_helper.await_args_list
    assert len(calls) == 2
    refresh_cmd = calls[0].args[1]
    assert Path(refresh_cmd[1]).name == "gptprof_refresh_profiles.py"
    assert refresh_cmd[2:] == ["--force", "--json"]
    assert calls[0].kwargs["answer_success"] is False
    assert Path(calls[1].args[1][-1]).name == "gptprof_send_buttons.py"


@pytest.mark.asyncio
async def test_gptprof_refresh_failure_does_not_render_stale_card():
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = lambda *_args, **_kwargs: True
    adapter._run_gptprof_helper = AsyncMock(return_value=False)

    await adapter._handle_gptprof_callback(_make_query(), "gptprof:refresh")

    assert adapter._run_gptprof_helper.await_count == 1


def test_gptprof_refresh_completion_rejects_lock_contention():
    assert _gptprof_refresh_completed(
        json.dumps([{"state": "already_running"}]).encode()
    ) is False
    assert _gptprof_refresh_completed(
        json.dumps([{"state": "refreshed"}, {"state": "fresh"}]).encode()
    ) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callback", "action"),
    [
        ("gptprof:new_auth", "new_auth"),
        ("gptprof:check_auth", "check_auth"),
        ("gptprof:pi_route", "pi_route"),
    ],
)
async def test_gptprof_legacy_card_actions_still_dispatch(callback, action):
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = lambda *_args, **_kwargs: True
    adapter._run_gptprof_profile_action = AsyncMock()
    query = _make_query()

    await adapter._handle_gptprof_callback(query, callback)

    adapter._run_gptprof_profile_action.assert_awaited_once_with(query, action)


@pytest.mark.asyncio
async def test_gptprof_new_auth_renders_only_safe_device_flow_fields(monkeypatch):
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(
                json.dumps(
                    {"ok": True, "verificationUrl": "https://auth.openai.com/codex/device", "userCode": "ABCD-EFGH"}
                ).encode(),
                b"",
            )
        ),
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(telegram_adapter_module.asyncio, "create_subprocess_exec", spawn)
    query = _make_query()

    await _make_adapter()._run_gptprof_profile_action(query, "new_auth")

    assert spawn.await_args.args[-1] == "device-start"
    query.answer.assert_awaited_once_with(
        text="Open https://auth.openai.com/codex/device and enter code ABCD-EFGH",
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_gptprof_new_auth_rejects_untrusted_device_url(monkeypatch):
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(
                json.dumps(
                    {"ok": True, "verificationUrl": "https://evil.example/device", "userCode": "ABCD-EFGH"}
                ).encode(),
                b"",
            )
        ),
    )
    monkeypatch.setattr(
        telegram_adapter_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    query = _make_query()

    await _make_adapter()._run_gptprof_profile_action(query, "new_auth")

    assert query.answer.await_args.kwargs["text"] == "gptprof action failed"
    assert "evil.example" not in query.answer.await_args.kwargs["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "command", "expected_text"),
    [
        ("check_auth", "device-check", "Authorization complete."),
        ("pi_route", "apply-pi-route", "Pi route applied. Send /new for a clean session."),
    ],
)
async def test_gptprof_profile_actions_use_exact_manager_commands(
    monkeypatch, action, command, expected_text
):
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(json.dumps({"ok": True}).encode(), b"")),
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(telegram_adapter_module.asyncio, "create_subprocess_exec", spawn)
    query = _make_query()

    await _make_adapter()._run_gptprof_profile_action(query, action)

    assert spawn.await_args.args[-1] == command
    query.answer.assert_awaited_once_with(text=expected_text, show_alert=True)


@pytest.mark.asyncio
async def test_gptprof_unauthorized_callback_spawns_no_helper():
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = lambda *_args, **_kwargs: False
    adapter._run_gptprof_helper = AsyncMock()
    adapter._run_gptprof_profile_action = AsyncMock()

    await adapter._handle_gptprof_callback(_make_query(), "gptprof:new_auth")

    adapter._run_gptprof_helper.assert_not_awaited()
    adapter._run_gptprof_profile_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_gptprof_helper_timeout_kills_and_reaps_child(monkeypatch):
    proc = SimpleNamespace(returncode=None, communicate=AsyncMock(), kill=MagicMock())
    monkeypatch.setattr(
        telegram_adapter_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    async def raise_after_consuming(coro, *, timeout):
        await coro
        raise asyncio.TimeoutError

    monkeypatch.setattr(telegram_adapter_module.asyncio, "wait_for", raise_after_consuming)
    query = _make_query()

    ok = await _make_adapter()._run_gptprof_helper(
        query, ["helper"], "success", timeout=1
    )

    assert ok is False
    proc.kill.assert_called_once_with()
    assert proc.communicate.await_count == 2


@pytest.mark.asyncio
async def test_gptprof_profile_action_cancellation_kills_and_reaps_child(monkeypatch):
    proc = SimpleNamespace(returncode=None, communicate=AsyncMock(), kill=MagicMock())
    monkeypatch.setattr(
        telegram_adapter_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )

    async def cancel_wait(coro, *, timeout):
        coro.close()
        raise asyncio.CancelledError

    monkeypatch.setattr(telegram_adapter_module.asyncio, "wait_for", cancel_wait)

    with pytest.raises(asyncio.CancelledError):
        await _make_adapter()._run_gptprof_profile_action(_make_query(), "pi_route")

    proc.kill.assert_called_once_with()
    assert proc.communicate.await_count == 1


@pytest.mark.asyncio
async def test_gptprof_profile_action_does_not_echo_helper_failure(monkeypatch):
    proc = SimpleNamespace(
        returncode=1,
        communicate=AsyncMock(return_value=(b"", b"secret-bearing helper failure")),
    )
    monkeypatch.setattr(
        telegram_adapter_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    query = _make_query()

    await _make_adapter()._run_gptprof_profile_action(query, "pi_route")

    answer = query.answer.await_args.kwargs["text"]
    assert answer == "gptprof action failed"
    assert "secret-bearing" not in answer


def test_gptprof_card_exposes_auth_and_pi_route_controls():
    module = _load_send_buttons_module()

    class Button:
        def __init__(self, text, callback_data):
            self.text = text
            self.callback_data = callback_data

    rows = module.build_control_rows(Button)
    callbacks = [button.callback_data for row in rows for button in row]

    assert callbacks == [
        "gptprof:refresh",
        "gptprof:autoswitch",
        "gptprof:new_auth",
        "gptprof:check_auth",
        "gptprof:pi_route",
    ]


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
