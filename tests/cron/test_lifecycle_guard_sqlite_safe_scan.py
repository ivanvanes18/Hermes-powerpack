"""The lifecycle guard's referenced-script scan must not cancel live SQLite locks.

The shell tokenizer also sees paths embedded in Python heredocs, so a preflight scan
can end up opening ``state.db``. A raw ``open()``/``close()`` of an already-open SQLite
file drops **every** POSIX advisory lock this process holds on that inode — silently
corrupting the live gateway's writes. The scan must therefore route through the
official ``hermes_cli.sqlite_safe_read`` locking API.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cron import lifecycle_guard
from hermes_cli import sqlite_safe_read


def test_referenced_script_read_holds_the_offline_lock(tmp_path, monkeypatch):
    script = tmp_path / "job.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    events: list[str] = []

    import contextlib

    @contextlib.contextmanager
    def tracking_offline(path, *, what="read"):
        events.append(f"enter:{Path(path).name}")
        yield
        events.append("exit")

    monkeypatch.setattr(sqlite_safe_read, "offline_file_access", tracking_offline)

    text, unsafe = lifecycle_guard._read_referenced_script(script)

    assert unsafe is False
    assert "echo hi" in text
    # The lock must still be held while the descriptor is closed, not released first.
    assert events == [f"enter:{script.name}", "exit"]


def test_live_sqlite_connection_blocks_the_raw_read(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

    import contextlib

    @contextlib.contextmanager
    def refusing_offline(path, *, what="read"):
        raise sqlite_safe_read.LiveConnectionError("live connection")
        yield  # pragma: no cover

    monkeypatch.setattr(sqlite_safe_read, "offline_file_access", refusing_offline)

    text, unsafe = lifecycle_guard._read_referenced_script(db_path)

    # A live local SQLite connection is an incomplete safety scan, so the lifecycle
    # guard fails closed rather than treating the unreadable target as safe.
    assert text is None
    assert unsafe is True


def test_live_connection_refusal_still_allows_the_remote_reader(tmp_path, monkeypatch):
    """The refusal is about THIS process's POSIX locks; a remote backend read is a
    different filesystem, so dropping it would silently shrink lifecycle scan coverage."""
    import contextlib

    @contextlib.contextmanager
    def refusing_offline(path, *, what="read"):
        raise sqlite_safe_read.LiveConnectionError("live connection")
        yield  # pragma: no cover

    monkeypatch.setattr(sqlite_safe_read, "offline_file_access", refusing_offline)

    remote_reads: list[str] = []

    def read_remote_script(path: str) -> str:
        remote_reads.append(path)
        return "hermes gateway restart\n"

    budget = lifecycle_guard._LifecycleScanBudget()
    unsafe = lifecycle_guard._contains_unsafe_gateway_action(
        f"bash {tmp_path / 'state.db'}",
        cwd=str(tmp_path), depth=0, visited=set(), budget=budget,
        read_remote_script=read_remote_script,
    )

    assert remote_reads == []
    assert unsafe is True


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sidecar_paths_lock_the_owning_database(tmp_path, monkeypatch, suffix):
    """``state.db-wal`` shares the lock domain of ``state.db``; the guard must protect
    the database, not the sidecar path it happened to tokenize."""
    sidecar = tmp_path / f"state.db{suffix}"
    sidecar.write_bytes(b"\x00" * 32)
    protected: list[Path] = []

    import contextlib

    @contextlib.contextmanager
    def tracking_offline(path, *, what="read"):
        protected.append(Path(path))
        yield

    monkeypatch.setattr(sqlite_safe_read, "offline_file_access", tracking_offline)

    lifecycle_guard._read_referenced_script(sidecar)

    assert protected == [(tmp_path / "state.db").resolve()]


def test_cloud_placeholder_is_still_refused_without_opening(tmp_path, monkeypatch):
    """The cloud-placeholder refusal must stay ahead of any file access."""
    placeholder = tmp_path / "onedrive-script.sh"
    placeholder.write_text("echo hi\n")
    monkeypatch.setattr(lifecycle_guard, "_on_cloud_path", lambda _p: True)

    import contextlib

    @contextlib.contextmanager
    def forbidden_offline(path, *, what="read"):
        raise AssertionError("cloud placeholder must not be opened")
        yield  # pragma: no cover

    monkeypatch.setattr(sqlite_safe_read, "offline_file_access", forbidden_offline)

    assert lifecycle_guard._read_referenced_script(placeholder) == (None, True)
