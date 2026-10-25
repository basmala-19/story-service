"""Ports: what S2 needs from the outside world.

Both are structural (``Protocol``): the real ``LLMClient`` from the providers
package and the orchestrator's DB-backed recorder satisfy them without
importing anything from this package.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class LLMClient(Protocol):
    """Subset of the spec's ``LLMClient`` (Section 11.2) used by S2.

    Implementations retry transient errors, repair invalid JSON once and record
    token usage. A test double can tell the two S2 calls apart by
    ``schema["title"]`` (``ConceptsArtifact`` or ``ConceptReviewReport``).
    """

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        model_role: str,
        temperature: float,
        max_tokens: int,
        images: Any = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AttemptRecord:
    """One stage attempt; the orchestrator turns it into a ``stage_runs`` row."""

    stage: str
    attempt: int
    status: str  # "passed" | "failed"
    validation: dict[str, Any]


class StageRecorder(Protocol):
    def record_attempt(self, record: AttemptRecord) -> None: ...


class NullRecorder:
    def record_attempt(self, record: AttemptRecord) -> None:  # noqa: D401
        return None
