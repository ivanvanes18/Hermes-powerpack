"""Preferred-credential selection: a caller may pin one exact pool row.

Telegram topics declare an affinity for a CredentialPool row by non-secret
``credential_id``. The pool must honour that row only while it is genuinely
available (same cooldown-clear/resync/refresh rules as an unpinned select),
fall back to the configured strategy otherwise, and leave the strategy's own
bookkeeping untouched for callers that pin nothing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace

import pytest

import agent.credential_pool as credential_pool_mod
from agent.credential_pool import (
    AUTH_TYPE_OAUTH, CredentialPool, PooledCredential, credential_id_fingerprint, load_pool,
)
from hermes_cli.auth import write_credential_pool


@pytest.fixture(autouse=True)
def _reset_preference_warn_latch():
    """``_PREFERENCE_WARNED`` is process-wide warn-once state; keep tests independent."""
    credential_pool_mod._PREFERENCE_WARNED.clear()
    yield
    credential_pool_mod._PREFERENCE_WARNED.clear()


def _rows(provider: str, count: int = 3) -> list[PooledCredential]:
    return [
        PooledCredential(
            provider=provider, id=f"row{i}", label=f"account{i}", source="manual",
            auth_type="api_key", access_token=f"fixture-key-{i}", priority=i,
        )
        for i in range(count)
    ]


def _pool(provider: str = "openrouter", *, count: int = 3, strategy: str = "fill_first") -> CredentialPool:
    rows = _rows(provider, count)
    write_credential_pool(provider, [row.to_dict() for row in rows])
    pool = CredentialPool(provider, rows)
    pool._strategy = strategy
    return pool


def _bench(pool: CredentialPool, entry_id: str, **updates) -> None:
    """Rewrite one row in place (cooldown / DEAD), bypassing selection."""
    pool._entries = [
        replace(row, **updates) if row.id == entry_id else row for row in pool._entries
    ]


# ── the preferred row is used when it is healthy ────────────────────────────


def test_preferred_healthy_row_wins_over_strategy_order():
    """fill_first would hand back row0; the pinned row2 must win instead."""
    pool = _pool()
    assert pool.select().id == "row0"

    entry = pool.select("row2")

    assert entry is not None
    assert entry.id == "row2"
    assert entry.runtime_api_key == "fixture-key-2"
    assert pool.current().id == "row2"


def test_preferred_row_counts_its_own_selection():
    pool = _pool()
    pool.select("row2")
    counts = {row.id: row.request_count for row in pool.entries()}
    assert counts == {"row0": 0, "row1": 0, "row2": 1}


# ── unavailable / unknown preferences fall back ─────────────────────────────


def test_exhausted_preferred_row_falls_back_to_strategy():
    pool = _pool()
    _bench(
        pool, "row2",
        last_status="exhausted", last_status_at=time.time(),
        last_error_code=429, last_error_reset_at=time.time() + 3600,
    )

    entry = pool.select("row2")

    assert entry is not None
    assert entry.id == "row0", "a benched preference must not starve the topic"


def test_dead_preferred_row_falls_back_to_strategy():
    pool = _pool()
    _bench(pool, "row2", last_status="dead", last_status_at=time.time(), last_error_code=401)

    entry = pool.select("row2")

    assert entry is not None
    assert entry.id == "row0"


def test_unknown_preferred_id_falls_back_to_strategy():
    pool = _pool()

    entry = pool.select("row-that-never-existed")

    assert entry is not None
    assert entry.id == "row0"


def test_expired_cooldown_on_preferred_row_is_cleared_like_a_normal_select():
    """The preference goes through the same cooldown-clear pass as selection."""
    pool = _pool()
    _bench(
        pool, "row2",
        last_status="exhausted", last_status_at=time.time() - 7200,
        last_error_code=429, last_error_reset_at=time.time() - 60,
    )

    entry = pool.select("row2")

    assert entry is not None and entry.id == "row2"
    assert entry.last_status == "ok"


def test_preference_on_an_empty_pool_still_returns_none():
    pool = _pool(count=0)
    assert pool.select("row0") is None


# ── unpinned selection is byte-for-byte unchanged ───────────────────────────


@pytest.mark.parametrize("strategy", ["fill_first", "round_robin", "least_used"])
def test_absent_preference_preserves_strategy(strategy):
    baseline = _pool(strategy=strategy)
    pinned_pool = _pool(strategy=strategy)

    assert [baseline.select().id for _ in range(4)] == [
        pinned_pool.select(None).id for _ in range(4)
    ]
    assert [row.id for row in baseline.entries()] == [row.id for row in pinned_pool.entries()]


def test_preferred_selection_does_not_reorder_the_pool():
    """round_robin rotates priorities on an unpinned select; a pinned one must not."""
    pool = _pool(strategy="round_robin")
    before = [(row.id, row.priority) for row in pool.entries()]

    pool.select("row2")

    assert [(row.id, row.priority) for row in pool.entries()] == before

    # …and the strategy still rotates for the next unpinned caller.
    pool.select()
    assert [(row.id, row.priority) for row in pool.entries()] != before


def test_preferred_selection_does_not_skew_least_used_for_other_callers():
    pool = _pool(strategy="least_used")
    pool.select("row2")
    # row0/row1 are still at zero uses, so least_used picks among them, not row2.
    assert pool.select().id in {"row0", "row1"}


# ── a pinned row waiting on a deferred refresh ──────────────────────────────
#
# openai-codex / xai-oauth defer their proactive OAuth refresh OUTSIDE the pool
# lock, so the first availability pass can only choose among rows that were NOT
# queued for one. A pin that was queued must not lose the turn to a fallback.


def _codex_rows() -> list[PooledCredential]:
    return [
        PooledCredential(
            provider="openai-codex", id="pinned", label="pinned-account",
            auth_type=AUTH_TYPE_OAUTH, priority=0, source="device_code",
            access_token="at-stale", refresh_token="rt-pinned", expires_at_ms=1,
        ),
        PooledCredential(
            provider="openai-codex", id="fallback", label="fallback-account",
            auth_type=AUTH_TYPE_OAUTH, priority=1, source="device_code",
            access_token="at-other", refresh_token="rt-other", expires_at_ms=2**53,
        ),
    ]


def _codex_pool(monkeypatch, *, refresh_succeeds: bool) -> CredentialPool:
    pool = CredentialPool("openai-codex", _codex_rows())
    monkeypatch.setattr(pool, "_persist", lambda **_kw: None)
    # Only the pinned row is stale, so only it is deferred into pending_refresh.
    monkeypatch.setattr(pool, "_entry_needs_refresh", lambda e: e.access_token == "at-stale")

    def _fake_refresh(entry, *, force):
        if not refresh_succeeds:
            return None
        refreshed = replace(entry, access_token="at-refreshed", expires_at_ms=2**53)
        pool._replace_entry(entry, refreshed)
        return refreshed

    monkeypatch.setattr(pool, "_refresh_entry", _fake_refresh)
    return pool


def test_preferred_row_deferred_for_refresh_serves_the_current_selection(monkeypatch):
    pool = _codex_pool(monkeypatch, refresh_succeeds=True)

    entry = pool.select("pinned")

    assert entry is not None
    assert entry.id == "pinned", "the refreshed pin must serve THIS selection, not the next one"
    assert entry.access_token == "at-refreshed"
    assert pool.current().id == "pinned"
    # The fallback was chosen provisionally and then superseded; it must not
    # carry the weight of a request it never served (least_used's baseline).
    assert {row.id: row.request_count for row in pool.entries()} == {"pinned": 1, "fallback": 0}


def test_preferred_row_whose_deferred_refresh_fails_falls_back_once(monkeypatch):
    pool = _codex_pool(monkeypatch, refresh_succeeds=False)

    entry = pool.select("pinned")

    assert entry is not None
    assert entry.id == "fallback", "an unrefreshable pin must not starve the caller"
    assert {row.id: row.request_count for row in pool.entries()} == {"pinned": 0, "fallback": 1}


def test_unpinned_select_keeps_its_single_deferred_refresh_pass(monkeypatch):
    """No pin: the historical "retry only when nothing was available" rule stands."""
    pool = _codex_pool(monkeypatch, refresh_succeeds=True)

    entry = pool.select()

    assert entry is not None and entry.id == "fallback"
    assert {row.id: row.request_count for row in pool.entries()} == {"pinned": 0, "fallback": 1}


# ── diagnostics carry a fingerprint, never the id/label/token ───────────────


def _warnings(caplog) -> str:
    return " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def test_unknown_preference_logs_a_fingerprint_not_the_raw_id(caplog):
    pool = _pool()
    unknown = "cred-pasted-by-mistake-0123456789"
    with caplog.at_level(logging.WARNING, logger="agent.credential_pool"):
        assert pool.select(unknown).id == "row0"

    logged = _warnings(caplog)
    assert unknown not in logged, "a configured id may be a mis-pasted secret; never echo it"
    assert credential_id_fingerprint(unknown) in logged
    assert "openrouter" in logged


def test_unavailable_preference_logs_neither_label_nor_token(caplog):
    pool = _pool()
    _bench(
        pool, "row2",
        last_status="exhausted", last_status_at=time.time(),
        last_error_code=429, last_error_reset_at=time.time() + 3600,
    )
    with caplog.at_level(logging.WARNING, logger="agent.credential_pool"):
        pool.select("row2")

    logged = _warnings(caplog)
    assert "row2" not in logged
    assert "account2" not in logged, "labels are user-chosen and may name an account"
    assert "fixture-key-2" not in logged


def test_preference_warning_fires_once_per_process(caplog):
    pool = _pool()
    with caplog.at_level(logging.WARNING, logger="agent.credential_pool"):
        for _ in range(3):
            pool.select("nope")
    assert sum(1 for r in caplog.records if r.levelno >= logging.WARNING) == 1


def test_fingerprint_is_bounded_and_one_way():
    fingerprint = credential_id_fingerprint("cred-alpha")
    assert 0 < len(fingerprint) <= 16
    assert "cred-alpha" not in fingerprint
    assert fingerprint == credential_id_fingerprint("cred-alpha")
    assert fingerprint != credential_id_fingerprint("cred-beta")


# ── per-topic isolation: what IS local, and what is NOT ─────────────────────


def test_rotation_is_local_in_memory_but_the_exhaustion_is_account_wide(tmp_path, monkeypatch):
    """Both halves of the locality contract, stated exactly.

    LOCAL: the cached pool object another topic is already holding keeps its own
    rows and its own cursor — one topic's 429 never rewrites another topic's
    in-memory selection.

    NOT LOCAL: the failed row's exhaustion is written to the shared auth store,
    so any pool loaded AFTERWARDS — including the next turn of any topic —
    observes that row as benched. Credentials are an account-wide resource; a
    rate limit is a property of the credential, not of the topic that hit it.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    rows = _rows("deepseek")
    write_credential_pool("deepseek", [row.to_dict() for row in rows])
    topic_a = CredentialPool("deepseek", [replace(row) for row in rows])
    topic_b = CredentialPool("deepseek", [replace(row) for row in rows])

    assert topic_a.select("row0").id == "row0"
    assert topic_b.select("row2").id == "row2"

    rotated = topic_a.mark_exhausted_and_rotate(status_code=429, credential_id="row0")
    assert rotated is not None and rotated.id != "row0"

    # LOCAL half — topic B's already-loaded pool object is untouched.
    assert {row.id: row.last_status for row in topic_b.entries()} == {
        "row0": None, "row1": None, "row2": None,
    }
    assert topic_b.current().id == "row2", "the other topic keeps its own cursor"

    # ACCOUNT-WIDE half — a fresh load sees the persisted bench.
    reloaded = load_pool("deepseek")
    statuses = {row.id: row.last_status for row in reloaded.entries()}
    assert statuses["row0"] == "exhausted"
    assert statuses["row2"] is None
    # A topic pinned to the benched row therefore falls back on its NEXT turn…
    assert reloaded.select("row0").id != "row0"
    # …while a topic pinned elsewhere is unaffected.
    assert load_pool("deepseek").select("row2").id == "row2"
