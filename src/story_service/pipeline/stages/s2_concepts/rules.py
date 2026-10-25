"""S2 validation rules (pure functions, no I/O, no LLM).

Implements the spec's code checks (all blockers) and the Concept Reviewer
verdict rules. Every check has a stable name so retries, reports and tests can
refer to it.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from story_service.pipeline.validation import Check, Severity
from story_service.validators.text import contains_phrase, norm_ar, word_count

from .models import ConceptExtractionInput, ConceptReviewReport, ConceptsArtifact

_ID_RE = re.compile(r"c[1-9]\d*")


@dataclass(frozen=True)
class Limits:
    min_concepts: int = 2
    max_concepts: int = 8
    min_must: int = 1
    max_must: int = 4
    max_title_words: int = 8
    max_definition_words: int = 40
    min_evidence: int = 1
    max_evidence: int = 2
    min_quote_words: int = 5
    max_quote_words: int = 40
    min_uses: int = 1
    max_uses: int = 3
    max_use_words: int = 25  # situation + why_it_matters together


LIMITS = Limits()


def _check(name: str, problems: list[str], severity: Severity = "blocker", ok: str = "") -> Check:
    return Check(
        name=name,
        passed=not problems,
        severity=severity,
        detail="; ".join(problems) if problems else ok,
    )


def _stems(text: str) -> set[str]:
    """Content tokens of ``text`` after ``norm_ar`` with the Arabic article stripped."""
    return {
        t[2:] if t.startswith("ال") and len(t) > 4 else t
        for t in norm_ar(text).split()
        if len(t) >= 3
    }


def _short(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# --------------------------------------------------------------------------
# Code checks on the extractor output
# --------------------------------------------------------------------------
def validate_concepts(
    artifact: ConceptsArtifact, ctx: ConceptExtractionInput, limits: Limits = LIMITS
) -> list[Check]:
    cs = artifact.concepts
    checks: list[Check] = []

    # ---- counts and weights ------------------------------------------------
    n = len(cs)
    checks.append(
        _check(
            "concept_count",
            [] if limits.min_concepts <= n <= limits.max_concepts
            else [f"{n} concepts; need {limits.min_concepts} to {limits.max_concepts}. "
                  "Merge closely related ideas or add missing ones."],
        )
    )
    must = sum(c.weight == "must" for c in cs)
    checks.append(
        _check(
            "must_count",
            [] if limits.min_must <= must <= limits.max_must
            else [f"{must} 'must' concepts; need {limits.min_must} to {limits.max_must}. "
                  "'must' = the student cannot apply the topic without it."],
        )
    )

    # ---- ids -----------------------------------------------------------------
    ids = [c.id for c in cs]
    checks.append(
        _check("id_format", [f"bad id '{i}' (expected c1, c2, ...)" for i in ids if not _ID_RE.fullmatch(i)])
    )
    dups = sorted(i for i, k in Counter(ids).items() if k > 1)
    checks.append(_check("ids_unique", [f"duplicate id '{i}'" for i in dups]))
    checks.append(
        _check(
            "ids_sequential",
            [f"position {pos}: expected 'c{pos}', got '{got}' (ids must be c1, c2, ... in order)"
             for pos, got in enumerate(ids, start=1) if got != f"c{pos}"],
        )
    )

    # ---- required text -------------------------------------------------------
    empty: list[str] = []
    for c in cs:
        if not c.title.strip():
            empty.append(f"{c.id}: empty title")
        if not c.definition.strip():
            empty.append(f"{c.id}: empty definition")
        if any(not q.strip() for q in c.evidence):
            empty.append(f"{c.id}: empty evidence quote")
        for u in c.real_world_uses:
            if not u.situation.strip() or not u.why_it_matters.strip():
                empty.append(f"{c.id}: incomplete real_world_use")
        m = c.misconception
        if m is not None and (not m.wrong_belief.strip() or not m.why_it_happens.strip()):
            empty.append(f"{c.id}: incomplete misconception (use null when there is none)")
    checks.append(_check("required_text", empty))

    # ---- word limits ---------------------------------------------------------
    checks.append(
        _check(
            "title_words",
            [f"{c.id}: title has {word_count(c.title)} words (max {limits.max_title_words})"
             for c in cs if word_count(c.title) > limits.max_title_words],
        )
    )
    checks.append(
        _check(
            "definition_words",
            [f"{c.id}: definition has {word_count(c.definition)} words (max {limits.max_definition_words})"
             for c in cs if word_count(c.definition) > limits.max_definition_words],
        )
    )

    # ---- evidence ------------------------------------------------------------
    count_bad, size_bad = [], []
    topic_n, later_n = norm_ar(ctx.topic_text), norm_ar(ctx.later_preview)
    missing, from_later = [], []
    for c in cs:
        if not limits.min_evidence <= len(c.evidence) <= limits.max_evidence:
            count_bad.append(
                f"{c.id}: {len(c.evidence)} quotes (need {limits.min_evidence} to {limits.max_evidence})"
            )
        for q in c.evidence:
            w = word_count(q)
            if not limits.min_quote_words <= w <= limits.max_quote_words:
                size_bad.append(
                    f"{c.id}: quote of {w} words (need {limits.min_quote_words} to "
                    f"{limits.max_quote_words}): '{_short(q)}'"
                )
            qn = norm_ar(q)
            if contains_phrase(topic_n, qn):
                continue
            if later_n and contains_phrase(later_n, qn):
                from_later.append(f"{c.id}: quote comes from the NEXT topic: '{_short(q)}'")
            else:
                missing.append(
                    f"{c.id}: quote not found verbatim in topic_text: '{_short(q)}'. "
                    "Copy one contiguous stretch exactly."
                )
    checks.append(_check("evidence_count", count_bad))
    checks.append(_check("evidence_words", size_bad))
    checks.append(_check("evidence_verbatim", missing))
    checks.append(_check("evidence_not_from_later_preview", from_later))

    # ---- real-world uses -----------------------------------------------------
    uses_bad = []
    for c in cs:
        k = len(c.real_world_uses)
        if not limits.min_uses <= k <= limits.max_uses:
            uses_bad.append(f"{c.id}: {k} real_world_uses (need {limits.min_uses} to {limits.max_uses})")
        for u in c.real_world_uses:
            w = word_count(u.situation) + word_count(u.why_it_matters)
            if w > limits.max_use_words:
                uses_bad.append(f"{c.id}: a real_world_use has {w} words (max {limits.max_use_words})")
    checks.append(_check("real_world_uses", uses_bad))

    # ---- assumed prior knowledge --------------------------------------------
    prior_bad = []
    if not ctx.prior_text.strip() and artifact.assumed_prior_knowledge:
        prior_bad.append(
            "assumed_prior_knowledge must be empty because prior_text is empty "
            "(it may only list what prior_text shows the student already studied)"
        )
    if any(not s.strip() for s in artifact.assumed_prior_knowledge):
        prior_bad.append("assumed_prior_knowledge contains an empty string")
    checks.append(_check("assumed_prior_knowledge", prior_bad))
    # Soft check: paraphrase is allowed, but an item sharing no content word with
    # prior_text is probably not "taken from" it. Warning only, never a blocker.
    prior_tokens = _stems(ctx.prior_text)
    unsupported = []
    if prior_tokens:
        for item in artifact.assumed_prior_knowledge:
            toks = _stems(item)
            if toks and not (toks & prior_tokens):
                unsupported.append(f"'{_short(item)}' shares no content word with prior_text")
    checks.append(_check("assumed_prior_knowledge_support", unsupported, severity="minor"))

    # ---- polish (minor) ------------------------------------------------------
    seen: dict[str, str] = {}
    twins = []
    for c in cs:
        key = norm_ar(c.title)
        if key in seen:
            twins.append(f"{c.id} and {seen[key]} have the same title")
        seen.setdefault(key, c.id)
    checks.append(_check("unique_titles", twins, severity="minor"))

    return checks


# --------------------------------------------------------------------------
# Concept Reviewer verdict
# --------------------------------------------------------------------------
def evaluate_review(review: ConceptReviewReport) -> list[Check]:
    """Blocker if a missing concept is must/important or any concept is invented."""
    serious = [m for m in review.missing_concepts if m.suggested_weight in ("must", "important")]
    optional = [m for m in review.missing_concepts if m.suggested_weight == "optional"]
    return [
        _check(
            "reviewer_missing_concepts",
            [f"missing {m.suggested_weight} concept '{m.title}': {m.reason}" for m in serious],
        ),
        _check(
            "reviewer_invented_concepts",
            [f"concept {i} is not supported by topic_text" for i in review.invented_concepts],
        ),
        _check(
            "reviewer_optional_missing",
            [f"optional concept left out: '{m.title}'" for m in optional],
            severity="minor",
        ),
        _check(
            "reviewer_weight_issues",
            [f"{w.id}: {w.issue}" for w in review.weight_issues],
            severity="minor",
        ),
    ]
