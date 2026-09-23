"""Read-only validator for a profile-local dispatcher registry JSON file."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from hermes_constants import get_hermes_home

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_CARD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$")
_REQUIRED = {"id", "card_id", "topic", "status", "done", "not_done", "next_step", "session_key", "chat_id", "topic_id", "updated_at"}


def read_tasks(path: str | Path | None = None) -> list[dict]:
    if path is not None:
        raise ValueError("dispatcher registry path is fixed to $HERMES_HOME/dispatcher/tasks.json")
    path = get_hermes_home() / "dispatcher" / "tasks.json"
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != "dispatcher-task-registry-v1" or not isinstance(data.get("tasks"), list):
        raise ValueError("dispatcher registry must be an object with a tasks list")
    ids: set[str] = set()
    card_ids: set[str] = set()
    for task in data["tasks"]:
        if not isinstance(task, dict) or set(task) != _REQUIRED:
            raise ValueError("each task must match the strict dispatcher schema")
        if not _ID.fullmatch(task["id"]) or not _CARD_ID.fullmatch(task["card_id"]):
            raise ValueError("task id and card_id must be bounded opaque identifiers")
        if task["id"] in ids or task["card_id"] in card_ids:
            raise ValueError("task ids and card_ids must be unique")
        for key in ("topic", "status", "done", "not_done", "next_step", "session_key", "chat_id", "topic_id"):
            if not isinstance(task[key], str) or not task[key].strip():
                raise ValueError(f"{key} must be a nonempty string")
        if not isinstance(task["updated_at"], (int, float)) or task["updated_at"] <= 0:
            raise ValueError("updated_at must be a positive timestamp")
        ids.add(task["id"])
        card_ids.add(task["card_id"])
    db_path = get_hermes_home() / "state.db"
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as conn:
        for task in data["tasks"]:
            row = conn.execute(
                """SELECT chat_id, thread_id FROM sessions WHERE session_key = ?
                   ORDER BY COALESCE(last_activity_at, started_at) DESC, started_at DESC, id DESC LIMIT 1""",
                (task["session_key"],),
            ).fetchone()
            if row is None or str(row[0]) != task["chat_id"] or str(row[1]) != task["topic_id"]:
                raise ValueError(f"session mapping mismatch for {task['session_key']!r}")
    return data["tasks"]
