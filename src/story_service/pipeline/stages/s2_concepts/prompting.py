"""Prompt assembly for S2.

System prompts are files in ``pipeline/prompts``. Their numeric limits are
injected from ``rules.Limits`` so the prompt and the validator cannot drift.
Lesson text is always delimited and treated as data (spec 5.0, untrusted input).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any

from .models import ConceptExtractionInput, ConceptsArtifact
from .rules import LIMITS, Limits

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_EXTRACTOR_FILE = "s2_concept_extractor.md"
_REVIEWER_FILE = "s2_concept_reviewer.md"
_RUBRIC_FILE = "s2_weight_rubric.md"

# Any attempt by the lesson text to open or close our delimiter is removed.
_DELIMITER_RE = re.compile(r"<\s*/?\s*lesson_text[^>]*>", re.IGNORECASE)


def neutralize(text: str) -> str:
    """Remove our own delimiter tags from untrusted text."""
    return _DELIMITER_RE.sub("", text or "")


def _read(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


@lru_cache(maxsize=4)
def system_prompts(limits: Limits = LIMITS) -> tuple[str, str]:
    """Return ``(extractor_system, reviewer_system)`` with limits filled in."""
    values = {k: v for k, v in asdict(limits).items()}
    rubric = Template(_read(_RUBRIC_FILE)).substitute(values)
    extractor = Template(_read(_EXTRACTOR_FILE)).substitute(values, weight_rubric=rubric)
    reviewer = Template(_read(_REVIEWER_FILE)).substitute(values, weight_rubric=rubric)
    return extractor, reviewer


def prompt_hash(limits: Limits = LIMITS) -> str:
    """Hash of the rendered prompts; part of the stage ``input_hash`` (spec 5.0)."""
    extractor, reviewer = system_prompts(limits)
    return hashlib.sha256((extractor + "\n\x00\n" + reviewer).encode("utf-8")).hexdigest()


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def _block(title: str, text: str) -> str:
    return f"## {title}\n<lesson_text>\n{neutralize(text)}\n</lesson_text>"


def build_extractor_user(ctx: ConceptExtractionInput, feedback: dict[str, Any] | None) -> str:
    meta = {
        "topic_title": ctx.topic_title,
        "language": ctx.language,
        "dialect": ctx.dialect_applied,
        "country": ctx.country,
        "age_group": ctx.age_group,
        "grade": ctx.grade,
        "subject": ctx.subject,
    }
    parts = [
        "## Metadata (trusted)\n" + _json(meta),
        "## Localization brief (trusted; ground real_world_uses in it)\n"
        + _json(ctx.localization.model_dump()),
        _block("TOPIC_TEXT (the only source of concepts and evidence)", ctx.topic_text),
        _block("PRIOR_TEXT (what the student already studied)", ctx.prior_text),
        _block("LATER_PREVIEW (next topic: must NOT be taught or quoted)", ctx.later_preview),
    ]
    if feedback:
        parts.append(
            "## Feedback from the previous attempt (fix every item)\n" + _json(feedback)
        )
    return "\n\n".join(parts)


def build_reviewer_user(ctx: ConceptExtractionInput, artifact: ConceptsArtifact) -> str:
    return "\n\n".join(
        [
            "## Metadata (trusted)\n"
            + _json({"language": ctx.language, "age_group": ctx.age_group, "subject": ctx.subject}),
            _block("TOPIC_TEXT", ctx.topic_text),
            "## CONCEPTS\n" + _json(artifact.model_dump(mode="json")),
        ]
    )
