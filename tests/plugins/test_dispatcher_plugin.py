import json
import sqlite3
import time

import pytest

from gateway.plugin_cards import PluginCardRegistry, PluginCommandContext
from plugins.dispatcher import register


def task(task_id, card_id, session_key="target-session", status="resumable"):
    return {"id": task_id, "card_id": card_id, "topic": f"Task {task_id}", "status": status,
            "done": "verified result", "not_done": "unfinished work", "next_step": "re-check",
            "session_key": session_key, "chat_id": "42", "topic_id": "7", "updated_at": time.time()}


class State:
    def __init__(self, tasks=None): self.data = {"tasks": tasks or []}
    def get(self, key, default=None): return self.data.get(key, default)
    def set(self, key, value): self.data[key] = value


class Ctx:
    def __init__(self, tasks=None): self.state = State(tasks)
    def register_command(self, *args, **kwargs): self.args, self.kwargs = args, kwargs
    def inject_message(self, session_key, content): self.injected = (session_key, content); return True


def test_dispatcher_empty_registry_is_explicit_and_has_no_demo_task():
    ctx = Ctx()
    register(ctx)
    assert ctx.args[1]() == "No dispatcher tasks are registered. Use /dispatcher refresh."


def test_gateway_command_auto_refreshes_canonical_registry_when_state_is_empty(monkeypatch):
    ctx = Ctx()
    register(ctx)
    refreshed = [task("one", "card-one", status="ПРЕРВАНО")]
    monkeypatch.setattr("plugins.dispatcher._refresh", lambda plugin_ctx: (plugin_ctx.state.set("tasks", refreshed) or "ok"))

    cards = ctx.kwargs["gateway_handler"](
        PluginCommandContext(None, None, "dispatcher-session", "42", "7", "9")
    )

    assert [card.card_id for card in cards] == ["card-one"]


def test_dispatcher_renders_all_tasks_as_cards_and_continuation_is_fail_closed():
    ctx = Ctx([task("one", "card-one"), task("two", "card-two", "other-session", "blocked")])
    register(ctx)
    command_ctx = PluginCommandContext(None, None, "dispatcher-session", "42", "7", "9")
    cards = ctx.kwargs["gateway_handler"](command_ctx)
    assert [card.card_id for card in cards] == ["card-one", "card-two"]
    registry = PluginCardRegistry()
    for card in cards:
        registry.add(card)
    assert registry.dispatch("card-one", "continue", command_ctx) == "Continuation queued."
    assert ctx.injected[0] == "target-session"
    assert "verified result" in ctx.injected[1]
    assert "unfinished work" in ctx.injected[1]
    assert "re-check" in ctx.injected[1]
    assert registry.dispatch("card-two", "continue", command_ctx) == "This task is not resumable; no action was taken."
    assert ctx.injected[0] == "target-session"


def test_card_claim_is_one_shot_and_preserves_plugin_context():
    registry = PluginCardRegistry()
    seen = []
    from gateway.plugin_cards import PluginCard, PluginCardButton
    card = PluginCard("card-one", "Task", "body", (PluginCardButton("continue", "Continue"),),
                      "42", "7", "target-session", action=lambda action, context: seen.append((action, context.session_key, context.plugin_context)),
                      plugin_context="plugin")
    registry.add(card)
    context = PluginCommandContext(None, None, "dispatcher-session", "42", "7", "9")
    registry.dispatch("card-one", "continue", context)
    assert seen == [("continue", "target-session", "plugin")]
    try:
        registry.dispatch("card-one", "continue", context)
    except KeyError:
        pass
    else:
        raise AssertionError("card callback was repeatable")


def _write_state_db(home, *, chat_id="42", thread_id="7"):
    home.mkdir(exist_ok=True)
    with sqlite3.connect(home / "state.db") as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, session_key TEXT, chat_id TEXT, thread_id TEXT, started_at REAL, last_activity_at REAL)")
        db.execute("INSERT INTO sessions VALUES ('sid', 'target-session', ?, ?, 1, 2)", (chat_id, thread_id))


def test_refresh_accepts_canonical_wrapped_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    _write_state_db(tmp_path / "home")
    (tmp_path / "home" / "dispatcher").mkdir()
    (tmp_path / "home" / "dispatcher" / "tasks.json").write_text(json.dumps({
        "schema": "dispatcher-task-registry-v1", "tasks": [task("one", "card-one", status="ПРЕРВАНО")],
    }), encoding="utf-8")
    ctx = Ctx()
    register(ctx)
    assert ctx.args[1]("refresh") == "Dispatcher registry refreshed: 1 task(s)."
    assert ctx.state.data["tasks"][0]["status"] == "ПРЕРВАНО"


def test_refresh_rejects_arbitrary_path_and_mapping_mismatch_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    _write_state_db(tmp_path / "home", chat_id="99")
    (tmp_path / "home" / "dispatcher").mkdir()
    canonical = tmp_path / "home" / "dispatcher" / "tasks.json"
    canonical.write_text(json.dumps({"schema": "dispatcher-task-registry-v1", "tasks": [task("one", "card-one")]}), encoding="utf-8")
    arbitrary = tmp_path / "other.json"
    arbitrary.write_text(canonical.read_text(), encoding="utf-8")
    ctx = Ctx([task("old", "old-card")])
    register(ctx)
    assert ctx.args[1](f"refresh {arbitrary}") == "Task old: resumable — next: re-check"
    assert "mapping mismatch" in ctx.args[1]("refresh")
    assert ctx.state.data["tasks"][0]["id"] == "old"


def test_close_persists_the_claimed_task_status():
    ctx = Ctx([task("one", "card-one", status="ПРЕРВАНО")])
    register(ctx)
    command_ctx = PluginCommandContext(None, None, "dispatcher-session", "42", "7", "9")
    card = ctx.kwargs["gateway_handler"](command_ctx)[0]
    registry = PluginCardRegistry()
    registry.add(card)

    assert registry.dispatch("card-one", "close", command_ctx) == "Task closed."
    assert ctx.state.data["tasks"][0]["status"] == "ЗАВЕРШЕНО"


@pytest.mark.parametrize("status", [
    "АКТИВНО", "ПРЕРВАНО", "ПАУЗА", "БЛОКЕР", "ЗАВЕРШЕНО", "ЗАМЕНЕНО", "НЕИЗВЕСТНО",
])
def test_dispatcher_accepts_native_status_vocabulary(status):
    ctx = Ctx([task("one", "card-one", status=status)])
    register(ctx)
    cards = ctx.kwargs["gateway_handler"](
        PluginCommandContext(None, None, "dispatcher-session", "42", "7", "9")
    )
    assert len(cards) == 1
