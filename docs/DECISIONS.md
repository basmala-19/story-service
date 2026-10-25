# DECISIONS (Module 2: S2 Concept Extractor)

1. **Package, not a single file.** `pipeline/stages/s2_concepts/` is a package (models, rules, ports, prompting, use_case) so the layers stay separate. `from story_service.pipeline.stages import s2_concepts` and `s2_concepts.run(...)` work as in the spec.
2. **Sync API.** `run(intake, brief, llm, *, recorder=None, max_attempts=3, feedback=None)` returns `ConceptsArtifact`; `run_detailed` also returns the validation report. Exhaustion raises `StageExhaustedError` (code `VALIDATION_EXHAUSTED`); the orchestrator maps it to `needs_review`.
3. **Stage runs are recorded by the caller.** S2 emits one `AttemptRecord` per attempt through the `StageRecorder` port; the orchestrator writes `stage_runs`. `compute_input_hash(ctx, model_id, feedback)` gives the cache key (inputs + prompt hash + model id).
4. **Reviewer routing.** The two LLM calls differ by `schema["title"]` (`ConceptsArtifact`, `ConceptReviewReport`); `FakeLLM` should route on it.
5. **Reviewer only runs after code checks pass.** A rejected output is not audited; the retry gets the code failures instead.
6. **`real_world_uses` word limit** ("each at most 25 words") is applied to `situation` + `why_it_matters` together (stricter reading).
7. **Structure vs limits.** Pydantic checks structure only; every limit is a named check in `rules.py` so retries get precise feedback.
8. **Reviewer `weight_issues` and missing `optional` concepts** are minor warnings, not blockers (spec makes only missing must/important and invented concepts blocking).
9. **Extra code checks beyond the spec:** `must_count` max 4 and `concept_count` 2 to 8 (both in the spec text), `id_format` (c1, c2, ...), `required_text`, `assumed_prior_knowledge` must be empty when `prior_text` is empty, and a separate `evidence_not_from_later_preview` check for a clearer fix hint. `unique_titles` is minor.
10. **Prompt limits are injected from `rules.Limits`** (string.Template) so prompt and validator cannot drift. Delimiter tags inside lesson text are stripped (`neutralize`).
11. **Shared files created here** because S2 needs them: `validators/text.py` (`norm_ar`), `pipeline/validation.py`, `pipeline/errors.py`. If Module 1 already owns equivalents, keep theirs and adjust imports.

## Changes that affect other modules / the team (S2 + Gemini)

| # | Change | Who must act |
|---|--------|--------------|
| A | **New `LLM_PROVIDER` value `gemini`** (spec lists `anthropic`, `openai_compatible`). New env vars `GEMINI_API_KEY`, `GEMINI_MODEL_LARGE/SMALL/VISION`. | M1 provider registry: register `GeminiLLMClient` (`providers/llm/gemini.py`); `.env.example` merge. |
| B | **Structured output uses the spec 14.2 fallback**: schema in the system prompt + `responseMimeType=application/json` + Pydantic validation + one repair attempt. The adapter does not rely on Gemini's native schema field. | M1 (any other adapter can do the same). |
| C | **New exception `InvalidLLMOutput`** (`pipeline/errors.py`): adapter raises it when JSON is invalid after its repair attempt; a stage counts it as a failed attempt. `ProviderError` (`providers/errors.py`) is non-retryable and must reach the orchestrator, which maps it to version `failed` / `PROVIDER_ERROR`. | M1 adapters, orchestrator. |
| D | **Orchestrator contract for S2**: call `s2_concepts.run(intake, brief, llm, recorder=..., max_attempts=MAX_STAGE_ATTEMPTS)`; catch `StageExhaustedError` -> `needs_review`/`VALIDATION_EXHAUSTED`, skip later stages; implement `StageRecorder.record_attempt` to write `stage_runs` (stage name `S2_concepts`); use `s2_concepts.compute_input_hash(ctx, model_id)` for the cache key. | Orchestrator owner. |
| E | **`LLMClient` calls are synchronous with keyword arguments** (`system, user, schema, model_role, temperature, max_tokens`). `FakeLLM` must route the two S2 calls by `schema["title"]` (`ConceptsArtifact` / `ConceptReviewReport`) and may raise `InvalidLLMOutput`. | M1 (`FakeLLM`), orchestrator (thread pool if async). |
| F | **`concepts.json` format is exactly the spec's.** Downstream can rely on: ids exactly `c1..cN` in order (enforced by the blocker check `ids_sequential`), `weight` in `must/important/optional`, 2 to 8 concepts, 1 to 4 `must`, `evidence` verbatim in `topic_text`, `assumed_prior_knowledge` empty when `prior_text` is empty. Helpers: `ConceptsArtifact.must_ids`, `.important_ids`, `.ids`. | S3 (memory/coverage checks), S4 (coverage), result builder (`coverage.must`). |
| G | **Shared files created by this module**: `validators/text.py` (`norm_ar`, `contains_phrase`, `word_count`), `pipeline/validation.py` (`Check`, `ValidationReport`), `pipeline/errors.py`. If equivalents exist, unify (keep `norm_ar` behaviour identical: S0 locator and S3 Jaccard use it too). | M0/M1, S0, S3 owners. |
| H | **Input to S2** is read from `intake.json` (topic_title, topic_text, prior_text, later_preview, language, dialect_applied, country, age_group, grade, subject) and `brief.json` (currency.spoken_name, places, foods, transport, holidays_and_seasons, school_context, sensitivities). Renaming any of those fields in S0/S1 breaks S2 (`ConceptExtractionInput.from_artifacts`). | S0/S1 owners. |
| I | **Source book is a scan** (no text layer). Book to `topic_text` conversion is outside this service (spec: parsing books is out of scope), so the caller must OCR (e.g. Gemini vision). Textbook topics are activity-style (explore / learn / think), with less definitional prose than the spec's fractions sample. | Caller / S0 owner. |


## Review follow-ups (after external review of the archive)

1. **Bug fixed:** `InvalidLLMOutput` from the *Reviewer* no longer discards the extracted artifact. It becomes the blocker check `reviewer_invalid_json`, and the artifact is kept as `previous_output` for the retry. The extractor's own invalid JSON is the separate check `invalid_json`. Regression test: `test_reviewer_invalid_json_keeps_the_extracted_artifact_for_the_retry`.
2. **IDs contract decided: sequential.** `ids_sequential` is a blocker (the prompt already demands `c1, c2, ...` in topic order, and retries receive the failure). Docs now match the code. To relax it, delete that one check and change item F above. Both `id_format` and `ids_unique` remain.
3. **`assumed_prior_knowledge`:** empty is enforced when `prior_text` is empty (blocker). Otherwise a minor warning `assumed_prior_knowledge_support` appears when an item shares no content word with `prior_text` (Arabic article stripped, paraphrase allowed). Never a blocker.
4. **OPEN QUESTION for the spec owner (Gemini retries):** spec 12.2 says "at most 5 tries" but lists five waits (2, 4, 8, 16, 32 s). Implemented reading: 1 request + 5 retries = 6 requests (`MAX_RETRIES` in `providers/llm/gemini.py`). The alternative reading (5 requests in total) means dropping the 32 s wait. Code, tests and this note must change together once decided.
5. **`extra="ignore"` scope:** only unknown extra keys are dropped. Every contract field is required, so a missing important field fails the schema check rather than being silently lost.
6. **Not yet verified:** the archive is not the full repository (no S0, S3, orchestrator, `FakeLLM`), and the live Gemini smoke test has not been run. See `docs/TESTING_S2.md`.
