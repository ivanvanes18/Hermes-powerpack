#!/usr/bin/env python3
"""Print a sanitized deterministic Jev shadow report."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.memory.hindsight.jev_shadow_report import ReportError, build_report

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    events_path = args.root / "events.jsonl"
    try:
        if args.root.is_symlink() or not events_path.is_file(): raise ReportError("root_invalid")
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        report = build_report(events)
        payload = json.dumps(report, sort_keys=True, indent=2) + "\n"
        if args.output:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle: handle.write(payload)
            except Exception:
                os.close(fd)
                raise
        else:
            print(payload, end="")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ReportError):
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
