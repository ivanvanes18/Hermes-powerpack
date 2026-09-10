"""Tests for TelegramAdapter.send_or_update_status (issue #30045).

The status-update path must:
  1. Send a fresh message on the first call for a (chat_id, status_key) pair.
  2. Edit that same message on subsequent calls with the same key.
  3. Fall back to sending fresh when the cached message edit fails.
  4. Keep distinct keys independent (no cross-talk).
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult


def _install_fake_telegram(monkeypatch):
    """Stub the python-telegram-bot package so TelegramAdapter can be imported."""
    fake_telegram = types.ModuleType("telegram")
    fake_telegram.Update = SimpleNamespace(ALL_TYPES=())
    fake_telegram.Bot = object
    fake_telegram.Message = object
    fake_telegram.InlineKeyboardButton = object
    fake_telegram.InlineKeyboardMarkup = object

    fake_error = types.ModuleType("telegram.error")
    fake_error.NetworkError = type("NetworkError", (Exception,), {})
    fake_error.BadRequest = type("BadRequest", (Exception,), {})
    fake_error.TimedOut = type("TimedOut", (Exception,), {})
    fake_telegram.error = fake_error

    fake_constants = types.ModuleType("telegram.constants")
    fake_constants.ParseMode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    fake_constants.ChatType = SimpleNamespace(
        GROUP="group", SUPERGROUP="supergroup",
        CHANNEL="channel", PRIVATE="private",
    )
    fake_telegram.constants = fake_constants

    fake_ext = types.ModuleType("telegram.ext")
    fake_ext.Application = object
    fake_ext.CommandHandler = object
    fake_ext.CallbackQueryHandler = object
    fake_ext.InlineQueryHandler = object
    fake_ext.MessageHandler = object
    fake_ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
    fake_ext.filters = object

    fake_request = types.ModuleType("telegram.request")
    fake_request.HTTPXRequest = object

    monkeypatch.setitem(sys.modules, "telegram", fake_telegram)
    monkeypatch.setitem(sys.modules, "telegram.error", fake_error)
    monkeypatch.setitem(sys.modules, "telegram.constants", fake_constants)
    monkeypatch.setitem(sys.modules, "telegram.ext", fake_ext)
    monkeypatch.setitem(sys.modules, "telegram.request", fake_request)


@pytest.fixture
def adapter(monkeypatch):
    _install_fake_telegram(monkeypatch)
    from plugins.platforms.telegram.adapter import TelegramAdapter

    a = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))
    a._bot = MagicMock()
    # Patch send / edit_message so tests can drive them directly.
    a.send = AsyncMock()
    a.edit_message = AsyncMock()
    return a


@pytest.mark.asyncio
async def test_first_call_sends_and_caches_message_id(adapter):
    """First call for a (chat, key) pair must send and remember the id."""
    adapter.send.return_value = SendResult(success=True, message_id="100")

    result = await adapter.send_or_update_status("chat-1", "lifecycle", "starting")

    assert result.success is True
    assert result.message_id == "100"
    adapter.send.assert_awaited_once()
    adapter.edit_message.assert_not_awaited()
    assert adapter._status_message_ids[("chat-1", "lifecycle")] == "100"


@pytest.mark.asyncio
async def test_distinct_status_keys_do_not_collide(adapter):
    """A different status_key gets its own message; the original isn't touched."""
    adapter.send.side_effect = [
        SendResult(success=True, message_id="100"),
        SendResult(success=True, message_id="200"),
    ]

    await adapter.send_or_update_status("chat-1", "lifecycle", "ctx pressure")
    await adapter.send_or_update_status("chat-1", "model-switch", "switched to opus")

    assert adapter.send.await_count == 2
    adapter.edit_message.assert_not_awaited()
    assert adapter._status_message_ids[("chat-1", "lifecycle")] == "100"
    assert adapter._status_message_ids[("chat-1", "model-switch")] == "200"




@pytest.mark.asyncio
async def test_successful_delete_evicts_matching_status_cache_entries(adapter):
    """A deleted message id must not stay cached, or the next status edit targets a
    message Telegram no longer has and the reply silently disappears."""
    adapter._bot.delete_message = AsyncMock(return_value=True)
    adapter._status_message_ids = {
        ("chat-1", "lifecycle"): "100",
        ("chat-1", "model-switch"): "100",
        ("chat-1", "other"): "200",
        ("chat-2", "lifecycle"): "100",
    }

    deleted = await adapter.delete_message("chat-1", "100")

    assert deleted is True
    assert adapter._status_message_ids == {
        ("chat-1", "other"): "200",
        ("chat-2", "lifecycle"): "100",
    }


@pytest.mark.asyncio
async def test_failed_delete_preserves_status_cache_entries(adapter):
    """A refused deletion leaves the message in place, so the cache must survive."""
    adapter._bot.delete_message = AsyncMock(return_value=False)
    adapter._status_message_ids = {
        ("chat-1", "lifecycle"): "100",
        ("chat-1", "other"): "200",
    }

    deleted = await adapter.delete_message("chat-1", "100")

    assert deleted is False
    assert adapter._status_message_ids == {
        ("chat-1", "lifecycle"): "100",
        ("chat-1", "other"): "200",
    }


@pytest.mark.asyncio
async def test_status_cache_keys_are_canonical_chat_identities(adapter):
    """The same chat arrives as int, str and '+'-prefixed str depending on the caller; all
    three must hit one cache entry or an edit would fork into a second status bubble."""
    adapter.send.return_value = SendResult(success=True, message_id="100")

    await adapter.send_or_update_status(-100123, "lifecycle", "first")
    assert adapter._status_message_ids == {("-100123", "lifecycle"): "100"}

    adapter.edit_message.return_value = SendResult(success=True, message_id="100")
    await adapter.send_or_update_status("-100123", "lifecycle", "second")

    adapter.edit_message.assert_awaited_once()
    assert adapter.send.await_count == 1
    assert adapter._status_message_ids == {("-100123", "lifecycle"): "100"}


@pytest.mark.asyncio
async def test_delete_evicts_across_chat_id_spelling_variants(adapter):
    """A deletion issued with a differently-spelled chat/message id must still evict: the
    stale entry would otherwise send every later status edit at a message Telegram deleted."""
    adapter._bot.delete_message = AsyncMock(return_value=True)
    adapter._status_message_ids = {
        ("123", "lifecycle"): "100",
        ("123", "other"): "200",
        ("456", "lifecycle"): "100",
    }

    deleted = await adapter.delete_message("+123", "+100")

    assert deleted is True
    assert adapter._status_message_ids == {
        ("123", "other"): "200",
        ("456", "lifecycle"): "100",
    }
