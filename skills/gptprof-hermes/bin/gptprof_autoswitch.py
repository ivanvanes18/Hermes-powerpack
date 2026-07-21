#!/usr/bin/env python3
"""Auto-switch Hermes Codex OAuth profile when 5h/week quota is near exhaustion.

Silent on no-op. Prints one compact line only when it switches or when the active
profile is below threshold and no healthy alternative exists.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import aiohttp

HERE = Path(__file__).resolve().parent
SEND_BUTTONS = Path(os.getenv("GPTPROF_SEND_BUTTONS", str(HERE / "send_buttons.py")))
HERMES_HOME = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
THRESHOLD = int(os.getenv("GPTPROF_AUTOSWITCH_THRESHOLD", "5"))
LOCK_PATH = Path(
    os.getenv(
        "GPTPROF_AUTOSWITCH_LOCK",
        str(HERMES_HOME / "run" / "gptprof-autoswitch.lock"),
    )
).expanduser()
STATE_PATH = Path(
    os.getenv(
        "GPTPROF_AUTOSWITCH_STATE",
        str(HERMES_HOME / "gptprof" / "autoswitch-state.json"),
    )
).expanduser()
LOCK_HOLDER = threading.local()

spec = importlib.util.spec_from_file_location("gptprof_send_buttons", SEND_BUTTONS)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load {SEND_BUTTONS}")
gptprof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gptprof)  # type: ignore[union-attr]


def pct(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def score(usage: dict[str, Any]) -> int:
    p = pct(usage.get("primary_left"))
    s = pct(usage.get("secondary_left"))
    if p is None or s is None or usage.get("usage_error"):
        return -1
    return min(p, s)


def should_switch(usage: dict[str, Any]) -> bool:
    p = pct(usage.get("primary_left"))
    s = pct(usage.get("secondary_left"))
    if usage.get("usage_error"):
        return True
    return (p is not None and p <= THRESHOLD) or (s is not None and s <= THRESHOLD)


def load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.is_symlink():
            return {}
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    gptprof.save_json(str(STATE_PATH), state)


def profile_catalog(profiles: dict[str, dict[str, Any]]) -> list[tuple[str, str, str]]:
    if hasattr(gptprof, "profile_catalog"):
        return gptprof.profile_catalog(profiles)
    seen: set[str] = set()
    items: list[tuple[str, str, str]] = []
    for slug, plan, model in getattr(gptprof, "PROFILES", []):
        items.append((slug, plan, model))
        seen.add(slug)
    for slug in sorted(profiles):
        if slug not in seen:
            items.append((slug, str(profiles[slug].get("plan") or "OpenAI"), "gpt-5.5"))
    return items


def switch_profile_auth(slug: str, profile: dict[str, Any]) -> None:
    """Switch auth only; do not change model/provider route."""
    gptprof.sync_active_auth(slug, profile)


async def collect_usage(profiles: dict[str, dict[str, Any]], catalog: list[tuple[str, str, str]]) -> dict[str, dict[str, Any]]:
    cache = gptprof.load_cache()
    connector = aiohttp.TCPConnector(limit=6, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for slug, _plan, _model in catalog:
            token = str((profiles.get(slug) or {}).get("access_token") or "")
            if token:
                tasks.append(gptprof.fetch_usage(session, token, slug, cache))
        results = await asyncio.gather(*tasks) if tasks else []
    gptprof.save_cache(cache)
    return {slug: usage for slug, usage in results}


async def main() -> int:
    from hermes_cli import auth as auth_mod

    try:
        lock = auth_mod._file_lock(
            LOCK_PATH,
            LOCK_HOLDER,
            1.0,
            "gptprof autoswitch already running",
        )
        lock.__enter__()
    except TimeoutError:
        return 0
    try:
        profiles = gptprof.overlay_runtime_profiles(gptprof.load_profiles())
        catalog = profile_catalog(profiles)
        current = gptprof.get_current_profile() or (catalog[0][0] if catalog else None)
        if not current:
            print("gptprof-autoswitch: no current profile")
            return 1

        usage_map = await collect_usage(profiles, catalog)
        current_usage = usage_map.get(current, {})
        current_profile = profiles.get(current) or {}
        if current_profile.get("_refresh_error"):
            current_usage = dict(current_usage)
            current_usage["usage_error"] = str(current_profile["_refresh_error"])
        if not should_switch(current_usage):
            return 0

        candidates = []
        for slug, _plan, _model in catalog:
            if slug == current:
                continue
            profile = profiles.get(slug) or {}
            if not profile.get("access_token") or profile.get("_refresh_error"):
                continue
            sc = score(usage_map.get(slug, {}))
            if sc > THRESHOLD:
                candidates.append((sc, slug, profile))
        candidates.sort(reverse=True, key=lambda x: x[0])

        state = load_state()
        now = time.time()
        if not candidates:
            if now - float(state.get("last_no_candidate_at") or 0) > 3600:
                state["last_no_candidate_at"] = now
                save_state(state)
                print(
                    f"⚠️ gptprof autoswitch: {current} below {THRESHOLD}%/error, "
                    "but no healthy alternative profile found."
                )
            return 0

        best_score, target, target_profile = candidates[0]
        switch_profile_auth(target, target_profile)
        state.update({
            "last_switch_at": now,
            "from": current,
            "to": target,
            "target_score": best_score,
            "threshold": THRESHOLD,
            "current_usage": current_usage,
            "target_usage": usage_map.get(target, {}),
        })
        save_state(state)
        tgt = usage_map.get(target, {})
        print(
            f"🔁 gptprof autoswitch: {current} → {target}; "
            f"old 5h={current_usage.get('primary_left')}% week={current_usage.get('secondary_left')}%; "
            f"new 5h={tgt.get('primary_left')}% week={tgt.get('secondary_left')}%; "
            "model unchanged"
        )
        return 0
    finally:
        lock.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
