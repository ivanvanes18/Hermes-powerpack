#!/usr/bin/env python3
"""Print a verified, sanitized deterministic Jev shadow report."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.memory.hindsight.jev_shadow_report import ReportError, build_report
from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore


def _safe_output(path: Path, payload: str) -> None:
    parent = path.parent.absolute()
    current = parent
    while True:
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode): raise ReportError("output_parent_unsafe")
        if current == current.parent: break
        current = current.parent
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode): raise ReportError("output_unsafe")
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = path.name
    tmp_name = f".{name}.{os.getpid()}.tmp"
    try:
        out = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            os.write(out, payload.encode()); os.fsync(out); os.fchmod(out, 0o600)
        finally: os.close(out)
        os.replace(tmp_name, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try: os.unlink(tmp_name, dir_fd=fd)
        except FileNotFoundError: pass
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.root.is_symlink() or not args.root.is_dir(): raise ReportError("root_invalid")
        root_fd = os.open(args.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            state_fd = os.open("state.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                info = os.fstat(state_fd)
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                    raise ReportError("state_unsafe")
                state = json.loads(os.read(state_fd, 1024 * 1024))
            finally:
                os.close(state_fd)
        finally:
            os.close(root_fd)
        store = ShadowEventStore(args.root, state["pilot_id"], state["target"])
        events = store.events()  # startup verifies state fields, modes, and exact event bytes digest
        report = build_report(events, target=state["target"])
        payload = json.dumps(report, sort_keys=True, indent=2) + "\n"
        if args.output: _safe_output(args.output, payload)
        else: print(payload, end="")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ReportError, KeyError, TypeError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
