"""Quick-command subprocess environment carries the invoking source."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform


def _make_source(*, platform=Platform.TELEGRAM, chat_id="-100123", thread_id="42"):
    source = MagicMock()
    source.platform = platform
    source.chat_id = chat_id
    source.thread_id = thread_id
    return source


def _make_event(command, args=""):
    event = MagicMock()
    event.get_command.return_value = command
    event.get_command_args.return_value = args
    event.text = f"/{command} {args}".strip()
    event.source = _make_source()
    event.source.user_id = "test_user"
    event.source.user_name = "Test User"
    event.source.chat_type = "group"
    return event


def test_inject_quick_command_context_overrides_stale_values_and_preserves_env():
    from gateway.run_inbound import _inject_quick_command_context

    env = {
        "PATH": "/usr/bin",
        "HERMES_QUICK_CHAT_ID": "old-chat",
        "HERMES_QUICK_THREAD_ID": "old-thread",
        "HERMES_QUICK_PLATFORM": "old-platform",
    }
    result = _inject_quick_command_context(env, _make_source())

    assert result is env
    assert env["PATH"] == "/usr/bin"
    assert env["HERMES_QUICK_CHAT_ID"] == "-100123"
    assert env["HERMES_QUICK_THREAD_ID"] == "42"
    assert env["HERMES_QUICK_PLATFORM"] == "telegram"


def test_inject_quick_command_context_clears_stale_optional_values_and_handles_plain_platform():
    from gateway.run_inbound import _inject_quick_command_context

    env = {
        "KEEP": "yes",
        "HERMES_QUICK_CHAT_ID": "old-chat",
        "HERMES_QUICK_THREAD_ID": "old-thread",
        "HERMES_QUICK_PLATFORM": "old-platform",
    }
    source = SimpleNamespace(chat_id=None, thread_id=None, platform="custom")

    _inject_quick_command_context(env, source)

    assert env == {"KEEP": "yes", "HERMES_QUICK_PLATFORM": "custom"}


@pytest.mark.asyncio
async def test_quick_command_exec_receives_source_context(monkeypatch):
    import gateway.run_inbound as run_inbound
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = {"quick_commands": {"notify": {"type": "exec", "command": "printf source-context"}}}
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._is_user_authorized = MagicMock(return_value=True)

    captured = {}

    class _Process:
        async def communicate(self):
            return b"source-context", b""

    async def _create_subprocess_shell(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return _Process()

    monkeypatch.setattr(run_inbound.asyncio, "create_subprocess_shell", _create_subprocess_shell)

    event = _make_event("notify")
    result = await runner._handle_message(event)

    assert result == "source-context"
    assert captured["command"] == "printf source-context"
    assert captured["env"]["HERMES_QUICK_CHAT_ID"] == "-100123"
    assert captured["env"]["HERMES_QUICK_THREAD_ID"] == "42"
    assert captured["env"]["HERMES_QUICK_PLATFORM"] == "telegram"


@pytest.mark.asyncio
async def test_quick_command_output_is_force_redacted(monkeypatch):
    import agent.redact
    import gateway.run_inbound as run_inbound
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    seen = {}

    class _Process:
        async def communicate(self):
            return b"Bearer synthetic-secret-value", b""

    async def _create_subprocess_shell(*_args, **_kwargs):
        return _Process()

    def _redact(text, *, force=False, **_kwargs):
        seen.update(text=text, force=force)
        return "[redacted]"

    monkeypatch.setattr(run_inbound.asyncio, "create_subprocess_shell", _create_subprocess_shell)
    monkeypatch.setattr(agent.redact, "redact_sensitive_text", _redact)

    result = await runner._hm_run_exec_quick_command("notify", "helper", _make_source())

    assert result == "[redacted]"
    assert seen == {"text": "Bearer synthetic-secret-value", "force": True}


@pytest.mark.asyncio
async def test_quick_command_spawn_error_is_generic(monkeypatch):
    import gateway.run_inbound as run_inbound
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)

    async def _create_subprocess_shell(*_args, **_kwargs):
        raise RuntimeError("Bearer synthetic-secret-value")

    monkeypatch.setattr(run_inbound.asyncio, "create_subprocess_shell", _create_subprocess_shell)

    result = await runner._hm_run_exec_quick_command("notify", "helper", _make_source())

    assert result == "Quick command failed."
    assert "synthetic-secret-value" not in result


def _silent_runner(returncode, stdout, stderr=b""):
    import gateway.run_inbound as run_inbound
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = {"quick_commands": {"notify": {"type": "exec", "command": "helper", "silent": True}}}
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._is_user_authorized = MagicMock(return_value=True)

    class _Process:
        async def communicate(self):
            return stdout, stderr

    _Process.returncode = returncode

    async def _create_subprocess_shell(*_args, **_kwargs):
        return _Process()

    return runner, run_inbound, _create_subprocess_shell


@pytest.mark.asyncio
async def test_silent_quick_command_suppresses_success_output(monkeypatch):
    runner, run_inbound, spawn = _silent_runner(0, b"GPT Profile card sent.")
    monkeypatch.setattr(run_inbound.asyncio, "create_subprocess_shell", spawn)

    assert await runner._handle_message(_make_event("notify")) is None


@pytest.mark.asyncio
async def test_silent_quick_command_still_surfaces_failure(monkeypatch):
    runner, run_inbound, spawn = _silent_runner(1, b"", b"helper failed")
    monkeypatch.setattr(run_inbound.asyncio, "create_subprocess_shell", spawn)

    assert await runner._handle_message(_make_event("notify")) == "helper failed"
