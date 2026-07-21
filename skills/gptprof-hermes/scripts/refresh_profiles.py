#!/usr/bin/env python3
"""Refresh imported gptprof credentials through Hermes CredentialPool."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any

from agent.credential_pool import PooledCredential, load_pool
from hermes_cli import auth as auth_mod


HERMES_HOME = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
LOCK_PATH = Path(
    os.getenv(
        "GPTPROF_REFRESH_LOCK",
        str(HERMES_HOME / "run" / "gptprof-token-refresh.lock"),
    )
).expanduser()
LOCK_HOLDER = threading.local()


def _token_expiry_date(token: str) -> str:
    try:
        part = token.split(".")[1]
        part += "=" * ((4 - len(part) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part.encode()))
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)):
            return "unknown"
        return dt.datetime.fromtimestamp(exp, tz=dt.timezone.utc).date().isoformat()
    except Exception:
        return "unknown"


def _status(
    entry: PooledCredential | None,
    state: str,
    detail: str | None = None,
) -> dict[str, Any]:
    if entry is None:
        return {"slug": None, "state": state, "detail": detail}
    return {
        "slug": entry.id.split(":", 1)[1],
        "state": state,
        "detail": detail,
        "expires": _token_expiry_date(entry.access_token),
    }


def refresh_profiles(force: bool = False) -> tuple[int, list[dict[str, Any]]]:
    """Refresh every imported gptprof entry using the stable pool lifecycle."""
    pool = load_pool("openai-codex")
    entries = [
        entry
        for entry in pool.entries()
        if entry.source == "manual:device_code" and entry.id.startswith("gptprof:")
    ]
    if not entries:
        return 1, [
            _status(
                None,
                "missing_pool",
                "no imported gptprof credential pool entries",
            )
        ]

    results: list[dict[str, Any]] = []
    had_error = False
    for entry in entries:
        if entry.auth_type != "oauth" or not entry.refresh_token:
            results.append(_status(entry, "error", "refresh token missing"))
            had_error = True
            continue
        if not force and not pool._entry_needs_refresh(entry):
            results.append(_status(entry, "fresh"))
            continue

        refreshed = pool._refresh_entry(entry, force=force)
        if refreshed is None:
            results.append(
                _status(entry, "error", "pool refresh failed; re-auth may be required")
            )
            had_error = True
            continue
        results.append(_status(refreshed, "refreshed"))
    return (1 if had_error else 0), results


def _print_results(results: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for item in results:
        slug = item.get("slug") or "gptprof"
        expires = f" · expires {item['expires']}" if item.get("expires") else ""
        detail = f" · {item['detail']}" if item.get("detail") else ""
        print(f"{slug}: {item['state']}{expires}{detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh imported gptprof credentials through Hermes CredentialPool"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="refresh every imported gptprof entry now",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON status")
    args = parser.parse_args(argv)

    try:
        with auth_mod._file_lock(
            LOCK_PATH,
            LOCK_HOLDER,
            1.0,
            "gptprof pool refresh already running",
        ):
            exit_code, results = refresh_profiles(force=args.force)
    except TimeoutError:
        results = [
            {
                "slug": None,
                "state": "already_running",
                "detail": "gptprof pool refresh already running",
            }
        ]
        exit_code = 0
    _print_results(results, args.json)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
