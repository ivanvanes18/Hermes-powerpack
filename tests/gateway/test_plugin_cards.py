import pytest

from gateway.plugin_cards import PluginCard, PluginCardButton, PluginCommandContext, PluginCardRegistry, validate_telegram_callback_data


def test_card_registry_dispatches_authorized_bound_action():
    seen = []
    registry = PluginCardRegistry()
    registry.add(PluginCard(
        card_id="task-1", title="Task", body="Next step", buttons=(
            PluginCardButton("continue", "Continue"),
        ), source_chat_id="42", source_thread_id="7", session_key="telegram:42:7",
    ), action=lambda action, ctx: (seen.append((action, ctx.chat_id, ctx.thread_id)) or "ok"))
    result = registry.dispatch("task-1", "continue", PluginCommandContext(
        event=None, source=None, session_key="telegram:42:7", chat_id="42", thread_id="7", user_id="9"))
    assert result == "ok"
    assert seen == [("continue", "42", "7")]


def test_card_registry_rejects_wrong_chat_or_thread():
    registry = PluginCardRegistry()
    registry.add(PluginCard("task-1", "Task", "Next", (), "42", "7", "s"), action=lambda *_: None)
    with pytest.raises(PermissionError):
        registry.dispatch("task-1", "continue", PluginCommandContext(None, None, "s", "99", "7", "9"))


def test_plugin_context_register_command_accepts_gateway_handler():
    from hermes_cli.plugins import PluginContext
    import inspect
    assert "gateway_handler" in inspect.signature(PluginContext.register_command).parameters


def test_telegram_callback_data_uses_utf8_byte_boundary():
    assert len(validate_telegram_callback_data("x" * 64)) == 64
    with pytest.raises(ValueError):
        validate_telegram_callback_data("x" * 65)


def test_plugin_manager_unload_invalidates_stale_cards_before_reload():
    from hermes_cli.plugins import PluginManager
    seen = []
    manager = PluginManager()
    registry = manager.plugin_card_registry
    registry.add(PluginCard("stale-card", "Task", "Body", (PluginCardButton("continue", "Continue"),), "42", "7", "s"),
                 action=lambda *_: seen.append(True))
    manager.unload()
    reloaded = manager.plugin_card_registry
    assert reloaded is not registry
    with pytest.raises(KeyError):
        registry.dispatch("stale-card", "continue", PluginCommandContext(None, None, "s", "42", "7", "9"))
    assert seen == []


def test_card_manager_binding_is_exact_and_one_shot():
    from plugins.platforms.telegram.adapter import TelegramAdapter
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._plugin_card_managers = {}
    originating = object()
    ambient_replacement = object()
    adapter.bind_plugin_card_manager("opaque-token", originating)
    adapter.bind_plugin_card_manager("replacement-token", ambient_replacement)
    assert adapter.get_plugin_card_manager("opaque-token") is originating
    assert adapter.get_plugin_card_manager("opaque-token") is originating
    assert adapter.pop_plugin_card_manager("opaque-token") is originating
    assert adapter.pop_plugin_card_manager("opaque-token") is None
    assert adapter.pop_plugin_card_manager("replacement-token") is ambient_replacement
