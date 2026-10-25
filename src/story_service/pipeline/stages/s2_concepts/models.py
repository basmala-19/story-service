"""S2 domain models (no I/O).

Pydantic enforces *structure* only (keys, types, enums). Every numeric or
length limit lives in ``rules.py`` so that a violation produces a named check
with a precise fix hint instead of one opaque schema error.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

Weight = Literal["must", "important", "optional"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# --------------------------------------------------------------------------
# Output artifact: concepts.json
# --------------------------------------------------------------------------
class RealWorldUse(_Model):
    situation: str
    why_it_matters: str


class Misconception(_Model):
    wrong_belief: str
    why_it_happens: str


class Concept(_Model):
    id: str
    title: str
    weight: Weight
    definition: str
    evidence: list[str]
    real_world_uses: list[RealWorldUse]
    misconception: Misconception | None = None


class ConceptsArtifact(_Model):
    """``concepts.json``: the only thing downstream stages (S3, S4) consume."""

    concepts: list[Concept]
    assumed_prior_knowledge: list[str] = Field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [c.id for c in self.concepts]

    def ids_with_weight(self, weight: Weight) -> list[str]:
        return [c.id for c in self.concepts if c.weight == weight]

    @property
    def must_ids(self) -> list[str]:
        return self.ids_with_weight("must")

    @property
    def important_ids(self) -> list[str]:
        return self.ids_with_weight("important")


# --------------------------------------------------------------------------
# Concept Reviewer output (separate LLM call)
# --------------------------------------------------------------------------
class MissingConcept(_Model):
    title: str
    reason: str
    suggested_weight: Weight


class WeightIssue(_Model):
    id: str
    issue: str


class ConceptReviewReport(_Model):
    missing_concepts: list[MissingConcept] = Field(default_factory=list)
    invented_concepts: list[str] = Field(default_factory=list)
    weight_issues: list[WeightIssue] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Input: S2 owns its own view of what it needs from S0 and S1.
# --------------------------------------------------------------------------
class LocalizationContext(_Model):
    """The slice of ``brief.json`` S2 needs, to ground ``real_world_uses``."""

    currency_name: str = ""
    places: list[str] = Field(default_factory=list)
    foods: list[str] = Field(default_factory=list)
    transport: list[str] = Field(default_factory=list)
    holidays_and_seasons: list[str] = Field(default_factory=list)
    school_context: str = ""
    sensitivities: list[str] = Field(default_factory=list)


class ConceptExtractionInput(_Model):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, frozen=True)

    topic_title: str
    topic_text: str
    prior_text: str = ""
    later_preview: str = ""
    language: Literal["ar", "en"]
    dialect_applied: str | None = None
    country: str
    age_group: str
    grade: str
    subject: str
    localization: LocalizationContext = Field(default_factory=LocalizationContext)

    @classmethod
    def from_artifacts(
        cls, intake: Mapping[str, Any] | BaseModel, brief: Mapping[str, Any] | BaseModel
    ) -> "ConceptExtractionInput":
        """Anti-corruption layer: adapt S0 ``intake.json`` and S1 ``brief.json``."""
        i, b = _as_dict(intake), _as_dict(brief)
        currency = b.get("currency") or {}
        loc = LocalizationContext(
            currency_name=(currency.get("spoken_name") or "") if isinstance(currency, dict) else "",
            places=list(b.get("places") or [])[:12],
            foods=list(b.get("foods") or [])[:12],
            transport=list(b.get("transport") or [])[:6],
            holidays_and_seasons=list(b.get("holidays_and_seasons") or [])[:6],
            school_context=b.get("school_context") or "",
            sensitivities=list(b.get("sensitivities") or [])[:8],
        )
        return cls(
            topic_title=i["topic_title"],
            topic_text=i["topic_text"],
            prior_text=i.get("prior_text") or "",
            later_preview=i.get("later_preview") or "",
            language=i["language"],
            dialect_applied=i.get("dialect_applied"),
            country=i["country"],
            age_group=i["age_group"],
            grade=str(i["grade"]),
            subject=i["subject"],
            localization=loc,
        )


def _as_dict(obj: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    return dict(obj)
