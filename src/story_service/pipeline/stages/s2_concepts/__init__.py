"""S2 Concept Extractor.

Public surface used by the orchestrator and by tests::

    from story_service.pipeline.stages import s2_concepts
    concepts = s2_concepts.run(intake=intake, brief=brief, llm=llm)

Layers (dependencies point inward only):
    models.py    domain data (no I/O)
    rules.py     domain validation (pure functions)
    ports.py     interfaces S2 needs (LLM, recorder)
    prompting.py prompt files + message building
    use_case.py  retry loop and orchestration of one stage execution
"""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel

from story_service.pipeline.errors import StageExhaustedError

from .models import (
    Concept,
    ConceptExtractionInput,
    ConceptReviewReport,
    ConceptsArtifact,
)
from .ports import AttemptRecord, LLMClient, NullRecorder, StageRecorder
from .use_case import (
    DEFAULT_MAX_ATTEMPTS,
    STAGE,
    StageOutput,
    compute_input_hash,
    execute,
)


def run_detailed(
    intake: Mapping[str, Any] | BaseModel,
    brief: Mapping[str, Any] | BaseModel,
    llm: LLMClient,
    *,
    recorder: StageRecorder | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    feedback: Mapping[str, Any] | None = None,
) -> StageOutput:
    """Run S2 and return the artifact together with the validation report."""
    ctx = ConceptExtractionInput.from_artifacts(intake, brief)
    return execute(ctx, llm, recorder=recorder, max_attempts=max_attempts, feedback=feedback)


def run(
    intake: Mapping[str, Any] | BaseModel,
    brief: Mapping[str, Any] | BaseModel,
    llm: LLMClient,
    *,
    recorder: StageRecorder | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    feedback: Mapping[str, Any] | None = None,
) -> ConceptsArtifact:
    """Run S2 and return ``concepts.json`` as a model.

    Raises ``StageExhaustedError`` (``VALIDATION_EXHAUSTED``) when all attempts fail.
    """
    return run_detailed(
        intake, brief, llm, recorder=recorder, max_attempts=max_attempts, feedback=feedback
    ).artifact


__all__ = [
    "AttemptRecord",
    "Concept",
    "ConceptExtractionInput",
    "ConceptReviewReport",
    "ConceptsArtifact",
    "LLMClient",
    "NullRecorder",
    "STAGE",
    "StageExhaustedError",
    "StageOutput",
    "StageRecorder",
    "compute_input_hash",
    "execute",
    "run",
    "run_detailed",
]
