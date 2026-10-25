"""Gemini adapter implementing the spec's ``LLMClient.complete_json`` (Section 11.2).

Structured output follows the spec's documented fallback (Section 14.2): the JSON
Schema is placed in the system instruction, the API is asked for
``application/json``, the reply is validated by the caller's Pydantic model, and
one automatic repair attempt is made on invalid JSON. This avoids depending on
which JSON-Schema subset a given Gemini model accepts.

Credentials: the key is read from the environment (``GEMINI_API_KEY``) and is
sent only in the ``x-goog-api-key`` header. It is never logged. Lesson text is
never logged either (spec 12.4).

Facts to verify against the official Gemini docs before production (spec 14.2):
the model ids below, the ``generateContent`` request/response field names, and
whether thinking tokens count toward ``maxOutputTokens`` for the chosen model.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any, Callable, Mapping

import httpx

from story_service.pipeline.errors import InvalidLLMOutput
from story_service.providers.errors import ProviderError

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODELS = {  # override with GEMINI_MODEL_LARGE / _SMALL / _VISION
    "large": "gemini-2.5-pro",
    "small": "gemini-2.5-flash",
    "vision": "gemini-2.5-pro",
}
BACKOFF_SECONDS = (2, 4, 8, 16, 32)  # spec 12.2, +/-20% jitter
# OPEN QUESTION for the spec owner: spec 12.2 says "at most 5 tries" but lists FIVE waits.
# Current reading: 1 initial request + 5 retries = 6 HTTP requests. To read it as 5 requests
# in total, drop the last wait (32 s). Pinned by tests/unit/test_gemini_adapter.py.
MAX_RETRIES = len(BACKOFF_SECONDS)
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

# post(url, headers, body, timeout) -> (status_code, parsed_json_or_None)
PostFn = Callable[[str, Mapping[str, str], Mapping[str, Any], float], tuple[int, Any]]


def _httpx_post(url: str, headers: Mapping[str, str], body: Mapping[str, Any], timeout: float):
    r = httpx.post(url, headers=dict(headers), json=body, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


class GeminiLLMClient:
    def __init__(
        self,
        api_key: str,
        *,
        models: Mapping[str, str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = 120.0,  # spec 11.3
        post: PostFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        if not api_key:
            raise ProviderError("GEMINI_API_KEY is missing")
        self._key = api_key
        self._models = {**DEFAULT_MODELS, **(models or {})}
        self._base = base_url.rstrip("/")
        self._timeout = timeout_sec
        self._post = post or _httpx_post
        self._sleep = sleep
        self._jitter = jitter
        self.usage: list[dict[str, Any]] = []  # one entry per HTTP call that succeeded

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GeminiLLMClient":
        e = os.environ if env is None else env
        models = {
            role: e[f"GEMINI_MODEL_{role.upper()}"]
            for role in ("large", "small", "vision")
            if e.get(f"GEMINI_MODEL_{role.upper()}")
        }
        return cls(e.get("GEMINI_API_KEY", ""), models=models)

    # ------------------------------------------------------------------ API
    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        model_role: str,
        temperature: float,
        max_tokens: int,
        images: Any = None,
    ) -> dict[str, Any]:
        if images:
            raise ProviderError("images are not supported by this adapter yet")
        model = self._models.get(model_role)
        if not model:
            raise ProviderError(f"unknown model_role '{model_role}'")

        system_full = (
            f"{system}\n\n# Response format\nReply with ONE JSON object only, no markdown fences, "
            f"that validates against this JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        contents = [{"role": "user", "parts": [{"text": user}]}]
        text = self._generate(model, system_full, contents, temperature, max_tokens)
        parsed = _try_parse(text)
        if parsed is not None:
            return parsed

        # One automatic repair attempt on invalid JSON (spec 5.0 / 12.2).
        contents += [
            {"role": "model", "parts": [{"text": text[:6000]}]},
            {"role": "user", "parts": [{"text": (
                "Your previous reply was not a valid JSON object. Return the corrected JSON object only, "
                "complete, matching the schema, with no commentary.")}]},
        ]
        text = self._generate(model, system_full, contents, temperature, max_tokens)
        parsed = _try_parse(text)
        if parsed is None:
            raise InvalidLLMOutput("LLM returned invalid JSON after one repair attempt")
        return parsed

    # ------------------------------------------------------------- internals
    def _generate(self, model, system, contents, temperature, max_tokens) -> str:
        url = f"{self._base}/models/{model}:generateContent"
        headers = {"x-goog-api-key": self._key, "content-type": "application/json"}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        last = "no attempt made"
        for i, delay in enumerate((0, *BACKOFF_SECONDS)):
            if delay:
                self._sleep(delay * (0.8 + 0.4 * self._jitter()))
            t0 = time.monotonic()
            try:
                status, data = self._post(url, headers, body, self._timeout)
            except (httpx.TimeoutException, httpx.TransportError) as err:
                last = f"transport error: {type(err).__name__}"
                continue
            if status == 429 or status >= 500:
                last = f"HTTP {status}"
                continue
            if status != 200 or not isinstance(data, dict):
                raise ProviderError(f"Gemini request failed: HTTP {status} {_api_message(data)}")
            self.usage.append({
                "model": model,
                "tokens_in": (data.get("usageMetadata") or {}).get("promptTokenCount", 0),
                "tokens_out": (data.get("usageMetadata") or {}).get("candidatesTokenCount", 0),
                "latency_ms": int((time.monotonic() - t0) * 1000),
            })
            return _extract_text(data)
        raise ProviderError(f"Gemini unavailable after {len(BACKOFF_SECONDS) + 1} tries ({last})")


def _extract_text(data: Mapping[str, Any]) -> str:
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise ProviderError(f"Gemini returned no candidates ({reason})")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _try_parse(text: str) -> dict[str, Any] | None:
    cleaned = _FENCE.sub("", text or "").strip()
    try:
        obj = json.loads(cleaned)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _api_message(data: Any) -> str:
    if isinstance(data, dict):
        msg = (data.get("error") or {}).get("message")
        if msg:
            return str(msg)[:300]
    return ""
