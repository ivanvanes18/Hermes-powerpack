"""Device-auth contract for the gptprof Codex profile manager.

`/gptprof` device auth must land the newly authorized account in the canonical
Hermes CredentialPool (`$HERMES_HOME/auth.json`), not in an OpenClaw-private
store the agent runtime never reads.
"""

import base64
import importlib.util
import json
import stat
import sys
import time
from pathlib import Path

import pytest

_MANAGER_PATH = Path(__file__).parents[1] / "bin" / "codex-profile-manager.py"
_POOL_SOURCE = "manual:device_code"
_ACCESS_CLAIMS = {
    "email": "device-user@example.com",
    "exp": int(time.time()) + 3600,
    "https://api.openai.com/auth": {
        "chatgpt_account_id": "acct-fake",
        "chatgpt_plan_type": "pro",
    },
}
_EXISTING_ROW = {
    "id": "gptprof:existing",
    "source": _POOL_SOURCE,
    "label": "existing",
    "auth_type": "oauth",
    "priority": 0,
    "access_token": "existing-access-token",
    "refresh_token": "existing-refresh-token",
    "base_url": "https://chatgpt.com/backend-api/codex",
}


def _jwt(claims: dict) -> str:
    def _seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(obj).encode("utf-8")
        ).rstrip(b"=").decode("ascii")

    return f"{_seg({'alg': 'none'})}.{_seg(claims)}.signature"


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def device_auth_env(tmp_path, monkeypatch):
    """Real manager module bound to a throwaway HOME + canonical HERMES_HOME."""
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    auth_path = hermes_home / "auth.json"
    auth_path.write_text(
        json.dumps({"credential_pool": {"openai-codex": [dict(_EXISTING_ROW)]}}),
        encoding="utf-8",
    )

    access_token = _jwt(_ACCESS_CLAIMS)
    responses = {
        "/api/accounts/deviceauth/usercode": {
            "device_auth_id": "device-auth-id-1",
            "user_code": "ABCD-EFGH",
            "interval": 5,
        },
        "/api/accounts/deviceauth/token": {
            "authorization_code": "fake-authorization-code",
            "code_verifier": "fake-code-verifier",
        },
        "/oauth/token": {
            "access_token": access_token,
            "refresh_token": "fake-refresh-token",
            "id_token": access_token,
        },
    }

    def _fake_urlopen(request, timeout=None):
        url = request.full_url
        for suffix, payload in responses.items():
            if url.endswith(suffix):
                return _FakeResponse(payload)
        raise AssertionError(f"unexpected outbound HTTP call: {url}")

    spec = importlib.util.spec_from_file_location(
        "gptprof_codex_profile_manager_under_test", _MANAGER_PATH
    )
    assert spec is not None and spec.loader is not None
    manager = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, manager)
    spec.loader.exec_module(manager)
    monkeypatch.setattr(manager.urllib.request, "urlopen", _fake_urlopen)

    return manager, home, hermes_home, access_token


def _pool_rows(hermes_home: Path) -> list:
    auth = json.loads((hermes_home / "auth.json").read_text(encoding="utf-8"))
    return auth.get("credential_pool", {}).get("openai-codex", [])


def _files_leaking(root: Path, secret: str) -> list:
    return [
        str(path)
        for path in root.rglob("*")
        if path.is_file() and secret in path.read_text(encoding="utf-8", errors="replace")
    ]


def test_device_auth_persists_new_account_into_canonical_credential_pool(device_auth_env):
    manager, home, hermes_home, access_token = device_auth_env
    from hermes_cli.auth_constants import DEFAULT_CODEX_BASE_URL

    started = manager.device_start()
    assert started["userCode"] == "ABCD-EFGH"
    pending_files = [
        path
        for path in hermes_home.rglob("*")
        if path.is_file() and "device-auth-id-1" in path.read_text(encoding="utf-8", errors="replace")
    ]

    result = manager.device_check()
    assert result["ok"] is True and result["pending"] is False

    rows = _pool_rows(hermes_home)
    existing_rows = [row for row in rows if row.get("id") == _EXISTING_ROW["id"]]
    assert len(existing_rows) == 1, "existing pool entry must survive"
    existing = existing_rows[0]
    for key, value in _EXISTING_ROW.items():
        assert existing.get(key) == value
    new_rows = [row for row in rows if row.get("access_token") == access_token]
    assert len(new_rows) == 1, f"device auth did not reach the CredentialPool: {rows}"
    new_row = new_rows[0]
    assert new_row["source"] == _POOL_SOURCE
    assert new_row["auth_type"] == "oauth"
    assert new_row["refresh_token"] == "fake-refresh-token"
    assert new_row["base_url"] == DEFAULT_CODEX_BASE_URL
    assert new_row["priority"] == _EXISTING_ROW["priority"] + 1

    for legacy_root in (home / ".openclaw", home / ".codex"):
        if legacy_root.exists():
            assert not _files_leaking(legacy_root, access_token)
            assert not _files_leaking(legacy_root, "fake-refresh-token")
    assert pending_files, "pending device auth state must live under HERMES_HOME"
    for path in pending_files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        text = path.read_text(encoding="utf-8", errors="replace")
        assert access_token not in text and "fake-refresh-token" not in text


def test_device_credential_store_is_idempotent_for_same_token_pair(device_auth_env):
    manager, _home, hermes_home, access_token = device_auth_env
    manager.device_start()
    first = manager.device_check()

    credential_id, added = manager.store_codex_device_credential(
        access_token,
        "fake-refresh-token",
        {"tokens": {"access_token": access_token, "refresh_token": "fake-refresh-token"}},
    )

    assert added is False
    assert credential_id == first["credentialId"]
    assert len(_pool_rows(hermes_home)) == 2


def test_device_check_reports_success_when_pending_cleanup_fails(
    device_auth_env, monkeypatch
):
    manager, _home, hermes_home, access_token = device_auth_env
    manager.device_start()

    monkeypatch.setattr(
        manager,
        "save_device_state",
        lambda _state: (_ for _ in ()).throw(OSError("cleanup failed")),
    )
    result = manager.device_check()

    assert result["ok"] is True
    assert result["cleanupWarning"] is True
    assert len(
        [row for row in _pool_rows(hermes_home) if row.get("access_token") == access_token]
    ) == 1


def test_device_check_does_not_clear_a_newer_auth_attempt(
    device_auth_env, monkeypatch
):
    manager, _home, _hermes_home, _access_token = device_auth_env
    manager.device_start()
    original_store = manager.store_codex_device_credential

    def store_then_start_new_attempt(access, refresh, auth):
        result = original_store(access, refresh, auth)
        manager.save_device_state(
            {
                "pendingDeviceAuth": {
                    "attemptId": "newer-attempt",
                    "deviceAuthId": "device-auth-id-2",
                    "userCode": "IJKL-MNOP",
                }
            }
        )
        return result

    monkeypatch.setattr(
        manager, "store_codex_device_credential", store_then_start_new_attempt
    )
    result = manager.device_check()

    assert result["ok"] is True
    assert manager.pending_device_auth()["attemptId"] == "newer-attempt"
