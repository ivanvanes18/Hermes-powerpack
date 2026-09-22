"""Strict, deterministic aggregation of Jev shadow metadata events."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

VERDICTS = ("shadow_retain", "shadow_buffer", "shadow_skip", "shadow_fail_open")
EVENT_KINDS = {"reservation", "evaluation", "retain_outcome"}
RETAIN_STATUSES = {"queued", "succeeded", "failed"}


class ReportError(ValueError):
    pass


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int(round((len(values) - 1) * fraction))))]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ReportError(code)


def _validate_event(event: Mapping[str, Any], ordinal: int) -> None:
    _require(isinstance(event, Mapping), "event_invalid")
    _require(event.get("ordinal") == ordinal, "ordinal_invalid")
    _require(event.get("kind") in EVENT_KINDS, "kind_invalid")
    _require(isinstance(event.get("pilot_id"), str) and bool(event.get("pilot_id")), "pilot_invalid")
    _require(isinstance(event.get("turn_id"), str) and bool(event.get("turn_id")), "turn_id_invalid")
    _require(isinstance(event.get("counts_toward_target"), bool), "count_invalid")
    kind = event["kind"]
    if kind == "evaluation":
        _require(isinstance(event.get("valid"), bool), "evaluation_invalid")
        _require(event.get("verdict") in VERDICTS, "verdict_invalid")
        _require(event.get("valid") is False or event.get("model") is not None, "evaluation_invalid")
        _require(isinstance(event.get("fact_types", []), list), "fact_types_invalid")
        _require(all(isinstance(value, str) for value in event.get("fact_types", [])), "fact_types_invalid")
        _require(isinstance(event.get("fact_count", 0), int) and event.get("fact_count", 0) >= 0, "fact_count_invalid")
        _require(event.get("counts_toward_target") is (event.get("valid") is True), "count_reconciliation")
        if event.get("valid") is True:
            _require(isinstance(event.get("model"), str) and bool(event.get("model")), "model_invalid")
        if "policy" in event:
            policy = event["policy"]
            _require(isinstance(policy, Mapping), "policy_invalid")
            for key in ("model", "accepted_models", "should_retain_threshold", "grounded_threshold", "standalone_threshold", "duplicate_threshold", "sensitive_threshold"):
                _require(key in policy, "policy_invalid")
    elif kind == "retain_outcome":
        _require(event.get("status") in RETAIN_STATUSES, "retain_status_invalid")
        _require(isinstance(event.get("fact_types", []), list), "fact_types_invalid")
        _require(all(isinstance(value, str) for value in event.get("fact_types", [])), "fact_types_invalid")
        _require(event.get("counts_toward_target") is False, "reservation_count_invalid")
    else:
        _require(event.get("counts_toward_target") is False, "reservation_count_invalid")


def build_report(events: Sequence[Mapping[str, Any]], *, target: int = 100) -> dict[str, Any]:
    if target != 100:
        raise ReportError("target_invalid")
    records = [dict(event) for event in events]
    identities: set[tuple[str, str]] = set()
    for ordinal, event in enumerate(records, 1):
        _validate_event(event, ordinal)
        identity = (event["turn_id"], event["kind"])
        if identity in identities:
            raise ReportError("duplicate_event")
        identities.add(identity)
    pilots = {event["pilot_id"] for event in records}
    if len(pilots) > 1:
        raise ReportError("pilot_mismatch")
    evaluations = [event for event in records if event["kind"] == "evaluation"]
    valid_evaluations = [event for event in evaluations if event["valid"] is True and event["counts_toward_target"] is True]
    if len(valid_evaluations) > target:
        raise ReportError("target_exceeded")
    verdict_counts = Counter(event["verdict"] for event in valid_evaluations)
    retain = [event for event in records if event["kind"] == "retain_outcome"]
    joined = {event["turn_id"]: event for event in retain}
    if len(joined) != len(retain):
        raise ReportError("duplicate_event")
    if any(turn_id not in {event["turn_id"] for event in evaluations} for turn_id in joined):
        raise ReportError("orphan_retain_outcome")
    latencies = [event["latency_ms"] for event in evaluations if isinstance(event.get("latency_ms"), (int, float)) and not isinstance(event.get("latency_ms"), bool)]
    usage = {"input_tokens": 0, "output_tokens": 0, "cost": 0}
    for event in valid_evaluations:
        value = event.get("usage") or {}
        if not isinstance(value, Mapping): raise ReportError("usage_invalid")
        for key in usage:
            if value.get(key) is not None:
                if not isinstance(value[key], (int, float)) or isinstance(value[key], bool): raise ReportError("usage_invalid")
                usage[key] += value[key]
    errors = Counter(event.get("error_code") for event in records if event.get("error_code"))
    fact_types = Counter(ft for event in valid_evaluations for ft in event.get("fact_types", []))
    false_skips = [event["turn_id"] for event in valid_evaluations if event["verdict"] == "shadow_skip" and event.get("fact_count", 0) > 0]
    retain_counts = Counter(event["status"] for event in retain)
    eligible = {event["turn_id"] for event in valid_evaluations if event["verdict"] in {"shadow_retain", "shadow_buffer"}}
    succeeded = {turn_id for turn_id, event in joined.items() if event["status"] == "succeeded"}
    return {
        "status": "pending_analysis" if len(valid_evaluations) == target else "collecting",
        "target": target, "event_count": len(records), "valid_evaluations": len(valid_evaluations),
        "verdict_counts": {key: verdict_counts.get(key, 0) for key in VERDICTS},
        "models": sorted({event["model"] for event in valid_evaluations if event.get("model")}),
        "latency_ms": {"p50": _percentile(latencies, .5), "p95": _percentile(latencies, .95)},
        "usage": usage, "error_counts": dict(sorted(errors.items())), "candidate_false_skips": false_skips,
        "retain_outcomes": {key: retain_counts.get(key, 0) for key in sorted(RETAIN_STATUSES)},
        "fact_count": sum(event.get("fact_count", 0) for event in valid_evaluations),
        "fact_type_counts": dict(sorted(fact_types.items())),
        "failure_cross_tab": {key: sum(1 for event in evaluations if event.get("error_code") == key) for key in sorted(errors)},
        "eligibility_cross_tab": {"eligible": len(eligible), "ineligible": len(valid_evaluations) - len(eligible), "eligible_and_succeeded": len(eligible & succeeded), "eligible_and_failed": len(eligible - succeeded)},
    }
