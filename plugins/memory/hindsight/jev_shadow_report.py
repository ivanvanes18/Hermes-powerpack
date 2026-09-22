"""Deterministic aggregation of validated Jev shadow events."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

VERDICTS = ("shadow_retain", "shadow_buffer", "shadow_skip", "shadow_fail_open")

class ReportError(ValueError):
    pass

def _percentile(values: list[float], fraction: float) -> float | None:
    if not values: return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * fraction))))
    return values[index]

def build_report(events: Sequence[Mapping[str, Any]], *, target: int = 100) -> dict[str, Any]:
    if target != 100: raise ReportError("target_invalid")
    records = [dict(event) for event in events]
    ids = [event.get("turn_id") for event in records]
    if None in ids or len(set(ids)) != len(ids): raise ReportError("turn_ids_invalid")
    valid = sum(bool(event.get("counts_toward_target")) for event in records)
    if valid > target: raise ReportError("target_exceeded")
    verdict_counts = Counter(event.get("verdict") for event in records if event.get("kind") == "evaluation" and event.get("counts_toward_target"))
    if any(key not in VERDICTS for key in verdict_counts): raise ReportError("verdict_invalid")
    latencies = [event["latency_ms"] for event in records if isinstance(event.get("latency_ms"), (int, float))]
    usage = {"input_tokens": sum((event.get("usage") or {}).get("input_tokens", 0) for event in records), "output_tokens": sum((event.get("usage") or {}).get("output_tokens", 0) for event in records), "cost": sum((event.get("usage") or {}).get("cost", 0) for event in records)}
    errors = Counter(event.get("error_code") for event in records if event.get("error_code"))
    false_skips = [event["turn_id"] for event in records if event.get("verdict") == "shadow_skip" and (event.get("fact_count") or 0) > 0]
    return {"status": "pending_analysis" if valid == target else "collecting", "target": target, "event_count": len(records), "valid_evaluations": valid, "verdict_counts": {key: verdict_counts.get(key, 0) for key in VERDICTS}, "models": sorted({event["model"] for event in records if event.get("model")}), "latency_ms": {"p50": _percentile(latencies, .5), "p95": _percentile(latencies, .95)}, "usage": usage, "error_counts": dict(sorted(errors.items())), "candidate_false_skips": false_skips}
