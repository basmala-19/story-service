#!/usr/bin/env python
"""Live smoke test for S2 against a real LLM provider (provider-agnostic).

The provider is any zero-argument factory returning an ``LLMClient``, given as
``module:attr`` (default: the optional Gemini adapter). Credentials come from the
environment or ./.env (never commit them). Example with another provider::

    --llm-factory my_project.providers.llm:build_default_client

Usage::

    export GEMINI_API_KEY=...            # only for the default Gemini factory
    PYTHONPATH=src python scripts/s2_live_smoke.py \
        --intake tests/fixtures/golden/book_p4_u1_l2_intake.json \
        --brief  tests/fixtures/golden/brief.json  [--runs 3] [--out out/]

Prints, per run: attempts used, every attempt's failed checks, warnings, token
usage and the final ``concepts.json``. Exit code: 0 all runs passed, 1 at least
one run exhausted its attempts, 2 configuration or provider error.
Lesson text is never printed.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run without PYTHONPATH

from story_service.pipeline.errors import StageExhaustedError  # noqa: E402
from story_service.pipeline.stages import s2_concepts  # noqa: E402
from story_service.providers.errors import ProviderError  # noqa: E402


DEFAULT_FACTORY = "story_service.providers.llm.gemini:GeminiLLMClient.from_env"


def build_llm(spec: str):
    module_name, _, attr_path = spec.partition(":")
    obj = importlib.import_module(module_name)
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    return obj()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class PrintRecorder:
    def __init__(self) -> None:
        self.records: list[s2_concepts.AttemptRecord] = []

    def record_attempt(self, r: s2_concepts.AttemptRecord) -> None:
        self.records.append(r)
        failed = [c for c in r.validation["checks"] if not c["passed"]]
        print(f"  attempt {r.attempt}: {r.status}")
        for c in failed:
            print(f"    - [{c['severity']}] {c['name']}: {c['detail'][:200]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intake", required=True, type=Path)
    ap.add_argument("--brief", required=True, type=Path)
    ap.add_argument("--llm-factory", default=DEFAULT_FACTORY,
                    help="module:attr of a zero-arg callable returning an LLMClient")
    ap.add_argument("--runs", type=int, default=1, help="repeat to measure stability")
    ap.add_argument("--out", type=Path, default=None, help="directory for concepts_<n>.json")
    args = ap.parse_args()

    load_dotenv(Path(".env"))
    try:
        llm = build_llm(args.llm_factory)
    except ProviderError as err:
        print(f"config error: {err}", file=sys.stderr)
        return 2

    intake = json.loads(args.intake.read_text(encoding="utf-8"))
    brief = json.loads(args.brief.read_text(encoding="utf-8"))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)

    exhausted, first_try = 0, 0
    for n in range(1, args.runs + 1):
        print(f"run {n}/{args.runs}")
        rec, t0, used0 = PrintRecorder(), time.monotonic(), len(getattr(llm, "usage", []))
        try:
            out = s2_concepts.run_detailed(intake, brief, llm, recorder=rec)
        except StageExhaustedError as err:
            exhausted += 1
            print(f"  EXHAUSTED after {err.attempts} attempts: {[i['code'] for i in err.issues]}")
            continue
        except ProviderError as err:
            print(f"provider error: {err}", file=sys.stderr)
            return 2
        first_try += out.attempts == 1
        calls = getattr(llm, "usage", [])[used0:]
        print(f"  passed in {out.attempts} attempt(s), {time.monotonic() - t0:.1f}s, "
              f"tokens in/out = {sum(u['tokens_in'] for u in calls)}/{sum(u['tokens_out'] for u in calls)}")
        for w in out.report.warnings:
            print(f"  warning {w.name}: {w.detail[:200]}")
        print("  concepts: " + ", ".join(f"{c.id}:{c.weight}:{c.title}" for c in out.artifact.concepts))
        if args.out:
            (args.out / f"concepts_{n}.json").write_text(
                json.dumps(out.artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8")

    print(f"\nSUMMARY: {args.runs - exhausted}/{args.runs} passed, {first_try} on the first attempt")
    return 1 if exhausted else 0


if __name__ == "__main__":
    sys.exit(main())
