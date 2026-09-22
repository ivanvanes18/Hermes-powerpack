import json
import stat
import os

import pytest

from plugins.memory.hindsight.jev_shadow_report import build_report
from plugins.memory.hindsight.jev_shadow_runtime import ShadowEventStore
from plugins.memory.hindsight.jev_shadow import (
    QUESTIONS,
    ShadowContractError,
    ShadowPolicy,
    ShadowTurn,
    build_shadow_request,
    derive_shadow_verdict,
    validate_shadow_response,
)


def test_build_shadow_request_keeps_only_three_turns_and_questions():
    turns = [ShadowTurn(f"u{i}", f"a{i}") for i in range(4)]
    request = build_shadow_request(turns, ShadowPolicy(model="jev-latest"))
    assert request["model"] == "jev-latest"
    assert [row["user_text"] for row in request["state"]["turns"]] == ["u1", "u2", "u3"]
    assert set(request["questions"]) == set(QUESTIONS)
    assert all("question" not in instruction.lower() for instruction in request["instructions"].values())


def test_build_shadow_request_redacts_and_blocks_excluded_content():
    request = build_shadow_request([ShadowTurn("my password is «redacted:sk-…»", "ack")], ShadowPolicy())
    assert "«redacted:sk-…»" not in json.dumps(request)
    with pytest.raises(ShadowContractError) as exc:
        build_shadow_request([ShadowTurn("contract act № 4", "ack")], ShadowPolicy())
    assert exc.value.code == "egress_blocked"
    assert "contract" not in str(exc.value).lower()


def valid_response(model="jev-latest"):
    return {"model": model, "answers": {
        "should_retain": {"noul": 0.9},
        "memory_kind": {"choice": "preference", "probabilities": {"preference": 1.0, "decision": 0.0, "constraint": 0.0, "fact": 0.0, "status": 0.0, "correction": 0.0, "none": 0.0}},
        "user_grounded": {"noul": 0.9}, "standalone_meaning": {"noul": 0.9}, "likely_duplicate": {"noul": 0.1},
        "retention_priority": {"choice": "retain_now", "probabilities": {"skip": 0.0, "buffer": 0.0, "retain_now": 1.0}},
        "sensitive": {"noul": 0.0},
    }}


@pytest.mark.parametrize("mutator,code", [
    (lambda p: p.update(model="other-model"), "model_mismatch"),
    (lambda p: p["answers"].pop("should_retain"), "answer_missing"),
    (lambda p: p["answers"]["memory_kind"].update(choice="outside"), "choice_invalid"),
    (lambda p: p["answers"]["should_retain"].update(noul=float("nan")), "probability_invalid"),
    (lambda p: p["answers"].update(extra={"noul": 0.1}), "answer_unknown"),
])
def test_validate_shadow_response_is_strict(mutator, code):
    payload = valid_response(); mutator(payload)
    with pytest.raises(ShadowContractError) as exc:
        validate_shadow_response(payload, ShadowPolicy())
    assert exc.value.code == code


def test_validate_shadow_response_returns_typed_answers():
    answers = validate_shadow_response(valid_response(), ShadowPolicy())
    assert answers.memory_kind == "preference"
    assert answers.retention_priority == "retain_now"
    assert answers.should_retain == pytest.approx(0.9)


def test_verdict_fail_open_precedes_skip_and_explicit_memory_retain():
    answers = validate_shadow_response(valid_response(), ShadowPolicy())
    assert derive_shadow_verdict(answers, explicit_memory_request=False, excluded_content=True) == "shadow_fail_open"
    assert derive_shadow_verdict(answers, explicit_memory_request=True, excluded_content=False) == "shadow_retain"


def test_verdict_buffer_and_skip():
    payload = valid_response(); payload["answers"]["should_retain"]["noul"] = 0.4; payload["answers"]["standalone_meaning"]["noul"] = 0.3
    payload["answers"]["retention_priority"] = {"choice": "buffer", "probabilities": {"skip": 0.0, "buffer": 1.0, "retain_now": 0.0}}
    assert derive_shadow_verdict(validate_shadow_response(payload, ShadowPolicy()), explicit_memory_request=False, excluded_content=False) == "shadow_buffer"
    payload["answers"]["memory_kind"] = {"choice": "none", "probabilities": {"preference": 0.0, "decision": 0.0, "constraint": 0.0, "fact": 0.0, "status": 0.0, "correction": 0.0, "none": 1.0}}
    payload["answers"]["should_retain"]["noul"] = 0.01; payload["answers"]["retention_priority"] = {"choice": "skip", "probabilities": {"skip": 1.0, "buffer": 0.0, "retain_now": 0.0}}
    assert derive_shadow_verdict(validate_shadow_response(payload, ShadowPolicy()), explicit_memory_request=False, excluded_content=False) == "shadow_skip"


def test_verdict_uses_every_policy_threshold():
    answers = validate_shadow_response(valid_response(), ShadowPolicy())
    strict = ShadowPolicy(should_retain_threshold=.95, grounded_threshold=.95,
                          standalone_threshold=.95, duplicate_threshold=.05,
                          sensitive_threshold=.01)
    assert derive_shadow_verdict(answers, policy=strict, explicit_memory_request=False,
                                 excluded_content=False) == "shadow_buffer"


def test_event_kinds_join_on_turn_id_without_rejecting_retain_outcome(tmp_path):
    store = ShadowEventStore(tmp_path / "pilot", "p1")
    store.append(sample_event())
    store.append({"kind": "retain_outcome", "pilot_id": "p1", "ordinal": 2,
                  "turn_id": "t1", "counts_toward_target": False,
                  "status": "succeeded", "operation_ids_count": 1,
                  "result_items_count": 1, "fact_count": 1,
                  "fact_types": ["preference"], "latency_ms": 4,
                  "error_code": None})
    report = build_report(store.events())
    assert report["retain_outcomes"]["succeeded"] == 1
    assert report["eligibility_cross_tab"]["eligible_and_succeeded"] == 1


def test_report_uses_retain_metadata_and_distinguishes_missing_from_failed():
    events = [sample_event(1), sample_event(2), sample_event(3)]
    events[0]["fact_count"] = 99
    events[0]["fact_types"] = ["evaluation_only"]
    events.extend([
        {"kind": "retain_outcome", "pilot_id": "p1", "ordinal": 4,
         "turn_id": "t1", "counts_toward_target": False, "status": "succeeded",
         "fact_count": 2, "fact_types": ["preference", "decision"]},
        {"kind": "retain_outcome", "pilot_id": "p1", "ordinal": 5,
         "turn_id": "t3", "counts_toward_target": False, "status": "failed",
         "fact_count": 7, "fact_types": ["fact"]},
    ])
    report = build_report(events)
    assert report["fact_count"] == 9
    assert report["fact_type_counts"] == {"decision": 1, "fact": 1, "preference": 1}
    assert report["retain_outcomes"] == {"failed": 1, "missing": 1, "queued": 0, "succeeded": 1}
    assert report["eligibility_cross_tab"]["eligible_and_failed"] == 1
    assert report["eligibility_cross_tab"]["eligible_and_missing"] == 1


def test_report_rejects_negative_retain_fact_metadata():
    event = sample_event()
    event.update(kind="retain_outcome", ordinal=1, counts_toward_target=False,
                 status="succeeded", fact_count=-1, fact_types=[])
    with pytest.raises(ValueError):
        build_report([event])


def test_output_publication_never_clobbers_final_file(tmp_path, monkeypatch):
    from scripts.jev_hindsight_shadow_report import _safe_output
    output = tmp_path / "report.json"
    original_link = os.link

    def competing_link(*args, **kwargs):
        output.write_text("competitor")
        return original_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", competing_link)
    with pytest.raises(FileExistsError):
        _safe_output(output, "ours\n")
    assert output.read_text() == "competitor"


def test_output_cleanup_preserves_substituted_temp_inode(tmp_path, monkeypatch):
    from scripts.jev_hindsight_shadow_report import _safe_output
    output = tmp_path / "report.json"
    temp = tmp_path / f".{output.name}.{os.getpid()}.tmp"
    original_link = os.link

    def substitute_then_fail(*args, **kwargs):
        original_link(*args, **kwargs)
        temp.unlink()
        temp.write_text("competitor")
        raise OSError("publication failed")

    monkeypatch.setattr(os, "link", substitute_then_fail)
    with pytest.raises(OSError, match="publication failed"):
        _safe_output(output, "ours\n")
    assert temp.read_text() == "competitor"


def sample_event(ordinal=1, turn_id=None, verdict="shadow_retain"):
    return {"kind": "evaluation", "pilot_id": "p1", "ordinal": ordinal, "turn_id": turn_id or f"t{ordinal}", "valid": True, "counts_toward_target": True, "verdict": verdict, "model": "jev-latest", "latency_ms": 10, "usage": {"input_tokens": 3, "output_tokens": 2, "cost": 0.1}, "error_code": None, "fact_types": ["preference"], "fact_count": 1}


def test_event_store_is_private_durable_and_idempotent(tmp_path):
    store = ShadowEventStore(tmp_path / "pilot", pilot_id="p1", target=100)
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    store.append(sample_event())
    assert stat.S_IMODE(store.events_path.stat().st_mode) == 0o600
    assert store.reserve("t1") is False
    with pytest.raises(ValueError): store.append(sample_event(ordinal=3, turn_id="t3"))
    assert store.snapshot().valid_evaluations == 1


def test_event_store_rejects_plaintext_and_nonpilot_target(tmp_path):
    with pytest.raises(ValueError): ShadowEventStore(tmp_path / "pilot", pilot_id="p1", target=99)
    store = ShadowEventStore(tmp_path / "pilot2", pilot_id="p1")
    bad = sample_event(); bad["prompt"] = "plaintext"
    with pytest.raises(ValueError): store.append(bad)


def test_report_reconciles_counts_and_collecting_status():
    report = build_report([sample_event(1), sample_event(2, verdict="shadow_skip")], target=100)
    assert report["status"] == "collecting"
    assert report["valid_evaluations"] == 2
    assert report["verdict_counts"] == {"shadow_retain": 1, "shadow_buffer": 0, "shadow_skip": 1, "shadow_fail_open": 0}


def test_report_rejects_malformed_and_duplicate_event_kinds():
    event = sample_event()
    with pytest.raises(ValueError): build_report([{**event, "verdict": "not-a-verdict"}])
    with pytest.raises(ValueError): build_report([event, {**event, "ordinal": 2}])
