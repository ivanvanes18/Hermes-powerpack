"""Runtime resolution may pin one credential-pool row by non-secret id.

The gateway uses this to give a Telegram topic an affinity for a specific
pooled credential. The pin must reach the resolved provider's OWN pool and
nothing else: an id that belongs to a different provider is simply not found
there and the normal strategy serves the turn.
"""

from __future__ import annotations

import json

import yaml

PROVIDER = "deepseek"
MODEL = "deepseek-chat"
KEYS = {"row-a": "sk-fake-placeholder-row-a", "row-b": "sk-fake-placeholder-row-b"}


def _write_home(tmp_path, monkeypatch, *, pools: dict | None = None):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": MODEL, "provider": PROVIDER}}), encoding="utf-8")
    pool = pools if pools is not None else {
        PROVIDER: [
            {"id": row_id, "label": row_id, "auth_type": "api_key", "priority": idx,
             "source": "manual", "access_token": token}
            for idx, (row_id, token) in enumerate(KEYS.items())
        ],
    }
    (hermes_home / "auth.json").write_text(
        json.dumps({"version": 1, "providers": {}, "credential_pool": pool}), encoding="utf-8")
    return hermes_home


def _resolve(**kwargs):
    from hermes_cli.runtime_provider import resolve_runtime_provider
    return resolve_runtime_provider(requested=PROVIDER, **kwargs)


def test_no_preference_resolves_the_strategy_row(tmp_path, monkeypatch):
    """Existing callers stay source-compatible and behaviourally unchanged."""
    _write_home(tmp_path, monkeypatch)
    assert _resolve()["api_key"] == KEYS["row-a"]


def test_preferred_credential_id_selects_that_row(tmp_path, monkeypatch):
    _write_home(tmp_path, monkeypatch)
    runtime = _resolve(preferred_credential_id="row-b")
    assert runtime["api_key"] == KEYS["row-b"]
    assert runtime["provider"] == PROVIDER


def test_unknown_preferred_credential_id_falls_back(tmp_path, monkeypatch):
    _write_home(tmp_path, monkeypatch)
    assert _resolve(preferred_credential_id="row-zzz")["api_key"] == KEYS["row-a"]


def test_preference_applies_only_to_the_resolved_providers_own_pool(tmp_path, monkeypatch):
    """An id from another provider's pool must not pull that provider's key in."""
    other_key = "sk-fake-placeholder-other-provider"
    _write_home(tmp_path, monkeypatch, pools={
        PROVIDER: [{"id": "row-a", "label": "row-a", "auth_type": "api_key", "priority": 0,
                    "source": "manual", "access_token": KEYS["row-a"]}],
        "novita": [{"id": "other-row", "label": "other-row", "auth_type": "api_key",
                      "priority": 0, "source": "manual", "access_token": other_key}],
    })

    runtime = _resolve(preferred_credential_id="other-row")

    assert runtime["api_key"] == KEYS["row-a"]
    assert runtime["api_key"] != other_key


def test_preferred_row_in_cooldown_falls_back_without_raising(tmp_path, monkeypatch):
    import time
    _write_home(tmp_path, monkeypatch, pools={
        PROVIDER: [
            {"id": "row-a", "label": "row-a", "auth_type": "api_key", "priority": 0,
             "source": "manual", "access_token": KEYS["row-a"]},
            {"id": "row-b", "label": "row-b", "auth_type": "api_key", "priority": 1,
             "source": "manual", "access_token": KEYS["row-b"],
             "last_status": "exhausted", "last_status_at": time.time(),
             "last_error_code": 429, "last_error_reset_at": time.time() + 3600},
        ],
    })

    assert _resolve(preferred_credential_id="row-b")["api_key"] == KEYS["row-a"]
