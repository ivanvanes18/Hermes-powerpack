from __future__ import annotations

import stat
import threading
from types import SimpleNamespace

import pytest

from hermes_cli import auth


def _stat(uid: int, gid: int):
    return SimpleNamespace(st_uid=uid, st_gid=gid)


def test_root_auth_write_prefers_existing_non_root_file_owner() -> None:
    assert auth._select_auth_store_owner_ids(_stat(1001, 1001), _stat(1002, 1002)) == (
        1001,
        1001,
    )


def test_root_auth_write_repairs_root_owned_file_from_non_root_parent() -> None:
    assert auth._select_auth_store_owner_ids(_stat(0, 0), _stat(1001, 1001)) == (
        1001,
        1001,
    )


def test_root_auth_write_keeps_root_owner_under_root_home() -> None:
    assert auth._select_auth_store_owner_ids(_stat(0, 0), _stat(0, 0)) == (0, 0)


def test_save_auth_store_applies_selected_owner_to_temp_and_final(
    tmp_path,
    monkeypatch,
) -> None:
    auth_path = tmp_path / "auth.json"
    calls: list[tuple[str, tuple[int, int] | None]] = []

    monkeypatch.setattr(auth, "_auth_file_path", lambda: auth_path)
    monkeypatch.setattr(
        auth,
        "_auth_store_owner_ids_for_root_write",
        lambda path: (1001, 1001) if path == auth_path else None,
    )
    monkeypatch.setattr(
        auth,
        "_apply_auth_store_owner",
        lambda path, owner_ids: calls.append((path.name, owner_ids)),
    )

    saved = auth._save_auth_store({"providers": {}})

    assert saved == auth_path
    assert any(
        name.startswith("auth.json.tmp.") and owner_ids == (1001, 1001)
        for name, owner_ids in calls
    )
    assert ("auth.json", (1001, 1001)) in calls
    assert auth_path.exists()


def test_repair_auth_lock_owner_creates_secure_lock_and_applies_owner(
    tmp_path,
    monkeypatch,
) -> None:
    lock_path = tmp_path / "auth.lock"
    calls: list[tuple[int, int]] = []

    monkeypatch.setattr(auth, "_auth_lock_path", lambda: lock_path)
    monkeypatch.setattr(
        auth,
        "_auth_store_owner_ids_for_root_write",
        lambda path: (1001, 1001) if path == lock_path else None,
    )
    monkeypatch.setattr(auth.os, "fchown", lambda _fd, uid, gid: calls.append((uid, gid)))

    auth._repair_auth_lock_owner_for_root()

    assert lock_path.exists()
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert calls == [(1001, 1001)]


def test_repair_auth_lock_owner_refuses_symlink(tmp_path, monkeypatch) -> None:
    target = tmp_path / "target"
    target.write_text("do not touch", encoding="utf-8")
    target.chmod(0o644)
    lock_path = tmp_path / "auth.lock"
    lock_path.symlink_to(target)

    monkeypatch.setattr(auth, "_auth_lock_path", lambda: lock_path)
    monkeypatch.setattr(auth, "_auth_store_owner_ids_for_root_write", lambda _path: (1001, 1001))

    auth._repair_auth_lock_owner_for_root()

    assert lock_path.is_symlink()
    assert target.read_text(encoding="utf-8") == "do not touch"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_file_lock_refuses_symlink(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_text("do not lock", encoding="utf-8")
    lock_path = tmp_path / "auth.lock"
    lock_path.symlink_to(target)

    with pytest.raises(OSError):
        with auth._file_lock(lock_path, threading.local(), 1.0, "timeout"):
            pass

    assert target.read_text(encoding="utf-8") == "do not lock"
