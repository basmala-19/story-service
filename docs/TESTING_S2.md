# Testing S2 (Concept Extractor)

## 1. Offline (no key, no network): must always pass
    pip install pydantic pytest httpx
    PYTHONPATH=src python -m pytest tests/unit -q          # 48 tests

Covers: every row of the spec's S2 tables, retries and `stage_runs` accounting, reviewer verdicts,
prompt-injection hygiene, English input, a real book topic, and the Gemini adapter (retries, backoff,
repair, error mapping, key never leaked) through an injected transport.

## 2. Live (real Gemini): you run this, with your own key
    cp .env.example .env      # then put GEMINI_API_KEY in .env (never commit)
    PYTHONPATH=src python scripts/s2_live_smoke.py \
        --intake tests/fixtures/golden/book_p4_u1_l2_intake.json \
        --brief  tests/fixtures/golden/brief.json --runs 5 --out out/
    PYTHONPATH=src python scripts/s2_live_smoke.py \
        --intake tests/fixtures/golden/intake.json \
        --brief  tests/fixtures/golden/brief.json --runs 5

Exit code 0 = all runs passed, 1 = some run exhausted attempts, 2 = config/provider error.

### What to look at
| Signal | Healthy | If not |
|--------|---------|--------|
| Runs passed | 5/5 | read the failed checks printed per attempt |
| Passed on first attempt | at least 4/5 | most often `evidence_verbatim`: tighten the prompt or add auto-snap of a near-miss quote to the closest sentence |
| Tokens in/out per run | stable run to run | large spread means unstable output |
| `reviewer_weight_issues` warnings | rare | read them; they point at weight mistakes |
| Concepts | 2 to 4 for one lesson page, 1 to 3 `must` | inflated `must` means rubric not followed |

### Human review (5 minutes per topic, do this on 3 to 5 real topics)
1. For each `must`: remove it mentally; could a student still do the lesson objective? If yes it is not `must`.
2. Is any concept from the next topic (`later_preview`)? Must be none.
3. Is any `evidence` quote the exact passage that states the concept?
4. Are `real_world_uses` ordinary for a 9 to 11 year old in Egypt (no brands, nothing sensitive)?
5. Re-run the same topic 3 times: are the `must` ids and titles the same each time?

### Before production
- Verify in the Gemini docs: model ids, `generateContent` field names, whether thinking tokens consume `maxOutputTokens` (if replies truncate, raise `EXTRACTOR_MAX_TOKENS` in `use_case.py`).
- Run on at least 3 other lessons (other unit, other subject, one English).
- Wire it to the real orchestrator and test resume after a crash (spec 13.5 item 7).
