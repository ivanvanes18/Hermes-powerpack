"""Private append-only storage for Jev shadow metadata."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_FORBIDDEN_KEYS = {"user_text", "assistant_text", "state", "request", "response", "content", "prompt"}
_SECRET_RE = __import__("re").compile(r"(?i)(sk[_-]|jv_live_|bearer\s|private key|password\s*=)")

@dataclass(frozen=True)
class PilotSnapshot:
    pilot_id: str
    target: int
    valid_evaluations: int
    event_count: int
    terminal_state: str

class ShadowEventStore:
    def __init__(self, root: Path, pilot_id: str, target: int = 100):
        if target != 100:
            raise ValueError("target_invalid")
        self.root = Path(root)
        self.pilot_id = pilot_id
        self.target = target
        self._prepare_root()
        self.events_path = self.root / "events.jsonl"
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / ".lock"
        for path in (self.events_path, self.lock_path):
            flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
            fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
        if not self.state_path.exists():
            self._write_state(0, 0)
        elif stat.S_IMODE(self.state_path.stat().st_mode) != 0o600:
            raise ValueError("state_permissions")

    def _prepare_root(self):
        if self.root.exists():
            if self.root.is_symlink() or not self.root.is_dir() or stat.S_IMODE(self.root.stat().st_mode) & 0o077:
                raise ValueError("root_unsafe")
        else:
            self.root.mkdir(parents=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def _events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists(): return []
        with self.events_path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def _write_state(self, count: int, event_count: int):
        digest = hashlib.sha256(self.events_path.read_bytes() if self.events_path.exists() else b"").hexdigest()
        payload = {"pilot_id": self.pilot_id, "target": self.target, "valid_evaluations": count, "terminal_state": "pending_analysis" if count >= self.target else "collecting", "event_count": event_count, "events_sha256": digest}
        tmp = self.state_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":")); handle.flush(); os.fsync(handle.fileno())
        os.chmod(tmp, 0o600); os.replace(tmp, self.state_path)

    def reserve(self, turn_id: str) -> bool:
        if not isinstance(turn_id, str) or not turn_id or any(e.get("turn_id") == turn_id for e in self._events()): return False
        return self.snapshot().valid_evaluations < self.target

    def append(self, event: Mapping[str, Any]) -> None:
        data = json.loads(json.dumps(dict(event), allow_nan=False))
        if any(key in data for key in _FORBIDDEN_KEYS): raise ValueError("plaintext_field")
        def scan(value):
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if str(key).lower() in _FORBIDDEN_KEYS: raise ValueError("plaintext_field")
                    scan(item)
            elif isinstance(value, list):
                for item in value: scan(item)
            elif isinstance(value, str) and _SECRET_RE.search(value): raise ValueError("secret_like")
        scan(data)
        events = self._events()
        if data.get("pilot_id") != self.pilot_id or data.get("turn_id") in {e.get("turn_id") for e in events}: raise ValueError("duplicate_event")
        expected = len(events) + 1
        if data.get("ordinal", expected) != expected: raise ValueError("ordinal_invalid")
        data.setdefault("ordinal", expected)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"); handle.flush(); os.fsync(handle.fileno())
        valid = sum(bool(e.get("counts_toward_target")) for e in events) + bool(data.get("counts_toward_target"))
        self._write_state(valid, len(events) + 1)

    def snapshot(self) -> PilotSnapshot:
        events = self._events()
        return PilotSnapshot(self.pilot_id, self.target, sum(bool(e.get("counts_toward_target")) for e in events), len(events), "pending_analysis" if sum(bool(e.get("counts_toward_target")) for e in events) >= self.target else "collecting")

    def events(self) -> list[dict[str, Any]]:
        return self._events()
