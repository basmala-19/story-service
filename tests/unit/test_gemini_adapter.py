"""Gemini adapter tests with an injected transport (no network, no real key)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from story_service.pipeline.errors import InvalidLLMOutput
from story_service.pipeline.stages import s2_concepts
from story_service.providers.errors import ProviderError
from story_service.providers.llm.gemini import BACKOFF_SECONDS, GeminiLLMClient

FIX = Path(__file__).resolve().parents[1] / "fixtures"
load = lambda rel: json.loads((FIX / rel).read_text(encoding="utf-8"))
SCHEMA = {"title": "X", "type": "object"}
KEY = "AIza-SECRET-KEY-123"


def ok(obj_or_text, tin=10, tout=5):
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text, ensure_ascii=False)
    return 200, {"candidates": [{"content": {"parts": [{"text": text}]}}],
                 "usageMetadata": {"promptTokenCount": tin, "candidatesTokenCount": tout}}


class FakePost:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, dict(headers), body))
        r = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def client(post, sleeps=None):
    sl = [] if sleeps is None else sleeps
    return GeminiLLMClient(KEY, post=post, sleep=sl.append, jitter=lambda: 0.5), sl


def call(c, role="large"):
    return c.complete_json("sys", "user", SCHEMA, role, 0.2, 1000)


def test_success_request_shape_and_usage():
    post = FakePost(ok({"a": 1}, 11, 7))
    c, _ = client(post)
    assert call(c) == {"a": 1}
    url, headers, body = post.calls[0]
    assert url.endswith("/models/gemini-2.5-pro:generateContent")
    assert headers["x-goog-api-key"] == KEY and KEY not in url
    assert body["generationConfig"]["temperature"] == 0.2
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert '"title": "X"' in body["systemInstruction"]["parts"][0]["text"]  # schema is in the prompt
    assert c.usage[0]["tokens_in"] == 11 and c.usage[0]["tokens_out"] == 7


def test_model_role_mapping_and_env(monkeypatch):
    post = FakePost(ok({"a": 1}))
    c = GeminiLLMClient.from_env({"GEMINI_API_KEY": KEY, "GEMINI_MODEL_SMALL": "my-small"})
    c._post = post
    call(c, "small")
    assert "/models/my-small:" in post.calls[0][0]


def test_markdown_fences_are_stripped():
    c, _ = client(FakePost(ok('```json\n{"a": 2}\n```')))
    assert call(c) == {"a": 2}


def test_transient_429_retries_with_backoff_then_succeeds():
    c, sleeps = client(FakePost((429, {}), (503, {}), ok({"a": 3})))
    assert call(c) == {"a": 3}
    assert sleeps == [2 * 1.0, 4 * 1.0]  # jitter 0.5 -> factor 1.0


def test_timeout_is_transient():
    c, sleeps = client(FakePost(httpx.ReadTimeout("slow"), ok({"a": 4})))
    assert call(c) == {"a": 4} and len(sleeps) == 1


def test_gives_up_after_five_retries_as_provider_error():
    post = FakePost((500, {}))
    c, sleeps = client(post)
    with pytest.raises(ProviderError):
        call(c)
    assert len(post.calls) == 1 + len(BACKOFF_SECONDS) and len(sleeps) == len(BACKOFF_SECONDS)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried_and_never_leak_the_key(status):
    post = FakePost((status, {"error": {"message": "bad key"}}))
    c, sleeps = client(post)
    with pytest.raises(ProviderError) as e:
        call(c)
    assert len(post.calls) == 1 and sleeps == [] and KEY not in str(e.value)


def test_invalid_json_gets_one_repair_attempt():
    post = FakePost(ok("not json at all"), ok({"a": 5}))
    c, _ = client(post)
    assert call(c) == {"a": 5} and len(post.calls) == 2
    assert post.calls[1][2]["contents"][-1]["role"] == "user"


def test_invalid_json_after_repair_raises_invalid_llm_output():
    c, _ = client(FakePost(ok("nope"), ok("still nope")))
    with pytest.raises(InvalidLLMOutput):
        call(c)


def test_blocked_prompt_is_provider_error():
    c, _ = client(FakePost((200, {"promptFeedback": {"blockReason": "SAFETY"}})))
    with pytest.raises(ProviderError):
        call(c)


def test_missing_key_and_images_rejected():
    with pytest.raises(ProviderError):
        GeminiLLMClient.from_env({})
    c, _ = client(FakePost(ok({})))
    with pytest.raises(ProviderError):
        c.complete_json("s", "u", SCHEMA, "large", 0.2, 10, images=[b"x"])


# ---------------------------------------------------- S2 through the adapter
def test_s2_end_to_end_over_adapter_with_real_book_topic():
    intake, brief = load("golden/book_p4_u1_l2_intake.json"), load("golden/brief.json")
    post = FakePost(ok(load("llm/s2_book_u1_l2_concepts.json")),
                    ok({"missing_concepts": [], "invented_concepts": [], "weight_issues": []}))
    c, _ = client(post)
    out = s2_concepts.run_detailed(intake, brief, c)
    assert out.attempts == 1 and out.report.passed
    assert out.artifact.must_ids == ["c1", "c2"] and len(post.calls) == 2


def test_s2_retries_when_adapter_reports_invalid_json():
    gold = load("llm/s2_concepts.json")
    post = FakePost(ok("junk"), ok("junk again"),                 # attempt 1: repair also fails
                    ok(gold), ok({"missing_concepts": [], "invented_concepts": [], "weight_issues": []}))
    c, _ = client(post)
    rec = []
    class R:
        def record_attempt(self, r): rec.append((r.attempt, r.status, r.validation["checks"][0]["name"]))
    out = s2_concepts.run(load("golden/intake.json"), load("golden/brief.json"), c, recorder=R())
    assert [r[:2] for r in rec] == [(1, "failed"), (2, "passed")] and rec[0][2] == "invalid_json"
    assert len(out.concepts) == 2
