"""Provider-level errors (spec 12.1: ``PROVIDER_ERROR``)."""
from __future__ import annotations


class ProviderError(Exception):
    """Non-retryable provider failure: bad credentials, unsupported request, retries exhausted."""

    code = "PROVIDER_ERROR"
