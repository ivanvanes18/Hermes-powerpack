"""``CredentialPool.reconcile_with_store`` — additive admission of new store rows.

A long-lived pool (a parent agent's session, shared with its delegated children)
must pick up a credential added after it was built, WITHOUT losing the live state
that makes sharing the object worthwhile — active leases, the rotation cursor, and
in-memory OAuth tokens that are fresher than what is on disk.
"""

from __future__ import annotations

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


def _loaded_pool(rows: list[dict]):
    from agent.credential_pool import load_pool
    from hermes_cli.auth import write_credential_pool

    write_credential_pool(PROVIDER, rows)
    return load_pool(PROVIDER)


def test_admits_new_row_after_the_existing_ones():
    pool = _loaded_pool([_row("cred-1", "reina-main", "tok-reina", 0)])

    from hermes_cli.auth import write_credential_pool

    write_credential_pool(PROVIDER, [_row("cred-2", "ivanvanes18", "tok-ivan", 1)])

    assert pool.reconcile_with_store() == 1
    entries = pool.entries()
    assert [e.id for e in entries] == ["cred-1", "cred-2"]
    # The credential in use keeps its priority; the newcomer is next in line.
    assert [e.priority for e in entries] == [0, 1]


def test_preserves_active_leases_and_current_cursor():
    pool = _loaded_pool([_row("cred-1", "reina-main", "tok-reina", 0)])
    assert pool.acquire_lease("cred-1") == "cred-1"

    from hermes_cli.auth import write_credential_pool

    write_credential_pool(
        PROVIDER,
        [_row("cred-1", "reina-main", "tok-reina", 0), _row("cred-2", "ivanvanes18", "tok-ivan", 1)],
    )

    assert pool.reconcile_with_store() == 1
    assert pool._active_leases == {"cred-1": 1}
    assert pool._current_id == "cred-1"
    assert pool.current() is not None and pool.current().id == "cred-1"


def test_does_not_overwrite_a_fresher_in_memory_rotation():
    """Disk bytes for a row the pool already holds are never adopted here."""
    pool = _loaded_pool([_row("cred-1", "reina-main", "tok-reina", 0)])
    existing = pool.entries()[0]
    # Model a single-use OAuth rotation that has not reached disk yet.
    pool._adopt(existing, persist=False, access_token="rotated-token")

    from hermes_cli.auth import write_credential_pool

    write_credential_pool(PROVIDER, [_row("cred-2", "ivanvanes18", "tok-ivan", 1)])

    assert pool.reconcile_with_store() == 1
    by_id = {e.id: e for e in pool.entries()}
    assert by_id["cred-1"].access_token == "rotated-token"
    assert by_id["cred-2"].access_token == "tok-ivan"


def test_repeated_reconciliation_does_not_duplicate_rows():
    pool = _loaded_pool([_row("cred-1", "reina-main", "tok-reina", 0)])

    from hermes_cli.auth import write_credential_pool

    write_credential_pool(
        PROVIDER,
        [_row("cred-1", "reina-main", "tok-reina", 0), _row("cred-2", "ivanvanes18", "tok-ivan", 1)],
    )

    assert pool.reconcile_with_store() == 1
    assert pool.reconcile_with_store() == 0
    assert [e.id for e in pool.entries()] == ["cred-1", "cred-2"]


def test_unpersistable_duplicate_row_is_not_admitted_twice(monkeypatch):
    """A store row that keeps a fresh id per load must not grow the pool."""
    import agent.credential_pool as pool_mod

    pool = _loaded_pool([_row("cred-1", "reina-main", "tok-reina", 0)])

    class _RerolledIdPool:
        provider = PROVIDER

        def entries(self):
            from agent.credential_pool import PooledCredential

            # Same credential, different id on every load (write-back failed).
            return [PooledCredential.from_dict(PROVIDER, _row("reroll", "reina-main", "tok-reina", 0))]

    monkeypatch.setattr(pool_mod, "load_pool", lambda _provider: _RerolledIdPool())

    assert pool.reconcile_with_store() == 0
    assert [e.id for e in pool.entries()] == ["cred-1"]


def test_failed_store_reload_keeps_every_existing_credential(monkeypatch):
    import agent.credential_pool as pool_mod

    pool = _loaded_pool(
        [_row("cred-1", "reina-main", "tok-reina", 0), _row("cred-2", "ivanvanes18", "tok-ivan", 1)],
    )
    before = pool.entries()

    def _boom(_provider):
        raise RuntimeError("auth store unreadable")

    monkeypatch.setattr(pool_mod, "load_pool", _boom)

    assert pool.reconcile_with_store() == 0
    assert pool.entries() == before


def test_store_row_missing_from_a_stale_read_is_not_dropped(monkeypatch):
    """A reload that sees fewer rows (empty/stale read) must not prune the pool."""
    import agent.credential_pool as pool_mod

    pool = _loaded_pool(
        [_row("cred-1", "reina-main", "tok-reina", 0), _row("cred-2", "ivanvanes18", "tok-ivan", 1)],
    )

    class _EmptyPool:
        provider = PROVIDER

        def entries(self):
            return []

    monkeypatch.setattr(pool_mod, "load_pool", lambda _provider: _EmptyPool())

    assert pool.reconcile_with_store() == 0
    assert [e.id for e in pool.entries()] == ["cred-1", "cred-2"]
