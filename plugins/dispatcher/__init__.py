"""Standalone bounded task dispatcher plugin."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from gateway.plugin_cards import PluginCard, PluginCardButton
from hermes_constants import get_hermes_home

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$")
_REQUIRED = {"id", "card_id", "topic", "status", "done", "not_done", "next_step", "session_key", "chat_id", "topic_id", "updated_at"}
_ALLOWED_STATUS = {
    "АКТИВНО", "ПРЕРВАНО", "ПАУЗА", "БЛОКЕР", "ЗАВЕРШЕНО", "ЗАМЕНЕНО", "НЕИЗВЕСТНО",
    # Compatibility for registries produced by the first private pilot.
    "resumable", "blocked", "paused", "unknown", "closed", "ignored",
}


def _validate_tasks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("dispatcher registry must be a JSON array")
    seen_ids: set[str] = set()
    seen_cards: set[str] = set()
    result: list[dict[str, Any]] = []
    for task in value:
        if not isinstance(task, dict) or set(task) != _REQUIRED:
            raise ValueError("dispatcher task has an invalid schema")
        for key in ("id", "card_id", "topic", "status", "done", "not_done", "next_step", "session_key", "chat_id", "topic_id"):
            if not isinstance(task[key], str) or not task[key].strip():
                raise ValueError(f"dispatcher task field {key!r} must be a nonempty string")
        if not _TASK_ID_RE.fullmatch(task["id"]) or not _CARD_ID_RE.fullmatch(task["card_id"]):
            raise ValueError("dispatcher id/card_id must be bounded opaque identifiers")
        if task["id"] in seen_ids or task["card_id"] in seen_cards:
            raise ValueError("dispatcher ids and card_ids must be unique")
        if task["status"] not in _ALLOWED_STATUS:
            raise ValueError("dispatcher task has an unsupported status")
        if not isinstance(task["updated_at"], (int, float)) or task["updated_at"] <= 0:
            raise ValueError("dispatcher updated_at must be a positive timestamp")
        seen_ids.add(task["id"])
        seen_cards.add(task["card_id"])
        result.append(dict(task))
    return result


def _tasks(ctx) -> list[dict[str, Any]]:
    value = ctx.state.get("tasks", [])
    return _validate_tasks(value)


def _save(ctx, tasks: list[dict[str, Any]]) -> None:
    ctx.state.set("tasks", _validate_tasks(tasks))


def _validate_session_mappings(tasks: list[dict[str, Any]]) -> None:
    db_path = get_hermes_home() / "state.db"
    if not db_path.is_file():
        raise ValueError("profile state.db does not exist")
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as conn:
        for task in tasks:
            row = conn.execute(
                """SELECT chat_id, thread_id FROM sessions WHERE session_key = ?
                   ORDER BY COALESCE(last_activity_at, started_at) DESC, started_at DESC, id DESC LIMIT 1""",
                (task["session_key"],),
            ).fetchone()
            if row is None:
                raise ValueError(f"session_key {task['session_key']!r} does not exist in state.db")
            if str(row[0]) != task["chat_id"] or str(row[1]) != task["topic_id"]:
                raise ValueError(f"session mapping mismatch for {task['session_key']!r}")


def _refresh(ctx) -> str:
    path = get_hermes_home() / "dispatcher" / "tasks.json"
    if not path.is_file():
        return "Dispatcher refresh failed: registry path does not exist."
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != "dispatcher-task-registry-v1":
            raise ValueError("dispatcher registry must use schema dispatcher-task-registry-v1")
        tasks = _validate_tasks(payload.get("tasks"))
        _validate_session_mappings(tasks)
        _save(ctx, tasks)
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        return f"Dispatcher refresh failed: {exc}"
    return f"Dispatcher registry refreshed: {len(tasks)} task(s)."


def _render_tasks(ctx, command_args: str = "") -> str:
    if command_args.strip() == "refresh":
        return _refresh(ctx)
    tasks = _tasks(ctx)
    if not tasks:
        return "No dispatcher tasks are registered. Use /dispatcher refresh."
    return "\n".join(f"{task['topic']}: {task['status']} — next: {task['next_step']}" for task in tasks)


def register(ctx) -> None:
    def render(command_args: str = "") -> str:
        return _render_tasks(ctx, command_args)

    def gateway_handler(command_context):
        args = ""
        event = command_context.event
        if event is not None and hasattr(event, "get_command_args"):
            args = event.get_command_args().strip()
        if args == "refresh":
            return _refresh(ctx)
        tasks = _tasks(ctx)
        if not tasks:
            return "No dispatcher tasks are registered. Use /dispatcher refresh."
        cards = []
        for task in tasks:
            def action(action_name, card_context, task_id=task["id"]):
                current_tasks = _tasks(ctx)
                current_index = next((i for i, item in enumerate(current_tasks) if item["id"] == task_id), None)
                if current_index is None:
                    return "Task no longer exists."
                current = current_tasks[current_index]
                target = current["session_key"]
                if not target.strip():
                    return "Task has no target session; refusing to dispatch."
                if action_name == "continue":
                    if current["status"] not in {"resumable", "ПРЕРВАНО"}:
                        return "This task is not resumable; no action was taken."
                    prompt = (
                        f"Verified last result: {current['done']}\n"
                        f"Unfinished step: {current['not_done']}\n"
                        f"Next step: {current['next_step']}\n"
                        "Re-check the current state before continuing."
                    )
                    queued = ctx.inject_message(target, prompt)
                    return "Continuation queued." if queued else "Continuation could not be queued."
                if action_name == "decision":
                    def received(text):
                        return ctx.inject_message(target, (
                            f"Verified last result: {current['done']}\n"
                            f"Unfinished step: {current['not_done']}\n"
                            f"Decision/details: {text}\n"
                            "Re-check the current state before applying this input."
                        ))
                    if card_context.request_text:
                        card_context.request_text(card_context.chat_id, card_context.thread_id, received,
                                                  user_id=card_context.user_id, session_key=target)
                    return "Reply in this topic with the decision or details."
                if action_name in {"close", "ignore"}:
                    current["status"] = "ЗАВЕРШЕНО" if action_name == "close" else "ЗАМЕНЕНО"
                    current["updated_at"] = time.time()
                    current_tasks[current_index] = current
                    _save(ctx, current_tasks)
                    return "Task closed." if action_name == "close" else "Task ignored."
                return "Unknown dispatcher action."

            cards.append(PluginCard(
                card_id=task["card_id"], title=task["topic"],
                body=(f"Status: {task['status']}\nDone: {task['done']}\n"
                      f"Not done: {task['not_done']}\nNext: {task['next_step']}"),
                buttons=(PluginCardButton("continue", "Continue"), PluginCardButton("decision", "Provide decision/details"),
                         PluginCardButton("close", "Close"), PluginCardButton("ignore", "Ignore")),
                source_chat_id=command_context.chat_id, source_thread_id=command_context.thread_id,
                session_key=task["session_key"], action=action, source_user_id=command_context.user_id,
                plugin_context=command_context.plugin_context,
            ))
        return cards

    ctx.register_command("dispatcher", render, description="Show interactive task cards",
                         gateway_handler=gateway_handler, args_hint="[refresh]")
