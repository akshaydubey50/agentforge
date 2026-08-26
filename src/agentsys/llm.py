"""The single seam every model call in agentsys passes through.

Resilience lives HERE and nowhere else. Before this, `litellm.completion` was
called bare: no timeout, no retry, no backoff. A provider holding a socket
open was caught only by Celery's 20-minute wall clock, and with
`--concurrency=4` four hung calls stall the entire worker (see
docs/ARCHITECTURE_AUDIT.md §5.7 / §7.5).

The rule is that no CALLER adds its own retry loop. Retrying is a property of
the transport, not of what the prompt happens to be about, and a retry loop
per call site is how you end up with 3 x 3 x 3 attempts nobody intended.

WHAT IS AND IS NOT RETRIED

Retrying costs real money and real latency, so it is only ever done for
failures that a later identical attempt could plausibly survive:

  transient   rate limits, timeouts, connection resets, provider 5xx.
  permanent   bad API key, permission denied, malformed request, context
              window exceeded, content policy. A second identical call gets
              the identical rejection -- retrying is pure waste, and for
              rate-limit-adjacent errors it is actively harmful.

Anything that is not a litellm provider error at all (a TypeError in our own
code, say) propagates untouched: wrapping it would hide a bug behind a
retry.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

import litellm
from litellm import exceptions as llm_exceptions
from litellm.utils import type_to_response_format_param
from pydantic import BaseModel

from agentsys.config import settings
from agentsys.sanitize import scrub_nul

logger = logging.getLogger(__name__)

T = TypeVar("T")


class LLMError(RuntimeError):
    """Base for every provider failure this module reports. Subclasses
    RuntimeError so the existing fail-open callers (`_classify_turn`,
    `quick_reply_node`, `artifacts._digest`) that catch broad Exception keep
    working unchanged."""


class LLMPermanentError(LLMError):
    """A retry would fail identically. Raised on the first attempt."""


class LLMTransientError(LLMError):
    """Retried up to settings.llm_max_attempts, then raised."""


# Checked FIRST, and order matters: litellm's ContextWindowExceededError
# subclasses BadRequestError, and both are permanent, but several transient
# classes also inherit from APIError -- so the permanent tuple has to win
# any ambiguity rather than being reached only as a fallthrough.
_PERMANENT = (
    llm_exceptions.AuthenticationError,
    llm_exceptions.PermissionDeniedError,
    llm_exceptions.NotFoundError,
    llm_exceptions.ContextWindowExceededError,
    llm_exceptions.ContentPolicyViolationError,
    llm_exceptions.UnprocessableEntityError,
    llm_exceptions.BadRequestError,
)

_TRANSIENT = (
    llm_exceptions.RateLimitError,
    llm_exceptions.Timeout,
    llm_exceptions.APIConnectionError,
    llm_exceptions.InternalServerError,
    llm_exceptions.ServiceUnavailableError,
)


def _api_key_for(model: str) -> str:
    if model.startswith("anthropic/"):
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your local .env file.")
        return settings.anthropic_api_key
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to your local .env file.")
    return settings.openai_api_key


def _retry_after_seconds(exc: Exception) -> float | None:
    """A 429 usually carries the provider's own opinion about when to come
    back. Honouring it beats guessing -- our backoff curve knows nothing
    about the account's quota window. Best-effort: the header is absent as
    often as it's present, and every provider nests it differently, so a
    failure to find it just falls through to the computed backoff."""
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        value = headers.get("retry-after") or headers.get("Retry-After")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _backoff_seconds(attempt: int) -> float:
    """Equal jitter: half the exponential delay, plus a random slice of the
    other half. Full jitter can return ~0 and hammer a provider that just
    said no; no jitter at all synchronises every worker in the pool into
    retrying on the same tick, which is the thundering herd this exists to
    avoid."""
    ceiling = min(
        settings.llm_backoff_max_seconds,
        settings.llm_backoff_base_seconds * (2**attempt),
    )
    return ceiling / 2 + random.uniform(0, ceiling / 2)


def _attempt_series(call: Callable[[str], T], model: str) -> T:
    """Run `call(model)` with bounded retries. Raises LLMPermanentError
    immediately on a permanent failure, LLMTransientError once the attempt
    budget is spent."""
    last: Exception | None = None

    for attempt in range(settings.llm_max_attempts):
        try:
            return call(model)
        except _PERMANENT as exc:
            # Deliberately not retried. Never log the exception's request
            # body -- an auth failure's payload carries the api key.
            raise LLMPermanentError(f"{type(exc).__name__} calling {model}: {exc}") from exc
        except _TRANSIENT as exc:
            last = exc
            if attempt == settings.llm_max_attempts - 1:
                break
            delay = _retry_after_seconds(exc)
            if delay is None:
                delay = _backoff_seconds(attempt)
            logger.warning(
                "%s calling %s (attempt %d/%d), retrying in %.1fs",
                type(exc).__name__, model, attempt + 1, settings.llm_max_attempts, delay,
            )
            time.sleep(delay)

    raise LLMTransientError(
        f"{type(last).__name__} calling {model} after {settings.llm_max_attempts} attempts: {last}"
    ) from last


def _resilient(call: Callable[[str], T], model: str, *, fallback_ok: bool = True) -> T:
    """The attempt series, plus one optional shot at a different model.

    The fallback is tried ONLY after transient exhaustion -- a permanent
    error (bad key, malformed request) would fail identically on any model,
    so falling back there just doubles the error. Off unless
    LLM_FALLBACK_MODEL is set.

    fallback_ok=False for embeddings: llm_fallback_model names a CHAT model,
    and handing an embedding call a chat model doesn't degrade gracefully,
    it returns something structurally wrong."""
    try:
        return _attempt_series(call, model)
    except LLMTransientError:
        fallback = settings.llm_fallback_model
        if not fallback_ok or not fallback or fallback == model:
            raise
        logger.warning("falling back from %s to %s after transient exhaustion", model, fallback)
        return _attempt_series(call, fallback)


def complete(
    prompt: str, *, model: str | None = None, web_search_options: dict | None = None
) -> tuple[str, object]:
    """Returns (text, completion) rather than just text -- callers that need
    to record cost (see agentsys.cost) need the raw completion for its
    model/usage fields; callers that don't just take the first element.

    web_search_options is passed through to the provider only when set, for
    search-capable models (tools/web_search.py's OpenAI-hosted backend is
    the one caller). It's here rather than in that tool so this file stays
    the single place that imports the provider SDK."""
    extra = {"web_search_options": web_search_options} if web_search_options else {}

    def _call(m: str):
        return litellm.completion(
            model=m,
            messages=[{"role": "user", "content": prompt}],
            api_key=_api_key_for(m),
            timeout=settings.llm_timeout_seconds,
            **extra,
        )

    response = _resilient(_call, model or settings.llm_model)
    # Scrub at the source: a NUL the model emits must never reach a DB write.
    return scrub_nul(response.choices[0].message.content or ""), response


def structured_complete(
    prompt: str, response_model: type[BaseModel], *, model: str | None = None
) -> tuple[BaseModel, object]:
    """Structured-output equivalent of complete().

    LiteLLM has no .parse()-style auto-validating helper (unlike the OpenAI
    SDK's beta client) -- response_format guarantees the model's raw text is
    valid JSON matching the schema, but you still get raw text back, so this
    does the model_validate_json() step every call site used to get for free.

    That validation deliberately sits OUTSIDE the retry series: a schema
    violation is the model's output being wrong, not the transport failing,
    and callers already handle it (see `_classify_turn`'s fail-open)."""
    response_format = type_to_response_format_param(response_model)

    def _call(m: str):
        return litellm.completion(
            model=m,
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
            api_key=_api_key_for(m),
            timeout=settings.llm_timeout_seconds,
        )

    response = _resilient(_call, model or settings.llm_model)
    parsed = response_model.model_validate_json(scrub_nul(response.choices[0].message.content))
    return parsed, response


def embed_texts(texts: list[str], *, model: str = "openai/text-embedding-3-small") -> list[list[float]]:
    if not texts:
        return []

    def _call(m: str):
        return litellm.embedding(
            model=m, input=texts, api_key=_api_key_for(m), timeout=settings.llm_timeout_seconds
        )

    response = _resilient(_call, model, fallback_ok=False)
    return [item["embedding"] for item in response.data]
