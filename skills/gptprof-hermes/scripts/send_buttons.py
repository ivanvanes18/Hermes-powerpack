#!/usr/bin/env python3
"""
Public GPT profile card for Hermes.

Shows Codex route, active profile, auth/usage/cache metadata and inline buttons.
The Telegram callback handler lives in the Hermes gateway deployment.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp
import yaml


def _read_env_file_value(key: str) -> str:
    """Read a simple KEY=value from ~/.hermes/.env without exporting secrets."""
    env_path = os.getenv("HERMES_ENV")
    if not env_path:
        home = os.getenv("HERMES_HOME", os.path.expanduser("~/.hermes"))
        env_path = os.path.join(home, ".env")
    try:
        with open(os.path.expanduser(env_path), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _env_or_file(key: str, default: str = "") -> str:
    return os.getenv(key) or _read_env_file_value(key) or default


BOT_AUTH_VALUE = _env_or_file("TELEGRAM_BOT_TOKEN")
TARGET_CHAT_ID = _env_or_file("GPTPROF_CHAT_ID")  # e.g. "123456789"
HERMES_HOME = os.path.expanduser(os.getenv("HERMES_HOME", "~/.hermes"))
AUTH_PATH = os.path.join(HERMES_HOME, "auth.json")
CONFIG_PATH = os.path.join(HERMES_HOME, "config.yaml")
HCP_DIR = os.path.expanduser(
    os.getenv("HERMES_HCP", os.path.join(HERMES_HOME, "gptprof", "profiles"))
)
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
USAGE_TIMEOUT = 8
CACHE_MAX_AGE = 15 * 60
CACHE_PATH = "/tmp/gptprof_usage_cache.json"
ACCESS_REFRESH_SKEW = int(os.getenv("GPTPROF_ACCESS_REFRESH_SKEW", str(48 * 60 * 60)))
# Optional external OpenClaw import is a break-glass path, not the primary refresh path.
INTEL64_OPENCLAW_SYNC = os.getenv("GPTPROF_INTEL64_OPENCLAW_SYNC", "0") == "1"
INTEL64_SSH_TARGET = os.getenv("GPTPROF_INTEL64_SSH_TARGET", "")
INTEL64_OPENCLAW_PROFILES = os.getenv("GPTPROF_INTEL64_OPENCLAW_PROFILES", "~/.openclaw/codex-profiles")
AUTH_LOCK_HOLDER = threading.local()

PROFILES = [
    ("profile1", "Pro", "gpt-5.5"),
    ("profile2", "Pro", "gpt-5.5"),
    ("profile3", "Plus", "gpt-5.4-mini"),
]
DEFAULT_PROFILE_SLUGS = [slug for slug, _plan, _model in PROFILES]

DISPLAY_PRICE = {
    "profile1": "Pro",
    "profile2": "Pro",
    "profile3": "Plus",
}

DEFAULT_PLAN = {
    "profile1": "Pro",
    "profile2": "Pro",
    "profile3": "Plus",
}

PLAN_LABELS = {}

_POOL_SOURCE = "manual:device_code"
_GENERIC_CALLBACK_PREFIX = "pool-"
_CALLBACK_SLUG_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
_TELEGRAM_CALLBACK_MAX_BYTES = 64


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def load_cache() -> dict[str, Any]:
    data = load_json(CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def save_cache(data: dict[str, Any]) -> None:
    save_json(CACHE_PATH, data)


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * ((4 - len(part) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(part.encode()))
    except Exception:
        return {}


def access_token_exp(token: str) -> float:
    exp = _jwt_payload(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else 0.0


def access_token_expiring(token: str, skew: int = ACCESS_REFRESH_SKEW) -> bool:
    exp = access_token_exp(token)
    return not exp or exp <= time.time() + skew


def token_expiry_date(token: str) -> str:
    exp = access_token_exp(token)
    if not exp:
        return "unknown"
    try:
        return dt.datetime.fromtimestamp(exp, tz=dt.timezone.utc).date().isoformat()
    except Exception:
        return "unknown"


def load_profiles() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    try:
        names = sorted(os.listdir(HCP_DIR))
    except Exception:
        names = []
    for fname in names:
        if not fname.endswith(".json"):
            continue
        slug = fname[:-5]
        data = load_json(os.path.join(HCP_DIR, fname), {})
        if isinstance(data, dict):
            profiles[slug] = data
    return profiles


def save_profile(slug: str, data: dict[str, Any]) -> None:
    save_json(os.path.join(HCP_DIR, f"{slug}.json"), data)


def _callback_data(slug: str, model: str, *, generic: bool = False) -> str | None:
    callback_slug = slug or ""
    if not _CALLBACK_SLUG_RE.fullmatch(callback_slug):
        return None
    if generic:
        callback = f"gptprof:pool:{callback_slug}:{model}"
    else:
        callback = f"gptprof:{callback_slug}:{model}"
    return callback if len(callback.encode()) <= _TELEGRAM_CALLBACK_MAX_BYTES else None


def profile_catalog(
    profiles: dict[str, dict[str, Any]],
    generic_model: str = "gpt-5.5",
) -> list[tuple[str, str, str]]:
    seen: set[str] = set()
    items: list[tuple[str, str, str]] = []
    for slug, default_plan, model in PROFILES:
        if slug not in profiles:
            continue
        data = profiles.get(slug) or {}
        if data.get("_pool_entry_ambiguous"):
            continue
        plan = normalize_plan(str(data.get("plan") or default_plan))
        items.append((slug, plan, model))
        seen.add(slug)
    for slug in sorted(profiles):
        if slug in seen:
            continue
        data = profiles.get(slug) or {}
        if data.get("_pool_entry_ambiguous"):
            continue
        plan = normalize_plan(str(data.get("plan") or DEFAULT_PLAN.get(slug) or "OpenAI"))
        is_generic = bool(data.get("_pool_entry_generic")) or (
            data.get("_pool_entry_generic") is None
            and data.get("_pool_entry_id") == slug
        )
        model = str(data.get("model") or (generic_model if is_generic else "gpt-5.5"))
        generic_id = str(data.get("_pool_entry_id") or slug)
        if is_generic and _callback_data(generic_id, model, generic=True) is None:
            continue
        items.append((slug, plan, model))
    return items


def normalize_plan(plan: str) -> str:
    return PLAN_LABELS.get(plan.strip().lower(), plan)


def _pool_entry_is_valid(item: Any) -> bool:
    return bool(isinstance(item, dict) and item.get("source") == _POOL_SOURCE
                and str(item.get("id") or "").strip()
                and str(item.get("access_token") or "").strip()
                and str(item.get("refresh_token") or "").strip())


def _pool_entry_legacy_slug(item: dict[str, Any]) -> str | None:
    entry_id = str(item.get("id") or "")
    if entry_id.startswith("gptprof:"):
        return entry_id.split(":", 1)[1] or None
    profile = item.get("profile")
    return (profile.strip() if item.get("source") == _POOL_SOURCE
            and isinstance(profile, str) and profile.strip() else None)


def _pool_entry_slug(item: dict[str, Any]) -> str | None:
    legacy_slug = _pool_entry_legacy_slug(item)
    if legacy_slug is not None:
        return legacy_slug
    if item.get("source") != _POOL_SOURCE:
        return None
    entry_id = str(item.get("id") or "")
    return f"{_GENERIC_CALLBACK_PREFIX}{entry_id}" if entry_id else None


def _pool_entry_matches(pool: list[Any], slug: str) -> list[dict[str, Any]]:
    entry_id = f"gptprof:{slug}"
    return [
        item
        for item in pool
        if isinstance(item, dict)
        and (
            item.get("id") == entry_id
            or item.get("source") == entry_id
            or (
                item.get("source") == _POOL_SOURCE
                and _pool_entry_legacy_slug(item) == slug
            )
        )
    ]


def _pool_entry_catalog(
    pool: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    valid: list[tuple[dict[str, Any], str | None]] = []
    generic_counts: dict[str, int] = {}
    for item in pool:
        if not isinstance(item, dict) or item.get("source") != _POOL_SOURCE:
            continue
        legacy_slug = _pool_entry_legacy_slug(item)
        if legacy_slug is None:
            entry_id = str(item.get("id") or "").strip()
            if entry_id:
                # Count every generic manual row before checking token
                # completeness so a complete+incomplete duplicate cannot
                # render a callback that the consumer must reject.
                generic_counts[entry_id] = generic_counts.get(entry_id, 0) + 1
        if not _pool_entry_is_valid(item):
            continue
        valid.append((item, legacy_slug))

    catalog: list[tuple[dict[str, Any], str]] = []
    for item, legacy_slug in valid:
        if legacy_slug is None:
            entry_id = str(item.get("id") or "")
            if generic_counts.get(entry_id) != 1:
                continue
        elif len(_pool_entry_matches(pool, legacy_slug)) != 1:
            continue
        slug = _pool_entry_slug(item)
        if slug:
            catalog.append((item, slug))
    return catalog


def _refresh_fingerprint(value: Any) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _refresh_fingerprint_history(import_record: Any) -> list[str]:
    if not isinstance(import_record, dict):
        return []
    fingerprints: list[str] = []
    history = import_record.get("refresh_fingerprints")
    if isinstance(history, list):
        fingerprints.extend(
            str(item) for item in history if isinstance(item, str) and item
        )
    legacy = import_record.get("refresh_fingerprint")
    if isinstance(legacy, str) and legacy:
        fingerprints.append(legacy)
    return list(dict.fromkeys(fingerprints))


def overlay_runtime_profiles(
    profiles: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Overlay pool-owned runtime tokens onto the bootstrap profile catalog."""
    from hermes_cli import auth as auth_mod
    from agent.credential_pool import load_pool

    # Persistently exhausted rows become eligible again after their cooldown.
    # Use the runtime pool lifecycle before reading auth.json so the card and
    # autoswitch never hide a recovered account indefinitely.
    pool_view = load_pool("openai-codex")
    with pool_view._lock:
        pool_view._available_entries(clear_expired=True, refresh=False)

    merged = {
        slug: dict(profile)
        for slug, profile in profiles.items()
        if isinstance(profile, dict)
    }
    for profile in merged.values():
        profile.pop("_pool_entry_ambiguous", None)
    auth_path = Path(AUTH_PATH).expanduser()
    with auth_mod._file_lock(
        auth_path.with_suffix(".lock"),
        AUTH_LOCK_HOLDER,
        auth_mod.AUTH_LOCK_TIMEOUT_SECONDS,
        "Timed out waiting for gptprof auth store lock",
    ):
        auth = auth_mod._load_auth_store(auth_path)

    pool_root = auth.get("credential_pool") if isinstance(auth, dict) else None
    pool = pool_root.get("openai-codex") if isinstance(pool_root, dict) else None
    if not isinstance(pool, list):
        return merged

    for slug in merged:
        if len(_pool_entry_matches(pool, slug)) > 1:
            merged[slug]["_pool_entry_ambiguous"] = True

    for item, slug in _pool_entry_catalog(pool):
        if _pool_entry_legacy_slug(item) is not None and slug not in merged:
            continue
        profile = merged.setdefault(slug, {})
        for key in (
            "access_token",
            "refresh_token",
            "email",
            "plan",
            "last_refresh",
        ):
            if item.get(key) not in (None, ""):
                profile[key] = item[key]
        profile["_pool_entry_id"] = str(item.get("id") or "")
        profile["_pool_entry_generic"] = _pool_entry_legacy_slug(item) is None
        if item.get("label") not in (None, ""):
            profile["_pool_entry_label"] = str(item.get("label"))
        status = str(item.get("last_status") or "").strip().lower()
        if status in {"dead", "exhausted"}:
            profile["_refresh_error"] = (
                item.get("last_error_reason")
                or item.get("last_error_message")
                or status
            )
        else:
            profile.pop("_refresh_error", None)
    return merged


def get_current_profile() -> str | None:
    auth = load_json(AUTH_PATH, {})
    if isinstance(auth, dict):
        pool_root = auth.get("credential_pool")
        pool = pool_root.get("openai-codex") if isinstance(pool_root, dict) else None
        if isinstance(pool, list):
            candidates = [
                (item, slug)
                for item, slug in _pool_entry_catalog(pool)
                if str(item.get("last_status") or "").strip().lower()
                not in {"dead", "exhausted"}
            ]
            if candidates:
                selected, slug = min(
                    candidates,
                    key=lambda pair: int(pair[0].get("priority") or 0),
                )
                return slug
            if pool:
                return None

        gptprof = auth.get("gptprof")
        if isinstance(gptprof, dict):
            active = gptprof.get("active_profile")
            if isinstance(active, str) and active.strip():
                return active.strip()

        codex = auth.get("codex") or {}
        if isinstance(codex, dict):
            active = codex.get("profile")
            if isinstance(active, str) and active.strip():
                return active.strip()
    return None


def sync_active_auth(slug: str, profile: dict[str, Any]) -> None:
    """Import/select one profile while keeping CredentialPool canonical."""
    from hermes_cli import auth as auth_mod
    from agent.credential_pool import load_pool

    auth_path = Path(AUTH_PATH).expanduser()
    bootstrap = load_json(os.path.join(HCP_DIR, f"{slug}.json"), {})
    if not isinstance(bootstrap, dict):
        bootstrap = {}
    bootstrap_tokens = bootstrap if bootstrap.get("refresh_token") else profile
    bootstrap_fingerprint = _refresh_fingerprint(bootstrap_tokens.get("refresh_token"))

    # Reject ambiguous callback identities before taking any write path.
    pre_auth = load_json(AUTH_PATH, {})
    pre_pool_root = pre_auth.get("credential_pool") if isinstance(pre_auth, dict) else None
    pre_pool = pre_pool_root.get("openai-codex") if isinstance(pre_pool_root, dict) else None
    pre_pool = pre_pool if isinstance(pre_pool, list) else []
    if len(_pool_entry_matches(pre_pool, slug)) > 1:
        raise ValueError(f"Profile {slug!r} is ambiguous in CredentialPool")

    # Clear expired cooldowns through the same availability semantics used by
    # runtime selection, without triggering network refresh.
    pool_view = load_pool("openai-codex")
    with pool_view._lock:
        pool_view._available_entries(clear_expired=True, refresh=False)

    with auth_mod._file_lock(
        auth_path.with_suffix(".lock"),
        AUTH_LOCK_HOLDER,
        auth_mod.AUTH_LOCK_TIMEOUT_SECONDS,
        "Timed out waiting for gptprof auth store lock",
    ):
        auth = auth_mod._load_auth_store(auth_path)
        if not isinstance(auth, dict):
            auth = {}

        gptprof = auth.setdefault("gptprof", {})
        if not isinstance(gptprof, dict):
            gptprof = {}
            auth["gptprof"] = gptprof
        imported_profiles = gptprof.setdefault("imported_profiles", {})
        if not isinstance(imported_profiles, dict):
            imported_profiles = {}
            gptprof["imported_profiles"] = imported_profiles
        import_record = imported_profiles.get(slug)
        recorded_fingerprints = _refresh_fingerprint_history(import_record)

        pool_root = auth.setdefault("credential_pool", {})
        if not isinstance(pool_root, dict):
            pool_root = {}
            auth["credential_pool"] = pool_root
        pool = pool_root.get("openai-codex")
        pool = pool if isinstance(pool, list) else []
        entry_id = f"gptprof:{slug}"
        matches = _pool_entry_matches(pool, slug)
        if len(matches) > 1:
            raise ValueError(f"Profile {slug!r} is ambiguous in CredentialPool")
        current_entry = matches[0] if matches else None
        legacy_entry = isinstance(current_entry, dict) and (
            _pool_entry_legacy_slug(current_entry) == slug
        )
        fresh_reauth = bool(
            recorded_fingerprints
            and bootstrap_fingerprint
            and bootstrap_fingerprint not in recorded_fingerprints
        )
        if current_entry is None and recorded_fingerprints and not fresh_reauth:
            raise ValueError(
                f"Profile {slug!r} has only stale bootstrap credentials; re-authenticate it"
            )
        current_status = str(
            current_entry.get("last_status") if isinstance(current_entry, dict) else ""
        ).strip().lower()
        if current_status in {"dead", "exhausted"} and not fresh_reauth:
            raise ValueError(
                f"Profile {slug!r} is unavailable in CredentialPool ({current_status})"
            )
        current_complete = bool(
            isinstance(current_entry, dict)
            and current_entry.get("access_token")
            and current_entry.get("refresh_token")
        )
        if current_entry is not None and not current_complete and not fresh_reauth:
            raise ValueError(
                f"Profile {slug!r} has an incomplete pool entry and stale bootstrap credentials"
            )
        runtime_tokens = (
            current_entry
            if current_complete and not fresh_reauth
            else bootstrap_tokens
        )
        if not runtime_tokens.get("access_token") or not runtime_tokens.get("refresh_token"):
            raise ValueError(f"Profile {slug!r} is missing a complete OAuth token pair")

        selected_entry = (
            dict(current_entry)
            if isinstance(current_entry, dict) and not fresh_reauth
            else {}
        )
        if legacy_entry or current_entry is None or fresh_reauth:
            selected_entry.update({"id": entry_id, "source": _POOL_SOURCE, "profile": slug, "label": slug})
        selected_entry.update(
            {
                "provider": "openai-codex",
                "auth_type": "oauth",
                "email": runtime_tokens.get("email") or profile.get("email"),
                "plan": runtime_tokens.get("plan") or profile.get("plan"),
                "access_token": runtime_tokens.get("access_token"),
                "refresh_token": runtime_tokens.get("refresh_token"),
                "base_url": auth_mod.DEFAULT_CODEX_BASE_URL,
                "priority": 0,
            }
        )
        if not current_entry or fresh_reauth:
            selected_entry.update({"last_status": "ok", "last_status_at": time.time()})

        remaining_pool = []
        for item in pool:
            if not isinstance(item, dict):
                continue
            if item is current_entry:
                continue
            if item.get("priority") == 0:
                item = {**item, "priority": 10}
            remaining_pool.append(item)
        pool_root["openai-codex"] = [selected_entry, *remaining_pool]

        gptprof["active_profile"] = slug
        if bootstrap_fingerprint:
            if bootstrap_fingerprint not in recorded_fingerprints:
                recorded_fingerprints.append(bootstrap_fingerprint)
            imported_profiles[slug] = {
                "refresh_fingerprints": recorded_fingerprints,
            }

        auth_mod._save_auth_store(auth, target_path=auth_path)


def sync_from_intel64_openclaw(
    profiles: dict[str, dict[str, Any]],
    cache: dict[str, Any],
    only_slugs: list[str] | None = None,
    force: bool = False,
) -> list[str]:
    """Import OpenClaw profile tokens from an optional external host, if reachable."""
    if not INTEL64_OPENCLAW_SYNC and not force:
        return []
    if not INTEL64_SSH_TARGET:
        return []
    wanted = set(only_slugs or [])
    slugs = [slug for slug, _plan, _model in PROFILES if not wanted or slug in wanted]
    if not slugs:
        return []
    script = f"""
import json
from pathlib import Path
base = Path({os.path.expanduser(INTEL64_OPENCLAW_PROFILES)!r})
out = {{}}
for slug in {slugs!r}:
    p = base / slug / 'auth.json'
    try:
        out[slug] = json.loads(p.read_text())
    except Exception as exc:
        out[slug] = {{'_error': type(exc).__name__}}
print(json.dumps(out))
""".strip()
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", INTEL64_SSH_TARGET, f"python3 - <<'PY'\n{script}\nPY"],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        remote_profiles = json.loads(result.stdout)
    except Exception:
        return []

    updated: list[str] = []
    for slug in slugs:
        remote = remote_profiles.get(slug)
        if not isinstance(remote, dict) or remote.get("_error"):
            continue
        tokens = remote.get("tokens") if isinstance(remote.get("tokens"), dict) else remote
        access_token = tokens.get("access_token") or remote.get("accessToken")
        refresh_token = tokens.get("refresh_token") or remote.get("refreshToken")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            continue
        local = profiles.get(slug) or {}
        local_exp = access_token_exp(str(local.get("access_token") or ""))
        remote_exp = access_token_exp(access_token)
        # Pull when intel64 has a fresher token or local token is absent/expiring.
        if remote_exp <= local_exp + 60 and not access_token_expiring(str(local.get("access_token") or "")):
            continue
        local.update({
            "profile": slug,
            "email": remote.get("email") or local.get("email") or f"{slug}@gmail.com",
            "plan": local.get("plan") or DEFAULT_PLAN.get(slug, "Codex"),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "last_refresh": remote.get("last_refresh") or local.get("last_refresh"),
            "source": "intel64-openclaw",
            "synced_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        local.pop("_refresh_error", None)
        profiles[slug] = local
        save_profile(slug, local)
        sync_active_auth(slug, local)
        cache.pop(slug, None)
        updated.append(slug)
    return updated


def get_current_model() -> str:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        model = cfg.get("model", "minimax")
        if isinstance(model, dict):
            model = model.get("default") or model.get("model") or "minimax"
        return str(model)
    except Exception:
        return "minimax"


def pct_left(window: dict[str, Any] | None) -> int | None:
    if not isinstance(window, dict):
        return None
    used = window.get("used_percent")
    if not isinstance(used, (int, float)):
        return None
    return max(0, min(100, 100 - int(used)))


def window_reset_at(window: dict[str, Any] | None) -> float | None:
    if not isinstance(window, dict):
        return None
    reset_at = window.get("reset_at") or window.get("resets_at")
    if isinstance(reset_at, (int, float)):
        return float(reset_at)
    seconds = (
        window.get("seconds_until_reset")
        or window.get("reset_after_seconds")
        or window.get("reset_in_seconds")
    )
    if isinstance(seconds, (int, float)):
        return time.time() + float(seconds)
    return None


def window_label(window: dict[str, Any] | None) -> str:
    if not isinstance(window, dict):
        return "unknown"
    try:
        seconds = int(window.get("limit_window_seconds"))
    except (TypeError, ValueError):
        return "unknown"
    if seconds <= 0:
        return "unknown"
    if seconds == 7 * 24 * 60 * 60:
        return "Week"
    for unit, suffix in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds % unit == 0:
            return f"{seconds // unit}{suffix}"
    return f"{seconds}s"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    if seconds <= 0:
        return "expired"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m" if minutes else "<1m"


def usage_error_text(usage_error: Any) -> str | None:
    if not usage_error:
        return None
    error = str(usage_error)
    if "token_revoked" in error:
        return "token revoked · new auth needed"
    if "token_expired" in error:
        return "token expired · new auth needed"
    return "usage unavailable"


def reset_text(reset_at: float | None, usage_error: str | None = None) -> str:
    if isinstance(reset_at, (int, float)):
        return format_duration(float(reset_at) - time.time())
    safe_usage_error = usage_error_text(usage_error)
    if safe_usage_error:
        return safe_usage_error
    return "unknown"



def cache_label(fetched_at: float | None) -> str:
    if not fetched_at:
        return "unknown"
    age = max(0, time.time() - float(fetched_at))
    if age < 10:
        label = "just now"
    elif age < 60:
        label = f"{int(age)}s ago"
    elif age < 3600:
        label = f"{int(age // 60)}m ago"
    else:
        label = f"{int(age // 3600)}h ago"
    if age > CACHE_MAX_AGE:
        label += " · stale"
    return label


def parse_usage(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    rl = payload.get("rate_limit") or {}
    primary = rl.get("primary_window") or payload.get("primary_window")
    secondary = rl.get("secondary_window") or payload.get("secondary_window")
    p = pct_left(primary)
    s = pct_left(secondary)
    p_reset = window_reset_at(primary)
    s_reset = window_reset_at(secondary)
    windows = [
        {"label": window_label(window), "left": left, "reset": reset}
        for window, left, reset in ((primary, p, p_reset), (secondary, s, s_reset))
        if isinstance(window, dict) and window
    ]
    fetched = payload.get("_fetched")
    return {
        "primary_left": p,
        "secondary_left": s,
        "primary_reset": p_reset,
        "secondary_reset": s_reset,
        "primary_label": window_label(primary),
        "secondary_label": window_label(secondary),
        "windows": windows,
        "cache": cache_label(fetched if isinstance(fetched, (int, float)) else None),
    }


async def fetch_usage(session: aiohttp.ClientSession, token: str, slug: str, cache: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    cached = cache.get(slug, {}) if isinstance(cache.get(slug), dict) else {}
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/",
    }
    try:
        async with session.get(USAGE_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=USAGE_TIMEOUT)) as r:
            if r.status == 200:
                data = await r.json()
                data["_fetched"] = time.time()
                cache[slug] = data
                return slug, parse_usage(data)
            parsed = parse_usage(cached)
            try:
                err_payload = await r.json(content_type=None)
                err = err_payload.get("error") if isinstance(err_payload, dict) else None
                code = err.get("code") if isinstance(err, dict) else None
                message = err.get("message") if isinstance(err, dict) else None
                if code:
                    parsed["usage_error"] = str(code)
                elif message:
                    parsed["usage_error"] = str(message)[:80]
                else:
                    parsed["usage_error"] = f"usage {r.status}"
            except Exception:
                parsed["usage_error"] = f"usage {r.status}"
            return slug, parsed
    except Exception as exc:
        parsed = parse_usage(cached)
        parsed["usage_error"] = f"usage {type(exc).__name__}"
        return slug, parsed
    return slug, parse_usage(cached)


def dollar_label(slug: str, plan: str) -> str:
    plan = normalize_plan(plan)
    if "$" in plan:
        return plan.split("$", 1)[-1].join(["$", ""])
    if plan:
        return plan
    return DISPLAY_PRICE.get(slug) or plan


def pct_text(value: int | None) -> str:
    return "—" if value is None else f"{value}%"


def profile_block(slug: str, plan: str, data: dict[str, Any], usage: dict[str, Any], active: bool) -> str:
    marker = "✅" if active else "▪️"
    status = " · active" if active else ""
    display_name = str(data.get("_pool_entry_label") or slug)
    plan = normalize_plan(plan)
    refresh_error = data.get("_refresh_error")
    if refresh_error == "refresh_token_reused":
        refresh = "refresh reused · new auth needed"
    elif refresh_error:
        refresh = "refresh failed · re-auth needed"
    else:
        refresh = "refresh ok" if data.get("refresh_token") else "refresh missing"
    expiry = token_expiry_date(str(data.get("access_token") or ""))
    primary_left = usage.get("primary_left")
    secondary_left = usage.get("secondary_left")
    usage_error = str(usage.get("usage_error") or "") or None
    primary_reset = reset_text(usage.get("primary_reset"), usage_error)
    secondary_reset = reset_text(usage.get("secondary_reset"), usage_error)
    lines = [
        f"{marker} {display_name} [{dollar_label(slug, plan)}]{status}",
        f"🔐 {refresh} · expires {expiry}",
    ]
    windows = usage.get("windows")
    if isinstance(windows, list) and windows:
        for window in windows:
            if not isinstance(window, dict):
                continue
            label = str(window.get("label") or "unknown")
            prefix = "📅" if label.casefold() == "week" else "📊"
            lines.append(
                f"{prefix} {label}: {pct_text(window.get('left'))} left · "
                f"reset {reset_text(window.get('reset'), usage_error)}"
            )
    else:
        lines.append(
            f"📊 5h: {pct_text(primary_left)} left · reset {primary_reset}",
        )
        lines.append(
            f"📅 Week: {pct_text(secondary_left)} left · reset {secondary_reset}",
        )
    lines.append(f"🕒 Cache: {usage.get('cache') or 'unknown'}")
    safe_usage_error = usage_error_text(usage_error)
    if safe_usage_error:
        lines.append(f"⚠️ Usage API: {safe_usage_error}")
    return "\n".join(lines)


def route_model_label(model: str) -> str:
    if model.startswith("openai/"):
        return model
    return f"openai/{model}" if model.startswith(("gpt-", "o")) else model


def _target_chat_id() -> str:
    """Resolve per-invocation routing before the manual static fallback."""
    return (
        os.getenv("HERMES_QUICK_CHAT_ID")
        or os.getenv("GPTPROF_CHAT_ID")
        or TARGET_CHAT_ID
    )


def _target_thread_id() -> int | None:
    raw = os.getenv("HERMES_QUICK_THREAD_ID")
    if not raw:
        return None
    try:
        thread_id = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return thread_id if thread_id > 1 else None


async def main() -> None:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

    bot = Bot(token=BOT_AUTH_VALUE)
    cache = load_cache()
    profiles = overlay_runtime_profiles(load_profiles())
    sync_from_intel64_openclaw(profiles, cache)
    profiles = overlay_runtime_profiles(profiles)
    current_model = get_current_model()
    catalog = profile_catalog(profiles, generic_model=current_model)
    current_slug = get_current_profile()
    if current_slug not in {slug for slug, _plan, _model in catalog}:
        current_slug = ""
    connector = aiohttp.TCPConnector(limit=6, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for slug, _plan, _model in catalog:
            access_value = str((profiles.get(slug) or {}).get("access_token") or "")
            if access_value:
                tasks.append(fetch_usage(session, access_value, slug, cache))
        results = await asyncio.gather(*tasks) if tasks else []
    save_cache(cache)
    usage_map = {slug: usage for slug, usage in results}

    current_data = profiles.get(current_slug) or {}
    current_display = (
        str(current_data.get("_pool_entry_label") or current_slug)
        if current_data.get("_pool_entry_generic")
        or current_slug.startswith(_GENERIC_CALLBACK_PREFIX)
        else current_slug
    )
    lines = [
        "✅ Autoswitch: no switch needed",
        f"🤖 GPT profile: {current_display}",
        "🧪 Route: native Codex trial",
        f"🧠 Model: {route_model_label(current_model)}",
        "⚙️ Runtime: codex",
        f"↪️ Pi fallback available: openai-codex/{current_model} · runtime=pi",
        "",
    ]

    blocks = []
    for slug, plan, _model in catalog:
        blocks.append(profile_block(slug, plan, profiles.get(slug, {}), usage_map.get(slug, {}), slug == current_slug))
    text = "\n\n".join(["\n".join(lines).rstrip(), *blocks])

    profile_buttons = []
    for slug, plan, model in catalog:
        usage = usage_map.get(slug, {})
        week = usage.get("secondary_left")
        for window in usage.get("windows") or []:
            if (
                isinstance(window, dict)
                and str(window.get("label") or "").casefold() == "week"
            ):
                week = window.get("left")
                break
        symbol = "✓" if slug == current_slug else ("⚠" if isinstance(week, int) and week <= 0 else "↔")
        profile_data = profiles.get(slug) or {}
        display_name = str(profile_data.get("_pool_entry_label") or slug)
        is_generic = bool(profile_data.get("_pool_entry_generic")) or (
            profile_data.get("_pool_entry_generic") is None
            and profile_data.get("_pool_entry_id") == slug
        )
        callback_slug = (
            str(profile_data.get("_pool_entry_id") or slug)
            if is_generic
            else slug
        )
        callback = _callback_data(callback_slug, model, generic=is_generic)
        if callback is None:
            continue
        btn_text = f"{symbol} {pct_text(week)} {display_name} [{dollar_label(slug, plan)}]"
        profile_buttons.append(InlineKeyboardButton(btn_text, callback_data=callback))

    rows = [profile_buttons[i:i + 2] for i in range(0, len(profile_buttons), 2)]
    rows.append([
        InlineKeyboardButton("🔄 Usage", callback_data="gptprof:refresh"),
        InlineKeyboardButton("🔁 Autoswitch", callback_data="gptprof:autoswitch"),
    ])
    keyboard = InlineKeyboardMarkup(rows)

    send_kwargs = {"chat_id": int(_target_chat_id()), "text": text,
                   "reply_markup": keyboard, "disable_web_page_preview": True}
    thread_id = _target_thread_id()
    if thread_id is not None:
        send_kwargs["message_thread_id"] = thread_id
    await bot.send_message(**send_kwargs)
    print("GPT Profile card sent.")


if __name__ == "__main__":
    asyncio.run(main())
