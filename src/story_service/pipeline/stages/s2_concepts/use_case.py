"""S2 application layer: orchestrates one stage execution.

Flow per attempt (spec 5.0 and Module 2):
  1. extractor LLM call (temperature 0.2, role ``large``)
  2. structural parse (Pydantic)
  3. code checks (all blockers)       -> on failure, skip the reviewer (it would
                                         only audit an output we already reject)
  4. independent Concept Reviewer call (separate prompt, temperature 0.2)
  5. verdict rules
A failed attempt feeds its failed checks (and the reviewer report) into the next
attempt, up to ``max_attempts``. Provider errors are not handled here: they
propagate to the orchestrator (``PROVIDER_ERROR``).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError

from story_service.pipeline.errors import InvalidLLMOutput, StageExhaustedError
from story_service.pipeline.validation import Check, ValidationReport

from . import prompting, rules
from .models import (
    ConceptExtractionInput,
    ConceptReviewReport,
    ConceptsArtifact,
)
from .ports import AttemptRecord, LLMClient, NullRecorder, StageRecorder

STAGE = "S2_concepts"
TEMPERATURE = 0.2  # extractors and critics (spec 5.0)
MODEL_ROLE = "large"
EXTRACTOR_MAX_TOKENS = 4000
REVIEWER_MAX_TOKENS = 2000
DEFAULT_MAX_ATTEMPTS = 3  # MAX_STAGE_ATTEMPTS


@dataclass(frozen=True)
class StageOutput:
    artifact: ConceptsArtifact
    report: ValidationReport
    attempts: int


def compute_input_hash(
    ctx: ConceptExtractionInput, model_id: str, feedback: Mapping[str, Any] | None = None
) -> str:
    """Cache key: canonical inputs + prompt hash + model id (spec 5.0)."""
    payload = {
        "inputs": ctx.model_dump(mode="json"),
        "feedback": feedback,
        "prompt": prompting.prompt_hash(),
        "model": model_id,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def execute(
    ctx: ConceptExtractionInput,
    llm: LLMClient,
    *,
    recorder: StageRecorder | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    feedback: Mapping[str, Any] | None = None,
) -> StageOutput:
    recorder = recorder or NullRecorder()
    extractor_system, reviewer_system = prompting.system_prompts()
    fb: dict[str, Any] | None = dict(feedback) if feedback else None
    last: ValidationReport | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            raw = llm.complete_json(
                system=extractor_system,
                user=prompting.build_extractor_user(ctx, fb),
                schema=ConceptsArtifact.model_json_schema(),
                model_role=MODEL_ROLE,
                temperature=TEMPERATURE,
                max_tokens=EXTRACTOR_MAX_TOKENS,
            )
        except InvalidLLMOutput as err:
            raw, artifact, review = None, None, None
            report = _report([Check(name="invalid_json", passed=False, severity="blocker", detail=str(err))])
        else:
            artifact, report, review = _evaluate(ctx, raw, llm, reviewer_system)

        last = report
        passed = report.passed
        recorder.record_attempt(
            AttemptRecord(STAGE, attempt, "passed" if passed else "failed", report.model_dump(mode="json"))
        )
        if passed and artifact is not None:
            return StageOutput(artifact=artifact, report=report, attempts=attempt)

        fb = {
            "failed_checks": report.feedback_items(),
            "reviewer_report": review.model_dump(mode="json") if review else None,
            "previous_output": artifact.model_dump(mode="json") if artifact else _jsonable(raw),
        }

    assert last is not None
    raise StageExhaustedError(STAGE, max_attempts, last.issues())


def _evaluate(
    ctx: ConceptExtractionInput,
    raw: Any,
    llm: LLMClient,
    reviewer_system: str,
) -> tuple[ConceptsArtifact | None, ValidationReport, ConceptReviewReport | None]:
    try:
        artifact = ConceptsArtifact.model_validate(raw)
    except ValidationError as err:
        return None, _report([_schema_check("schema", err)]), None

    checks = rules.validate_concepts(artifact, ctx)
    if any(not c.passed and c.severity in ("blocker", "major") for c in checks):
        return artifact, _report(checks), None

    try:
        review_raw = llm.complete_json(
            system=reviewer_system,
            user=prompting.build_reviewer_user(ctx, artifact),
            schema=ConceptReviewReport.model_json_schema(),
            model_role=MODEL_ROLE,
            temperature=TEMPERATURE,
            max_tokens=REVIEWER_MAX_TOKENS,
        )
    except InvalidLLMOutput as err:
        # The extractor output is fine; only the review failed. Keep the artifact
        # so the retry still receives it as ``previous_output``.
        bad = Check(name="reviewer_invalid_json", passed=False, severity="blocker", detail=str(err))
        return artifact, _report(checks + [bad]), None
    try:
        review = ConceptReviewReport.model_validate(review_raw)
    except ValidationError as err:
        return artifact, _report(checks + [_schema_check("reviewer_schema", err)]), None

    checks += rules.evaluate_review(review)
    return artifact, _report(checks), review


def _report(checks: list[Check]) -> ValidationReport:
    return ValidationReport(stage=STAGE, checks=checks)


def _schema_check(name: str, err: ValidationError) -> Check:
    items = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()[:8]]
    return Check(name=name, passed=False, severity="blocker", detail="; ".join(items))


def _jsonable(raw: Any) -> Any:
    if isinstance(raw, BaseModel):
        return raw.model_dump(mode="json")
    try:
        json.dumps(raw)
        return raw
    except TypeError:
        return repr(raw)[:2000]
