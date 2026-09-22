"""Pure contract and policy helpers for the Jev Hindsight shadow pilot."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent.redact import redact_sensitive_text

SHADOW_RETAIN = "shadow_retain"
SHADOW_BUFFER = "shadow_buffer"
SHADOW_SKIP = "shadow_skip"
SHADOW_FAIL_OPEN = "shadow_fail_open"

QUESTIONS: dict[str, dict[str, Any]] = {
    "should_retain": {"type": "noul", "instructions": "Does this window contain durable information useful after the conversation?"},
    "memory_kind": {"type": "choice", "instructions": "Select the best kind of durable user-grounded information.", "criteria": {
        "preference": "A user preference or stable choice.", "decision": "A decision the user made or explicitly accepted.", "constraint": "A user requirement, boundary, or constraint.",
        "fact": "A durable fact directly supported by the user.", "status": "A materially changed status that remains useful.", "correction": "A correction to prior information.", "none": "No durable memory; filler or transient content.",
    }},
    "user_grounded": {"type": "noul", "instructions": "Is the candidate directly supported by the user or explicitly accepted by the user?"},
    "standalone_meaning": {"type": "noul", "instructions": "Can the durable meaning be understood outside this exchange?"},
    "likely_duplicate": {"type": "noul", "instructions": "Does this merely repeat durable information in the bounded window?"},
    "retention_priority": {"type": "choice", "instructions": "Estimate timing for retention, not authority.", "criteria": {
        "skip": "No durable information should be retained.", "buffer": "Potentially durable but context-dependent or uncertain.", "retain_now": "Durable information should be retained now.",
    }},
    "sensitive": {"type": "noul", "instructions": "Does the state contain excluded, credential-like, contractual, or act-related material?"},
}

_BLOCK_PATTERNS = (
    re.compile(r"(?i)\b(?:authorization\s*:\s*bearer|bearer\s+[a-z0-9._~-]{8,})\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:sk|jv_live)_[a-z0-9_-]{6,}\b"),
    re.compile(r"(?i)\b(?:contract|договор|act|акт)\b"),
    re.compile(r"(?i)<\s*memory-context\b|\b(?:tool\s+output|recalled\s+memory)\b"),
    re.compile(r"(?i)\b(?:telegram|session)\s+(?:chat\s+)?(?:id|identifier)\b"),
    re.compile(r"(?i)\bapi\s*key\s*[:=]\s*(?![«\[]?redacted)|https?://[^\s/@]+:[^\s/@]+@"),
)
_JEV_VERSION_RE = re.compile(r"^jev-(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")

class ShadowContractError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

@dataclass(frozen=True)
class ShadowTurn:
    user_text: str
    assistant_text: str

@dataclass(frozen=True)
class ShadowPolicy:
    model: str = "jev-latest"
    accepted_models: tuple[str, ...] = ()
    should_retain_threshold: float = 0.70
    grounded_threshold: float = 0.70
    standalone_threshold: float = 0.60
    duplicate_threshold: float = 0.80
    sensitive_threshold: float = 0.20

    def models(self) -> tuple[str, ...]:
        return self.accepted_models or (self.model,)

@dataclass(frozen=True)
class ShadowAnswers:
    should_retain: float
    memory_kind: str
    user_grounded: float
    standalone_meaning: float
    likely_duplicate: float
    retention_priority: str
    sensitive: float


def _safe_text(value: str) -> str:
    if not isinstance(value, str):
        raise ShadowContractError("text_invalid")
    redacted = redact_sensitive_text(value, force=True, redact_url_credentials=True)
    if any(pattern.search(value) or pattern.search(redacted) for pattern in _BLOCK_PATTERNS):
        raise ShadowContractError("egress_blocked")
    return redacted


def build_shadow_request(turns: Sequence[ShadowTurn], policy: ShadowPolicy) -> dict[str, Any]:
    if not turns:
        raise ShadowContractError("turns_empty")
    bounded = list(turns)[-3:]
    rows = []
    for index, turn in enumerate(bounded):
        rows.append({"relative_order": index - len(bounded) + 1, "user_text": _safe_text(turn.user_text), "assistant_text": _safe_text(turn.assistant_text)})
    return {
        "model": policy.model,
        "state": {"turns": rows, "grounding_rule": "Assistant claims, recalled context, and tool output are not user-grounded unless the user explicitly confirms them."},
        "questions": {key: {"type": value["type"], "instructions": value["instructions"], **({"criteria": value["criteria"]} if "criteria" in value else {})} for key, value in QUESTIONS.items()},
    }


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ShadowContractError("probability_invalid")
    return float(value)


def _choice(answer: Any, options: set[str]) -> tuple[str, Mapping[str, Any]]:
    if (
        not isinstance(answer, Mapping)
        or set(answer) != {"choice", "confidence", "probabilities", "type"}
        or answer["type"] != "choice"
        or answer["choice"] not in options
        or not isinstance(answer["probabilities"], Mapping)
        or set(answer["probabilities"]) != options
    ):
        raise ShadowContractError("choice_invalid")
    _probability(answer["confidence"])
    probabilities = {key: _probability(value) for key, value in answer["probabilities"].items()}
    if abs(sum(probabilities.values()) - 1.0) > 1e-6:
        raise ShadowContractError("probability_sum_invalid")
    return str(answer["choice"]), probabilities


def validate_shadow_response(payload: Mapping[str, Any], policy: ShadowPolicy) -> ShadowAnswers:
    if not isinstance(payload, Mapping):
        raise ShadowContractError("response_invalid")
    response_model = payload.get("model")
    accepted_models = policy.models()
    if response_model not in accepted_models and not (
        isinstance(response_model, str)
        and "jev-latest" in accepted_models
        and _JEV_VERSION_RE.fullmatch(response_model) is not None
    ):
        raise ShadowContractError("model_mismatch")
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise ShadowContractError("answers_invalid")
    if set(answers) != set(QUESTIONS):
        missing = set(QUESTIONS) - set(answers)
        raise ShadowContractError("answer_missing" if missing else "answer_unknown")
    def noul(name: str) -> float:
        item = answers[name]
        if not isinstance(item, Mapping) or set(item) != {"noul", "type"} or item["type"] != "noul":
            raise ShadowContractError("answer_type_invalid")
        return _probability(item["noul"])
    memory_kind, _ = _choice(answers["memory_kind"], set(QUESTIONS["memory_kind"]["criteria"]))
    priority, _ = _choice(answers["retention_priority"], set(QUESTIONS["retention_priority"]["criteria"]))
    return ShadowAnswers(noul("should_retain"), memory_kind, noul("user_grounded"), noul("standalone_meaning"), noul("likely_duplicate"), priority, noul("sensitive"))


def derive_shadow_verdict(answers: ShadowAnswers, *, policy: ShadowPolicy | None = None, explicit_memory_request: bool, excluded_content: bool) -> str:
    policy = policy or ShadowPolicy()
    if excluded_content or answers.sensitive >= policy.sensitive_threshold:
        return SHADOW_FAIL_OPEN
    if explicit_memory_request or answers.memory_kind in {"correction", "decision", "constraint"}:
        return SHADOW_RETAIN
    if answers.should_retain >= policy.should_retain_threshold and answers.user_grounded >= policy.grounded_threshold and answers.retention_priority == "retain_now" and answers.likely_duplicate < policy.duplicate_threshold and answers.standalone_meaning >= policy.standalone_threshold:
        return SHADOW_RETAIN
    if answers.retention_priority == "skip" and answers.should_retain < policy.should_retain_threshold and answers.memory_kind == "none":
        return SHADOW_SKIP
    if answers.should_retain >= policy.should_retain_threshold or answers.retention_priority == "buffer" or answers.standalone_meaning < policy.standalone_threshold:
        return SHADOW_BUFFER
    return SHADOW_FAIL_OPEN
