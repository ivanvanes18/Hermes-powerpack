from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_FORBIDDEN_KEYS = {"user_text", "assistant_text", "state", "request", "response", "content", "prompt"}
_SECRET_RE = re.compile(r"(?i)(sk[_-]|jv_live_|bearer\s|private key|password\s*=)")
_EVENT_KINDS = {"reservation", "evaluation", "retain_outcome"}
_LOGGER = logging.getLogger(__name__)
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ShadowTransportError("duplicate_key")
        result[key] = value
    return result


@dataclass(frozen=True)
class PilotSnapshot:
    pilot_id: str
    target: int
    valid_evaluations: int
    event_count: int
    terminal_state: str


def _open_directory_chain(path: Path) -> int:
    """Open/create a directory path one component at a time, without lookup races."""
    path = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(os.sep, flags)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=fd)
                child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


class ShadowEventStore:
    def __init__(self, root: Path, pilot_id: str, target: int = 100):
        if target != 100:
            raise ValueError("target_invalid")
        if not isinstance(pilot_id, str) or not pilot_id:
            raise ValueError("pilot_invalid")
        self.root = Path(root).absolute()
        self.pilot_id = pilot_id
        self.target = target
        with _PROCESS_LOCKS_GUARD:
            self._process_lock = _PROCESS_LOCKS.setdefault(str(self.root), threading.Lock())
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        parent_fd = _open_directory_chain(self.root.parent)
        try:
            try:
                self._root_fd = os.open(self.root.name, flags, dir_fd=parent_fd)
            except FileNotFoundError:
                os.mkdir(self.root.name, 0o700, dir_fd=parent_fd)
                self._root_fd = os.open(self.root.name, flags, dir_fd=parent_fd)
            except OSError as exc:
                raise ValueError("root_unsafe") from exc
        finally:
            os.close(parent_fd)
        info = os.fstat(self._root_fd)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("root_unsafe")
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.fchmod(self._root_fd, 0o700)
        self._root_identity = os.fstat(self._root_fd)
        self.events_path = self.root / "events.jsonl"
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / ".lock"
        self._lock_fd = self._open_relative(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(self._lock_fd, 0o600)
        self._ensure_regular(".lock", 0o600)
        self._open_relative("events.jsonl", os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        self._ensure_regular("events.jsonl", 0o600)
        if self._relative_exists("state.json"):
            self._ensure_regular("state.json", 0o600)
            with self._exclusive():
                self._verify_state_locked()
        else:
            with self._exclusive():
                self._write_state_locked()

    def __del__(self):
        for fd in (getattr(self, "_lock_fd", -1), getattr(self, "_root_fd", -1)):
            try:
                if fd >= 0:
                    os.close(fd)
            except OSError:
                pass

    def _check_root(self) -> None:
        try:
            info = self.root.lstat()
        except FileNotFoundError as exc:
            raise ValueError("root_rebound") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("root_rebound")
        held = os.fstat(self._root_fd)
        if (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino):
            raise ValueError("root_rebound")

    def _open_relative(self, name: str, flags: int, mode: int = 0o600) -> int:
        self._check_root()
        return os.open(name, flags, mode, dir_fd=self._root_fd)

    def _relative_exists(self, name: str) -> bool:
        try:
            fd = self._open_relative(name, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return False
        else:
            os.close(fd)
            return True

    def _ensure_regular(self, name: str, mode: int) -> None:
        fd = self._open_relative(name, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != mode:
                raise ValueError(f"{name}_unsafe")
        finally:
            os.close(fd)

    def _exclusive(self):
        class Guard:
            def __init__(self, store: ShadowEventStore): self.store = store
            def __enter__(self):
                self.store._process_lock.acquire()
                try:
                    self.store._check_root()
                    fcntl.flock(self.store._lock_fd, fcntl.LOCK_EX)
                    self.store._check_root()
                    return self.store
                except BaseException:
                    self.store._process_lock.release()
                    raise
            def __exit__(self, *_):
                fcntl.flock(self.store._lock_fd, fcntl.LOCK_UN)
                self.store._process_lock.release()
        return Guard(self)

    def _read_bytes_locked(self, name: str) -> bytes:
        fd = self._open_relative(name, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError(f"{name}_unsafe")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk: return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(fd)

    def _events_locked(self) -> list[dict[str, Any]]:
        raw = self._read_bytes_locked("events.jsonl")
        events: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict): raise ValueError("event_invalid")
                events.append(value)
        return events

    def _write_state_locked(self) -> None:
        raw = self._read_bytes_locked("events.jsonl")
        events = self._events_from_bytes(raw)
        valid = sum(1 for event in events if event.get("kind") == "evaluation" and event.get("valid") is True and event.get("counts_toward_target") is True)
        if valid > self.target: raise ValueError("target_exceeded")
        data = {"pilot_id": self.pilot_id, "target": self.target, "valid_evaluations": valid,
                "terminal_state": "pending_analysis" if valid >= self.target else "collecting",
                "event_count": len(events), "events_sha256": hashlib.sha256(raw).hexdigest()}
        payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        name = f".state.{os.getpid()}.{threading.get_ident()}.tmp"
        fd = self._open_relative(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, payload); os.fsync(fd); os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        try:
            os.replace(name, "state.json", src_dir_fd=self._root_fd, dst_dir_fd=self._root_fd)
            os.fsync(self._root_fd)
        finally:
            try: os.unlink(name, dir_fd=self._root_fd)
            except FileNotFoundError: pass

    @staticmethod
    def _events_from_bytes(raw: bytes) -> list[dict[str, Any]]:
        result = []
        for line in raw.splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict): raise ValueError("event_invalid")
                result.append(value)
        return result

    def _verify_state_locked(self) -> None:
        raw = self._read_bytes_locked("events.jsonl")
        state_raw = self._read_bytes_locked("state.json")
        try: state = json.loads(state_raw)
        except (TypeError, ValueError) as exc: raise ValueError("state_invalid") from exc
        events = self._events_from_bytes(raw)
        valid = sum(1 for event in events if event.get("kind") == "evaluation" and event.get("valid") is True and event.get("counts_toward_target") is True)
        expected = {"pilot_id": self.pilot_id, "target": self.target, "valid_evaluations": valid,
                    "terminal_state": "pending_analysis" if valid >= self.target else "collecting",
                    "event_count": len(events), "events_sha256": hashlib.sha256(raw).hexdigest()}
        if state != expected: raise ValueError("state_invalid")

    def _validate_event(self, data: dict[str, Any], events: list[dict[str, Any]]) -> None:
        if data.get("pilot_id") != self.pilot_id or data.get("kind") not in _EVENT_KINDS:
            raise ValueError("event_invalid")
        if not isinstance(data.get("turn_id"), str) or not data["turn_id"]:
            raise ValueError("turn_id_invalid")
        identity = (data["turn_id"], data["kind"])
        if any((event.get("turn_id"), event.get("kind")) == identity for event in events):
            raise ValueError("duplicate_event")
        expected = len(events) + 1
        if data.get("ordinal", expected) != expected: raise ValueError("ordinal_invalid")
        data.setdefault("ordinal", expected)
        if data.get("kind") == "evaluation" and data.get("valid") is True and data.get("counts_toward_target") is True:
            if sum(1 for event in events if event.get("kind") == "evaluation" and event.get("valid") is True and event.get("counts_toward_target") is True) >= self.target:
                raise ValueError("target_exceeded")

    def append(self, event: Mapping[str, Any]) -> None:
        data = json.loads(json.dumps(dict(event), allow_nan=False))
        def scan(value: Any) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    if str(key).lower() in _FORBIDDEN_KEYS: raise ValueError("plaintext_field")
                    scan(child)
            elif isinstance(value, list):
                for child in value: scan(child)
            elif isinstance(value, str) and _SECRET_RE.search(value):
                raise ValueError("secret_like")
        scan(data)
        with self._exclusive():
            events = self._events_locked()
            self._validate_event(data, events)
            line = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
            fd = self._open_relative("events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            try:
                os.write(fd, line); os.fsync(fd)
            finally: os.close(fd)
            self._write_state_locked()

    def reserve(self, turn_id: str) -> bool:
        if not isinstance(turn_id, str) or not turn_id: return False
        with self._exclusive():
            events = self._events_locked()
            if any(event.get("turn_id") == turn_id for event in events): return False
            valid = sum(1 for event in events if event.get("kind") == "evaluation" and event.get("valid") is True and event.get("counts_toward_target") is True)
            active = sum(1 for event in events
                         if event.get("kind") == "reservation"
                         and not any(terminal.get("kind") == "evaluation" and terminal.get("turn_id") == event.get("turn_id") for terminal in events))
            if valid + active >= self.target: return False
            data = {"kind": "reservation", "pilot_id": self.pilot_id, "turn_id": turn_id,
                    "owner_pid": os.getpid(), "counts_toward_target": False}
            self._validate_event(data, events)
            line = json.dumps(data, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            fd = self._open_relative("events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            try: os.write(fd, line); os.fsync(fd)
            finally: os.close(fd)
            self._write_state_locked()
            return True

    def reconcile_stale_reservations(self) -> int:
        """Close claims whose owning process is no longer alive.

        A reservation is a durable claim, not permission to retry after a
        restart. Only a dead owner can be reconciled; live owners retain the
        claim so a second runtime cannot cause an extra provider call.
        """
        reconciled = 0
        with self._exclusive():
            events = self._events_locked()
            for reservation in events:
                if reservation.get("kind") != "reservation":
                    continue
                if any(event.get("kind") == "evaluation" and event.get("turn_id") == reservation.get("turn_id") for event in events):
                    continue
                owner_pid = reservation.get("owner_pid")
                try:
                    if not isinstance(owner_pid, int) or owner_pid <= 0:
                        owner_alive = False
                    else:
                        from gateway.status import _pid_exists
                        owner_alive = bool(_pid_exists(owner_pid))
                except (TypeError, ValueError, OSError):
                    owner_alive = False
                if owner_alive:
                    continue
                terminal = {"kind": "evaluation", "pilot_id": self.pilot_id,
                            "turn_id": reservation["turn_id"], "valid": False,
                            "counts_toward_target": False, "verdict": "shadow_fail_open",
                            "model": None, "policy": {}, "latency_ms": 0, "usage": {},
                            "error_code": "stale_reservation", "fact_count": 0, "fact_types": []}
                self._validate_event(terminal, events)
                line = json.dumps(terminal, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
                fd = self._open_relative("events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
                try:
                    os.write(fd, line); os.fsync(fd)
                finally:
                    os.close(fd)
                events.append(terminal)
                reconciled += 1
            if reconciled:
                self._write_state_locked()
        return reconciled

    def snapshot(self) -> PilotSnapshot:
        with self._exclusive():
            events = self._events_locked()
            self._verify_state_locked()
            valid = sum(1 for event in events if event.get("kind") == "evaluation" and event.get("valid") is True and event.get("counts_toward_target") is True)
            return PilotSnapshot(self.pilot_id, self.target, valid, len(events), "pending_analysis" if valid >= self.target else "collecting")

    def events(self) -> list[dict[str, Any]]:
        with self._exclusive():
            self._verify_state_locked()
            return self._events_locked()


@dataclass(frozen=True)
class ShadowRuntimeConfig:
    pilot_id: str
    policy: Any = None
    max_queue: int = 32
    timeout: float = 10.0


@dataclass(frozen=True)
class RetainOutcome:
    status: str
    operation_ids_count: int = 0
    result_items_count: int | None = None
    fact_count: int | None = None
    fact_types: tuple[str, ...] = ()
    latency_ms: int | None = None
    error_code: str | None = None


class ShadowTransportError(RuntimeError):
    def __init__(self, code: str): self.code = code; super().__init__(code)


class TypeSafeTransport:
    def __init__(self, endpoint: str, api_key: str, timeout: float = 10.0):
        self.url = endpoint.rstrip("/") + "/v1/systemone"; self.api_key = api_key; self.timeout = timeout
    def evaluate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        body = json.dumps(dict(payload), separators=(",", ":")).encode(); last: ShadowTransportError | None = None
        for _ in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise ShadowTransportError("timeout")
            req = urllib.request.Request(self.url, data=body, method="POST", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=remaining) as response: raw = response.read()
                try: value = json.loads(raw.decode(), object_pairs_hook=_reject_duplicate_json_keys)
                except (ValueError, UnicodeDecodeError) as exc: raise ShadowTransportError("invalid_json") from exc
                if not isinstance(value, dict): raise ShadowTransportError("response_invalid")
                return value
            except urllib.error.HTTPError as exc:
                code = "http_429" if exc.code == 429 else "http_5xx" if 500 <= exc.code < 600 else "http_4xx" if 400 <= exc.code < 500 else "transport_error"
                last = ShadowTransportError(code)
                if code == "http_4xx": raise last
            except TimeoutError: last = ShadowTransportError("timeout")
            except OSError: last = ShadowTransportError("transport_error")
        raise last or ShadowTransportError("transport_error")


class JevShadowRuntime:
    def __init__(self, config: ShadowRuntimeConfig, store: ShadowEventStore, transport: Any):
        import queue
        from .jev_shadow import ShadowPolicy
        self.config = config; self.store = store; self.transport = transport; self.policy = config.policy or ShadowPolicy()
        self._queue = queue.Queue(maxsize=config.max_queue); self._stop = threading.Event(); self._thread = None
        self._disabled = False; self._persistence_error: str | None = None
        self._admission_lock = threading.Lock(); self._admitted: set[str] = set()
        try:
            self.store.reconcile_stale_reservations()
        except AttributeError:
            pass
        except (ValueError, OSError) as exc:
            self._disable_persistence(exc)

    def _disable_persistence(self, error: BaseException) -> None:
        self._disabled = True
        self._persistence_error = type(error).__name__
        _LOGGER.warning("Jev shadow persistence disabled (%s)", self._persistence_error)

    def status_snapshot(self) -> dict[str, Any]:
        return {"disabled": self._disabled, "error_type": self._persistence_error,
                "error_code": "persistence_error" if self._disabled else None,
                "report_valid": not self._disabled}

    def _ensure(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, daemon=True); self._thread.start()

    def _worker(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try: item = self._queue.get(timeout=.05)
            except Exception: continue
            try: self._evaluate(*item)
            finally: self._queue.task_done()

    def enqueue(self, turn_id: str, turns: Sequence[Any], *, explicit_memory_request: bool) -> bool:
        import queue
        if self._stop.is_set() or self._disabled: return False
        with self._admission_lock:
            if turn_id in self._admitted:
                return False
            try:
                existing = self.store.events() if hasattr(self.store, "events") else []
                if any(event.get("turn_id") == turn_id for event in existing):
                    return False
                if not self.store.reserve(turn_id):
                    try:
                        self.store.append({"kind": "evaluation", "pilot_id": self.store.pilot_id,
                                           "turn_id": turn_id, "valid": False,
                                           "counts_toward_target": False,
                                           "verdict": "shadow_fail_open", "model": None,
                                           "policy": self._policy_dict(), "latency_ms": 0,
                                           "usage": {}, "error_code": "target_reached",
                                           "fact_count": 0, "fact_types": []})
                    except ValueError:
                        pass
                    return False
            except (ValueError, OSError) as exc:
                self._disable_persistence(exc); return False
            self._admitted.add(turn_id)
        try: self._queue.put_nowait((turn_id, list(turns), explicit_memory_request))
        except queue.Full:
            with self._admission_lock: self._admitted.discard(turn_id)
            try:
                self.store.append({"kind": "evaluation", "pilot_id": self.store.pilot_id,
                                   "turn_id": turn_id, "valid": False,
                                   "counts_toward_target": False,
                                   "verdict": "shadow_fail_open", "model": None,
                                   "policy": self._policy_dict(), "latency_ms": 0,
                                   "usage": {}, "error_code": "queue_full",
                                   "fact_count": 0, "fact_types": []})
            except (ValueError, OSError) as exc:
                self._disable_persistence(exc)
            return False
        self._ensure(); return True

    def _evaluate(self, turn_id: str, turns: list[Any], explicit: bool) -> None:
        from .jev_shadow import build_shadow_request, validate_shadow_response, derive_shadow_verdict, ShadowContractError, SHADOW_FAIL_OPEN
        event: dict[str, Any] = {"kind": "evaluation", "pilot_id": self.store.pilot_id, "turn_id": turn_id, "valid": False, "counts_toward_target": False, "verdict": SHADOW_FAIL_OPEN, "model": None, "policy": self._policy_dict(), "latency_ms": None, "usage": {}, "error_code": None, "fact_count": 0, "fact_types": []}
        start = time.monotonic()
        try:
            response = self.transport.evaluate(build_shadow_request(turns, self.policy))
            answers = validate_shadow_response(response, self.policy)
            event.update(valid=True, counts_toward_target=True, verdict=derive_shadow_verdict(answers, policy=self.policy, explicit_memory_request=explicit, excluded_content=False), model=response.get("model"))
        except ShadowContractError as exc: event["error_code"] = exc.code
        except ShadowTransportError as exc: event["error_code"] = exc.code
        except TimeoutError: event["error_code"] = "timeout"
        except Exception: event["error_code"] = "transport_error"
        event["latency_ms"] = int((time.monotonic() - start) * 1000)
        with self._admission_lock:
            try: self.store.append(event)
            except (ValueError, OSError) as exc: self._disable_persistence(exc)
            finally: self._admitted.discard(turn_id)

    def _policy_dict(self) -> dict[str, Any]:
        return {"model": self.policy.model, "accepted_models": list(self.policy.accepted_models), "should_retain_threshold": self.policy.should_retain_threshold, "grounded_threshold": self.policy.grounded_threshold, "standalone_threshold": self.policy.standalone_threshold, "duplicate_threshold": self.policy.duplicate_threshold, "sensitive_threshold": self.policy.sensitive_threshold}

    def record_retain_outcome(self, turn_id: str, outcome: RetainOutcome) -> None:
        if self._disabled: return
        try:
            if any(e.get("turn_id") == turn_id and e.get("kind") == "retain_outcome" for e in self.store.events()): return
            self.store.append({"kind": "retain_outcome", "pilot_id": self.store.pilot_id, "turn_id": turn_id, "counts_toward_target": False, "status": outcome.status, "operation_ids_count": outcome.operation_ids_count, "result_items_count": outcome.result_items_count, "fact_count": outcome.fact_count if outcome.fact_count is not None else 0, "fact_types": list(outcome.fact_types), "latency_ms": outcome.latency_ms, "error_code": outcome.error_code})
        except (ValueError, OSError) as exc: self._disable_persistence(exc)

    def shutdown(self, timeout: float = 1.0) -> None:
        if self._thread is None: return
        self._stop.set(); self._thread.join(max(0, timeout))
