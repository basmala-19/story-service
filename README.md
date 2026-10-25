# S2 Concept Extractor (story-service)

Stage **S2** of the Interactive Lesson Stories pipeline. It reads one lesson topic and returns `concepts.json`: the standalone learning concepts of that topic, each with a weight (`must`, `important`, `optional`), a definition, verbatim evidence, real-world uses and an optional misconception. The Story Idea stage (S3) and the Story Writer (S4) build on this file.

Status: implemented and unit-tested (48 tests, no network). **Not yet verified** against a real LLM or inside the full repository. See [Status and open items](#status-and-open-items).

## How it works

```
intake.json + brief.json (+ optional feedback)
        |
        v
 1. Extractor LLM            large model, temperature 0.2, own prompt
        |
 2. Python validation        schema, counts, word limits, ids, verbatim evidence
        |  fail -> retry with the failed checks, skip the reviewer
        v
 3. Independent Reviewer LLM  large model, separate prompt: missing / invented / weights
        |  blocker -> retry with the report
        v
 concepts.json               (max 3 attempts, else StageExhaustedError)
```

The LLM does the understanding (extraction, weighting, review). Python does everything that can be checked exactly. The stage depends only on the `LLMClient` interface, not on any provider.

### What counts as a failure

| Source | Check | Severity |
|---|---|---|
| Extractor | `schema`, `invalid_json` | blocker |
| Python | `concept_count` (2 to 8), `must_count` (1 to 4), `id_format`, `ids_unique`, `ids_sequential` (`c1..cN` in order) | blocker |
| Python | `required_text`, `title_words` (max 8), `definition_words` (max 40) | blocker |
| Python | `evidence_count` (1 to 2), `evidence_words` (5 to 40), `evidence_verbatim` (found in `topic_text` after `norm_ar`), `evidence_not_from_later_preview` | blocker |
| Python | `real_world_uses` (1 to 3 items, max 25 words each, both fields together), `assumed_prior_knowledge` (must be empty when `prior_text` is empty) | blocker |
| Python | `unique_titles`, `assumed_prior_knowledge_support` | minor (warning) |
| Reviewer | `reviewer_missing_concepts` (missing concept weighted `must` or `important`), `reviewer_invented_concepts`, `reviewer_invalid_json`, `reviewer_schema` | blocker |
| Reviewer | `reviewer_optional_missing`, `reviewer_weight_issues` | minor (warning) |

A stage passes when no blocker or major check failed. Minors are returned in the report as warnings.

### Weights

The rubric (`src/story_service/pipeline/stages/s2_concepts/prompts/s2_weight_rubric.md`) is shared by both prompts:

- **must**: the student cannot apply the topic without it. Later stages require every `must` concept to be shown in the story plot. Between 1 and 4 per topic.
- **important**: needed for fuller or more correct understanding, but the basic task still works without it.
- **optional**: enrichment. Nothing breaks without it.

## Repository layout

```
src/story_service/
  validators/text.py               norm_ar, contains_phrase, word_count (shared)
  pipeline/
    validation.py                  Check, ValidationReport (shared)
    errors.py                      StageExhaustedError, InvalidLLMOutput (shared)
    stages/s2_concepts/
      models.py                    domain models + input adapter (no I/O)
      rules.py                     pure validation rules and reviewer verdict
      ports.py                     LLMClient and StageRecorder interfaces
      prompting.py                 prompt loading + message building + injection hygiene
      prompts/                     extractor, reviewer, and shared weight rubric
      use_case.py                  attempt loop, retries, input hash
      __init__.py                  public API: run(), run_detailed()
  providers/                       OPTIONAL: Gemini adapter (see below)
scripts/s2_live_smoke.py           live smoke test against a real provider
tests/unit/                        test_s2_concepts.py, test_gemini_adapter.py
tests/fixtures/                    golden intake/brief, LLM fixtures, real book lesson
docs/                              DECISIONS.md, TESTING_S2.md
```

## Setup

Python 3.11 or newer.

Windows (PowerShell), from inside the `story-service` folder:

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -q
```

Mac or Linux:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

Expected: `48 passed`. No API key is needed for the tests. In VS Code, open the `story-service` folder itself, select the `.venv` interpreter, and use the Testing sidebar (settings are in `.vscode/`).

## Using the stage

```python
from story_service.pipeline.errors import StageExhaustedError
from story_service.pipeline.stages import s2_concepts

try:
    out = s2_concepts.run_detailed(
        intake, brief, llm,                 # dicts or models; llm implements LLMClient
        recorder=recorder,                  # optional; writes one record per attempt
        max_attempts=3,                     # MAX_STAGE_ATTEMPTS
        feedback=None,                      # optional feedback from an earlier run
    )
    concepts_json = out.artifact.model_dump(mode="json")   # the artifact
    warnings = out.report.warnings                          # minor findings
except StageExhaustedError as err:
    err.code      # "VALIDATION_EXHAUSTED"
    err.issues    # unresolved blockers, in the API `issues` shape
```

- `s2_concepts.run(...)` returns just the `ConceptsArtifact`.
- Provider failures (`ProviderError`) are not caught here; the orchestrator maps them to `PROVIDER_ERROR`.
- Cache key for resume: `s2_concepts.compute_input_hash(ConceptExtractionInput.from_artifacts(intake, brief), model_id)`.
- Helpers on the artifact: `.ids`, `.must_ids`, `.important_ids`.

### Input (read from S0 and S1)

From `intake.json`: `topic_title`, `topic_text`, `prior_text`, `later_preview`, `language` (`ar` or `en`), `dialect_applied`, `country`, `age_group`, `grade`, `subject`.
From `brief.json`: `currency.spoken_name`, `places`, `foods`, `transport`, `holidays_and_seasons`, `school_context`, `sensitivities`.

If S0 or S1 rename any of these fields, update `ConceptExtractionInput.from_artifacts`.

### Output

`concepts.json` exactly as in the module spec: `concepts[]` (`id`, `title`, `weight`, `definition`, `evidence[]`, `real_world_uses[]`, `misconception`) and `assumed_prior_knowledge[]`.

### Interfaces the stage expects

- `LLMClient.complete_json(system, user, schema, model_role, temperature, max_tokens, images=None) -> dict`, synchronous. S2 calls it with `model_role="large"` and temperature `0.2`. A test double can tell the two calls apart by `schema["title"]` (`ConceptsArtifact`, `ConceptReviewReport`).
- When the LLM reply is not valid JSON even after the adapter's one repair attempt, the adapter raises `InvalidLLMOutput`. S2 treats it as a failed attempt and retries.
- `StageRecorder.record_attempt(AttemptRecord)`: the orchestrator turns each record into a `stage_runs` row (stage name `S2_concepts`).

## Live smoke test (real LLM)

The unit tests prove the logic. Only a live run shows whether the prompts produce good concepts.

```
copy .env.example .env          # Windows; use cp on Mac/Linux. Put your key in .env, never in git
python scripts/s2_live_smoke.py --intake tests/fixtures/golden/book_p4_u1_l2_intake.json --brief tests/fixtures/golden/brief.json --runs 3 --out out
```

The script works with any provider: pass `--llm-factory module:function` (a zero-argument callable returning an `LLMClient`). Without it, it uses the optional Gemini adapter and reads `GEMINI_API_KEY`.

Exit code: `0` all runs passed, `1` a run exhausted its attempts, `2` configuration or provider error. See `docs/TESTING_S2.md` for what to look at and how to review weights by hand.

## Optional: Gemini adapter

`src/story_service/providers/llm/gemini.py` is an optional `LLMClient` implementation. Nothing in S2 imports it. It needs approval from the Module 1 owner before joining the provider registry (it adds `LLM_PROVIDER=gemini`, which the shared spec does not list). It uses the spec's documented structured-output fallback (schema in the prompt, JSON mode, validation, one repair attempt) and retries 429, 5xx and timeouts with the spec's backoff.

## Status and open items

- Verified: 48 unit tests (golden case, every row of the spec's S2 tables, retries, reviewer verdicts, injection hygiene, English input, a real textbook lesson, the Gemini adapter through a fake transport).
- Not verified: a live run with a real provider; integration with the real repository (S0, S3, orchestrator, `FakeLLM`); resume after a crash.
- Open decision for the spec owner: spec 12.2 says "at most 5 tries" but lists five waits. The Gemini adapter currently makes 1 request plus 5 retries.
- Source books may be scanned images. Turning them into `topic_text` happens before this service (OCR is the caller's job).

Design decisions and the changes that affect other modules are listed in `docs/DECISIONS.md`.
