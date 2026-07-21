"""Static contracts for the optional gptprof helper installation."""

from pathlib import Path
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _installer_config_block() -> str:
    installer = (ROOT / "scripts" / "install-powerpack.sh").read_text(
        encoding="utf-8"
    )
    marker = '"$ROOT/$VENV_DIR/bin/python" - <<\'PY\'\n'
    return installer.split(marker, 1)[1].split("\nPY\n", 1)[0]


def _load_send_buttons(monkeypatch, home: Path):
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_HCP", str(home / "gptprof" / "profiles"))
    path = ROOT / "skills" / "gptprof-hermes" / "bin" / "send_buttons.py"
    spec = importlib.util.spec_from_file_location("gptprof_send_buttons_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_keeps_canonical_and_legacy_send_buttons_names():
    installer = (ROOT / "scripts" / "install-powerpack.sh").read_text(encoding="utf-8")

    assert '"$HOME/.local/bin/send_buttons.py"' in installer
    assert '"$HOME/.local/bin/gptprof_send_buttons.py"' in installer
    assert '"$HOME/.local/bin/codex-profile-manager.py"' not in installer
    assert "from hermes_cli.config import is_managed, save_config" in installer
    assert "save_config(cfg" in installer
    assert "config_path.write_text" not in installer
    assert "except Exception:\n    cfg = {}" not in installer


def test_installer_refuses_to_overwrite_malformed_config(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    config_path = home / "config.yaml"
    malformed = "quick_commands: [unterminated\n"
    config_path.write_text(malformed, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        exec(compile(_installer_config_block(), "install-powerpack.sh", "exec"), {})

    assert config_path.read_text(encoding="utf-8") == malformed


def test_installer_leaves_managed_config_untouched(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    config_path = home / "config.yaml"
    original = "model: MiniMax-M2.7\n"
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.config.is_managed", lambda: True)

    with pytest.raises(SystemExit) as exc_info:
        exec(compile(_installer_config_block(), "install-powerpack.sh", "exec"), {})

    assert exc_info.value.code == 0
    assert config_path.read_text(encoding="utf-8") == original


def test_autoswitch_default_auth_path_is_home_relative():
    autoswitch = (ROOT / "skills" / "gptprof-hermes" / "bin" / "gptprof_autoswitch.py").read_text(
        encoding="utf-8"
    )

    assert "/home/hermes/.hermes/auth.json" not in autoswitch
    assert "HERMES_HOME" in autoswitch
    assert "/tmp/gptprof-autoswitch" not in autoswitch


def test_helpers_delegate_oauth_rotation_to_hermes_pool():
    send_buttons = (ROOT / "skills" / "gptprof-hermes" / "bin" / "send_buttons.py").read_text(
        encoding="utf-8"
    )
    refresh = (ROOT / "skills" / "gptprof-hermes" / "bin" / "refresh_profiles.py").read_text(
        encoding="utf-8"
    )

    assert "refresh_profile_token" not in send_buttons
    assert "CODEX_OAUTH_TOKEN_URL" not in send_buttons
    assert "load_pool" in refresh
    assert "manual:device_code" in send_buttons
    assert 'callback_data="gptprof:new_auth"' not in send_buttons
    assert 'callback_data="gptprof:check_auth"' not in send_buttons
    assert 'callback_data="gptprof:pi_route"' not in send_buttons
    assert "HERMES_AUTH" not in send_buttons


def test_profile_switch_prefers_pool_tokens_without_singleton_copies(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profile_dir = home / "gptprof" / "profiles"
    profile_dir.mkdir(parents=True)
    auth_path = home / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "active_provider": "minimax",
                "credential_pool": {
                    "openai-codex": [
                        {
                            "id": "gptprof:profile1",
                            "source": "manual:device_code",
                            "label": "profile1",
                            "auth_type": "oauth",
                            "priority": 8,
                            "access_token": "fresh-access",
                            "refresh_token": "fresh-refresh",
                        },
                        {
                            "id": "another-account",
                            "source": "manual:device_code",
                            "label": "another-account",
                            "auth_type": "oauth",
                            "priority": 0,
                            "access_token": "other-access",
                            "refresh_token": "other-refresh",
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    helper = _load_send_buttons(monkeypatch, home)

    helper.sync_active_auth(
        "profile1",
        {
            "access_token": "stale-bootstrap-access",
            "refresh_token": "stale-bootstrap-refresh",
            "email": "profile@example.com",
            "plan": "Pro",
        },
    )

    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    selected = auth["credential_pool"]["openai-codex"][0]
    assert selected["id"] == "gptprof:profile1"
    assert selected["source"] == "manual:device_code"
    assert selected["access_token"] == "fresh-access"
    assert selected["refresh_token"] == "fresh-refresh"
    assert auth["gptprof"]["active_profile"] == "profile1"
    assert auth["active_provider"] == "minimax"
    assert "codex" not in auth
    assert not auth.get("providers")
    assert auth["credential_pool"]["openai-codex"][1]["priority"] != 0


def test_runtime_pool_overlays_stale_bootstrap_profile(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    profile_dir = home / "gptprof" / "profiles"
    profile_dir.mkdir(parents=True)
    (home / "auth.json").write_text(
        json.dumps(
            {
                "gptprof": {"active_profile": "profile1"},
                "credential_pool": {
                    "openai-codex": [
                        {
                            "id": "gptprof:profile1",
                            "source": "manual:device_code",
                            "label": "profile1",
                            "auth_type": "oauth",
                            "priority": 0,
                            "access_token": "fresh-access",
                            "refresh_token": "fresh-refresh",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    helper = _load_send_buttons(monkeypatch, home)

    profiles = helper.overlay_runtime_profiles(
        {"profile1": {"access_token": "stale-access", "refresh_token": "stale-refresh"}}
    )

    assert profiles["profile1"]["access_token"] == "fresh-access"
    assert profiles["profile1"]["refresh_token"] == "fresh-refresh"
    assert helper.get_current_profile() == "profile1"


def test_profile_switch_rejects_unavailable_pool_entry(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    (home / "gptprof" / "profiles").mkdir(parents=True)
    auth_path = home / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "providers": {},
                "gptprof": {
                    "imported_profiles": {
                        "profile1": {
                            "refresh_fingerprint": hashlib.sha256(
                                b"stale-refresh"
                            ).hexdigest()[:16]
                        }
                    }
                },
                "credential_pool": {
                    "openai-codex": [
                        {
                            "id": "gptprof:profile1",
                            "source": "manual:device_code",
                            "label": "profile1",
                            "auth_type": "oauth",
                            "priority": 0,
                            "access_token": "access",
                            "refresh_token": "refresh",
                            "last_status": "dead",
                            "last_status_at": 4_000_000_000.0,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    helper = _load_send_buttons(monkeypatch, home)

    with pytest.raises(ValueError, match="unavailable"):
        helper.sync_active_auth(
            "profile1",
            {"access_token": "stale-access", "refresh_token": "stale-refresh"},
        )

    assert json.loads(auth_path.read_text(encoding="utf-8"))["credential_pool"][
        "openai-codex"
    ][0]["last_status"] == "dead"


def test_profile_switch_does_not_resurrect_consumed_bootstrap(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    (home / "gptprof" / "profiles").mkdir(parents=True)
    auth_path = home / "auth.json"
    refresh_token = "consumed-refresh"
    auth_path.write_text(
        json.dumps(
            {
                "providers": {},
                "gptprof": {
                    "imported_profiles": {
                        "profile1": {
                            "refresh_fingerprint": hashlib.sha256(
                                refresh_token.encode()
                            ).hexdigest()[:16]
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    helper = _load_send_buttons(monkeypatch, home)

    with pytest.raises(ValueError, match="stale bootstrap"):
        helper.sync_active_auth(
            "profile1",
            {"access_token": "stale-access", "refresh_token": refresh_token},
        )

    assert "credential_pool" not in json.loads(auth_path.read_text(encoding="utf-8"))


def test_profile_switch_rejects_rollback_to_any_consumed_bootstrap(
    tmp_path, monkeypatch
):
    home = tmp_path / "hermes-home"
    profile_dir = home / "gptprof" / "profiles"
    profile_dir.mkdir(parents=True)
    profile_path = profile_dir / "profile1.json"
    auth_path = home / "auth.json"
    auth_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    helper = _load_send_buttons(monkeypatch, home)

    profile_a = {"access_token": "access-A", "refresh_token": "refresh-A"}
    profile_b = {"access_token": "access-B", "refresh_token": "refresh-B"}
    profile_path.write_text(json.dumps(profile_a), encoding="utf-8")
    helper.sync_active_auth("profile1", profile_a)
    profile_path.write_text(json.dumps(profile_b), encoding="utf-8")
    helper.sync_active_auth("profile1", profile_b)

    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["credential_pool"]["openai-codex"] = []
    auth_path.write_text(json.dumps(auth), encoding="utf-8")
    profile_path.write_text(json.dumps(profile_a), encoding="utf-8")

    with pytest.raises(ValueError, match="stale bootstrap"):
        helper.sync_active_auth("profile1", profile_a)


def test_refresh_helper_reports_missing_pool_without_refreshing_profile_files(tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "skills" / "gptprof-hermes" / "bin" / "refresh_profiles.py"),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout) == [
        {
            "slug": None,
            "state": "missing_pool",
            "detail": "no imported gptprof credential pool entries",
        }
    ]


def test_refresh_helper_refuses_symlink_lock(tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("keep-me", encoding="utf-8")
    lock_path = tmp_path / "refresh.lock"
    lock_path.symlink_to(sentinel)
    env = os.environ.copy()
    env.update(
        {
            "HERMES_HOME": str(home),
            "PYTHONPATH": str(ROOT),
            "GPTPROF_REFRESH_LOCK": str(lock_path),
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "skills" / "gptprof-hermes" / "bin" / "refresh_profiles.py"),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep-me"


def test_autoswitch_state_save_does_not_follow_predictable_temp_symlink(
    tmp_path, monkeypatch
):
    home = tmp_path / "hermes-home"
    (home / "gptprof" / "profiles").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv(
        "GPTPROF_SEND_BUTTONS",
        str(ROOT / "skills" / "gptprof-hermes" / "bin" / "send_buttons.py"),
    )
    path = ROOT / "skills" / "gptprof-hermes" / "bin" / "gptprof_autoswitch.py"
    spec = importlib.util.spec_from_file_location("gptprof_autoswitch_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state_path = tmp_path / "state.json"
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("keep-me", encoding="utf-8")
    state_path.with_suffix(".tmp").symlink_to(sentinel)
    monkeypatch.setattr(module, "STATE_PATH", state_path)

    module.save_state({"state": "ok"})

    assert sentinel.read_text(encoding="utf-8") == "keep-me"
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"state": "ok"}
