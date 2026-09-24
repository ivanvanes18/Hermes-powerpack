"""A delegated same-provider child must see credentials added after its parent.

Production incident: a long-lived Telegram parent session was created while
``openai-codex`` had a single OAuth row; a second OAuth row was added later.
``_resolve_child_credential_pool`` returned the parent's pool object
unchanged, so a child spawned afterwards still saw ONE row: on
``usage_limit_reached`` it logged "marking reina-main exhausted", then "no
available entries", then activated the configured OpenRouter fallback — while
a sibling child created fresh at the same time (its ``load_pool`` ran after
the second row landed) correctly logged "rotated to ivanvanes18".

The child must keep sharing the parent's pool OBJECT (so leases and the
rotation cursor stay coordinated) and see the newly added row.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

PROVIDER = "openai-codex"


def _row(entry_id: str, label: str, token: str, priority: int) -> dict:
    return {
        "id": entry_id,
        "label": label,
        "auth_type": "oauth",
        "priority": priority,
        "source": "manual",
        "access_token": token,
        "refresh_token": f"refresh-{entry_id}",
    }


@pytest.fixture(autouse=True)
def _offline_codex_quota_probe(monkeypatch):
    """Keep the exhausted-entry cooldown check off the network and binding."""
    import hermes_cli.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_probe_codex_quota_restored", lambda *a, **k: False)


def _parent(pool):
    """Minimal stand-in for the long-lived parent agent."""
    return SimpleNamespace(provider=PROVIDER, base_url=None, _credential_pool=pool)


def _usage_limit_context() -> dict:
    return {
        "reason": "usage_limit_reached",
        "message": "You've hit your usage limit.",
        "reset_at": time.time() + 3600,
    }


def test_child_rotates_to_credential_added_after_parent_was_created():
    from agent.credential_pool import load_pool
    from hermes_cli.auth import write_credential_pool
    from tools.delegate_tool_config import _resolve_child_credential_pool

    write_credential_pool(PROVIDER, [_row("cred-1", "reina-main", "tok-reina", 0)])
    parent_pool = load_pool(PROVIDER)
    assert [e.label for e in parent_pool.entries()] == ["reina-main"]
    # The parent is mid-session on its only credential.
    assert parent_pool.acquire_lease("cred-1") == "cred-1"

    # A second OAuth account is added AFTER the parent agent was built.
    write_credential_pool(
        PROVIDER,
        [
            _row("cred-1", "reina-main", "tok-reina", 0),
            _row("cred-2", "ivanvanes18", "tok-ivan", 1),
        ],
    )

    child_pool = _resolve_child_credential_pool(PROVIDER, _parent(parent_pool))

    assert child_pool is parent_pool, "child must keep sharing the parent's pool object"
    assert [e.label for e in child_pool.entries()] == ["reina-main", "ivanvanes18"]

    rotated = child_pool.mark_exhausted_and_rotate(
        status_code=429, error_context=_usage_limit_context(), credential_id="cred-1",
    )
    assert rotated is not None, "child fell through to the external fallback instead of rotating"
    assert rotated.label == "ivanvanes18"


def test_resolution_preserves_parent_pool_when_store_reload_fails(monkeypatch):
    """A failed store reload keeps the existing parent pool (and its rows)."""
    import agent.credential_pool as pool_mod
    from agent.credential_pool import load_pool
    from hermes_cli.auth import write_credential_pool
    from tools.delegate_tool_config import _resolve_child_credential_pool

    write_credential_pool(PROVIDER, [_row("cred-1", "reina-main", "tok-reina", 0)])
    parent_pool = load_pool(PROVIDER)

    def _boom(_provider):
        raise RuntimeError("auth store unreadable")

    monkeypatch.setattr(pool_mod, "load_pool", _boom)

    child_pool = _resolve_child_credential_pool(PROVIDER, _parent(parent_pool))

    assert child_pool is parent_pool
    assert [e.id for e in child_pool.entries()] == ["cred-1"]


def test_resolution_preserves_parent_pool_when_reconciliation_raises():
    """Reconciliation is best-effort: a raising pool must not collapse to None."""
    from tools.delegate_tool_config import _resolve_child_credential_pool

    class _RaisingPool:
        def reconcile_with_store(self):
            raise RuntimeError("pool lock unavailable")

    parent_pool = _RaisingPool()

    assert _resolve_child_credential_pool(PROVIDER, _parent(parent_pool)) is parent_pool
