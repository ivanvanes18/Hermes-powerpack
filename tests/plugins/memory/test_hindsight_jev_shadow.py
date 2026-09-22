import json
import math
import os
import stat

import pytest

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
    request = build_shadow_request(
        [ShadowTurn("my password is sk-1234567890", "ack")], ShadowPolicy()
    )
    assert "sk-1234567890" not in json.dumps(request)
    with pytest.raises(ShadowContractError) as exc:
        build_shadow_request([ShadowTurn("contract act № 4", "ack")], ShadowPolicy())
    assert exc.value.code == "egress_blocked"
    assert "contract" not in str(exc.value).lower()


def valid_response(model="jev-latest"):
    return {
        "model": model,
        "answers": {
            "should_retain": {"noul": 0.9},
            "memory_kind": {"choice": "preference", "probabilities": {
                "preference": 1.0, "decision": 0.0, "constraint": 0.0,
                "fact": 0.0, "status": 0.0, "correction": 0.0, "none": 0.0,
            }},
            "user_grounded": {"noul": 0.9},
            "standalone_meaning": {"noul": 0.9},
            "likely_duplicate": {"noul": 0.1},
            "retention_priority": {"choice": "retain_now", "probabilities": {
                "skip": 0.0, "buffer": 0.0, "retain_now": 1.0,
            }},
            "sensitive": {"noul": 0.0},
        },
    }


@pytest.mark.parametrize(
    "mutator,code",
    [
        (lambda p: p.update(model="other-model"), "model_mismatch"),
        (lambda p: p["answers"].pop("should_retain"), "answer_missing"),
        (lambda p: p["answers"]["memory_kind"].update(choice="outside"), "choice_invalid"),
        (lambda p: p["answers"]["should_retain"].update(noul=float("nan")), "probability_invalid"),
        (lambda p: p["answers"].update(extra={"noul": 0.1}), "answer_unknown"),
    ],
)
def test_validate_shadow_response_is_strict(mutator, code):
    payload = valid_response()
    mutator(payload)
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
    payload = valid_response()
    payload["answers"]["should_retain"]["noul"] = 0.4
    payload["answers"]["standalone_meaning"]["noul"] = 0.3
    payload["answers"]["retention_priority"] = {"choice": "buffer", "probabilities": {"skip": 0.0, "buffer": 1.0, "retain_now": 0.0}}
    answers = validate_shadow_response(payload, ShadowPolicy())
    assert derive_shadow_verdict(answers, explicit_memory_request=False, excluded_content=False) == "shadow_buffer"
    payload["answers"]["memory_kind"] = {"choice": "none", "probabilities": {"preference": 0.0, "decision": 0.0, "constraint": 0.0, "fact": 0.0, "status": 0.0, "correction": 0.0, "none": 1.0}}
    payload["answers"]["should_retain"]["noul"] = 0.01
    payload["answers"]["retention_priority"] = {"choice": "skip", "probabilities": {"skip": 1.0, "buffer": 0.0, "retain_now": 0.0}}
    answers = validate_shadow_response(payload, ShadowPolicy())
    assert derive_shadow_verdict(answers, explicit_memory_request=False, excluded_content=False) == "shadow_skip"
