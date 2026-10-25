"""Pipeline-level errors raised by stages and handled by the orchestrator."""
from __future__ import annotations

from typing import Any


class StageExhaustedError(Exception):
    """A stage used all its attempts without passing validation.

    The orchestrator maps this to version status ``needs_review`` with error
    code ``VALIDATION_EXHAUSTED`` and skips the remaining stages (spec 5.0, 12.1).
    """

    code = "VALIDATION_EXHAUSTED"

    def __init__(self, stage: str, attempts: int, issues: list[dict[str, Any]]):
        self.stage = stage
        self.attempts = attempts
        self.issues = issues
        summary = "; ".join(f"{i['code']}: {i['message']}" for i in issues[:5])
        super().__init__(f"{stage} failed after {attempts} attempts. {summary}")


class InvalidLLMOutput(Exception):
    """The LLM reply was not valid JSON even after the adapter's one repair attempt.

    Stages count this as a failed attempt (it is a validation failure, not a
    provider failure), so the stage retries with feedback.
    """
