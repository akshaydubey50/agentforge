"""Retry/backoff behaviour at the shared LLM seam, with the provider mocked.

No network, no API key, no model -- these belong in the release gate's tier 1
(see eval/gate.py's UNIT_SUITES). Backoff is neutralised by setting the base
to 0 rather than by patching time.sleep, so the real sleep path still runs and
a bug in the delay computation still surfaces.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from litellm import exceptions as llm_exceptions

from agentsys import llm
from agentsys.config import settings
from agentsys.graph.schemas import MemoryCandidateBatch, ReviewOutput


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch):
    monkeypatch.setattr(settings, "llm_backoff_base_seconds", 0.0, raising=False)
    monkeypatch.setattr(settings, "llm_backoff_max_seconds", 0.0, raising=False)
    monkeypatch.setattr(settings, "llm_max_attempts", 3, raising=False)
    monkeypatch.setattr(settings, "llm_fallback_model", "", raising=False)
    # _api_key_for raises without one; this is not a real key and reaches no
    # provider, because litellm.completion is replaced in every test below.
    monkeypatch.setattr(settings, "openai_api_key", "test-key-not-real", raising=False)


class _Response:
    """Minimal stand-in for a litellm ModelResponse."""

    def __init__(self, text="ok"):
        message = type("M", (), {"content": text})()
        self.choices = [type("C", (), {"message": message})()]


def _raiser(exc, *, succeed_after=None):
    """A fake litellm.completion that raises `exc` until `succeed_after`
    calls have been made. Records its own call count."""
    state = {"calls": 0, "models": []}

    def _call(*_args, **kwargs):
        state["calls"] += 1
        state["models"].append(kwargs.get("model"))
        if succeed_after is not None and state["calls"] > succeed_after:
            return _Response()
        raise exc

    _call.state = state
    return _call


def _rate_limit():
    return llm_exceptions.RateLimitError(
        message="slow down", llm_provider="openai", model="gpt-4o-mini"
    )


def _auth_error():
    return llm_exceptions.AuthenticationError(
        message="bad key", llm_provider="openai", model="gpt-4o-mini"
    )


def _bad_request():
    return llm_exceptions.BadRequestError(
        message="malformed", llm_provider="openai", model="gpt-4o-mini"
    )


def _schema_bad_request():
    return llm_exceptions.BadRequestError(
        message=(
            "OpenAIException - Error code: 400 - {'error': {'message': "
            "\"Invalid schema for response_format 'MemoryCandidateBatch': "
            "In context=('properties', 'source'), 'additionalProperties' is "
            "required to be supplied and to be false.\"}}"
        ),
        llm_provider="openai",
        model="gpt-4o-mini",
    )


# --- transient: retried ----------------------------------------------------


def test_transient_failure_is_retried_then_succeeds(monkeypatch):
    fake = _raiser(_rate_limit(), succeed_after=2)
    monkeypatch.setattr(llm.litellm, "completion", fake)

    text, _ = llm.complete("hello")

    assert text == "ok"
    assert fake.state["calls"] == 3, "should have failed twice and succeeded on the third"


def test_transient_failure_gives_up_after_max_attempts(monkeypatch):
    fake = _raiser(_rate_limit())
    monkeypatch.setattr(llm.litellm, "completion", fake)

    with pytest.raises(llm.LLMTransientError):
        llm.complete("hello")

    assert fake.state["calls"] == settings.llm_max_attempts


@pytest.mark.parametrize(
    "exc",
    [
        llm_exceptions.Timeout(message="timed out", model="m", llm_provider="openai"),
        llm_exceptions.APIConnectionError(message="conn reset", llm_provider="openai", model="m"),
        llm_exceptions.InternalServerError(message="500", llm_provider="openai", model="m"),
        llm_exceptions.ServiceUnavailableError(message="503", llm_provider="openai", model="m"),
    ],
)
def test_every_transient_class_is_retried(monkeypatch, exc):
    fake = _raiser(exc, succeed_after=1)
    monkeypatch.setattr(llm.litellm, "completion", fake)

    llm.complete("hello")

    assert fake.state["calls"] == 2


# --- permanent: NOT retried ------------------------------------------------


def test_authentication_error_is_not_retried(monkeypatch):
    """The whole point of the classification. A bad key retried three times is
    three identical rejections and three times the latency."""
    fake = _raiser(_auth_error())
    monkeypatch.setattr(llm.litellm, "completion", fake)

    with pytest.raises(llm.LLMPermanentError):
        llm.complete("hello")

    assert fake.state["calls"] == 1


def test_bad_request_is_not_retried(monkeypatch):
    fake = _raiser(_bad_request())
    monkeypatch.setattr(llm.litellm, "completion", fake)

    with pytest.raises(llm.LLMPermanentError):
        llm.complete("hello")

    assert fake.state["calls"] == 1


def test_structured_schema_bad_request_falls_back_to_json_mode(monkeypatch):
    calls = []

    def fake_completion(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _schema_bad_request()
        return _Response('{"candidates":[]}')

    monkeypatch.setattr(llm.litellm, "completion", fake_completion)

    parsed, _ = llm.structured_complete("extract memory", MemoryCandidateBatch)

    assert parsed.candidates == []
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert calls[1]["messages"][0]["role"] == "system"


def test_structured_generic_bad_request_does_not_fall_back(monkeypatch):
    fake = _raiser(_bad_request())
    monkeypatch.setattr(llm.litellm, "completion", fake)

    with pytest.raises(llm.LLMPermanentError):
        llm.structured_complete("extract memory", MemoryCandidateBatch)

    assert fake.state["calls"] == 1


def test_structured_complete_sends_strict_response_format(monkeypatch):
    captured = {}

    def fake_completion(*_args, **kwargs):
        captured.update(kwargs)
        return _Response('{"candidates":[]}')

    monkeypatch.setattr(llm.litellm, "completion", fake_completion)

    parsed, _ = llm.structured_complete("extract memory", MemoryCandidateBatch)

    response_format = captured["response_format"]
    schema = response_format["json_schema"]["schema"]
    source_schema = schema["$defs"]["MemoryCandidate"]["properties"]["source"]

    assert parsed.candidates == []
    assert response_format is not MemoryCandidateBatch
    assert response_format["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["MemoryCandidateSource"]["additionalProperties"] is False
    assert source_schema["additionalProperties"] is False

    def assert_object_schemas_are_closed(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_object_schemas_are_closed(value)
        elif isinstance(node, list):
            for item in node:
                assert_object_schemas_are_closed(item)

    assert_object_schemas_are_closed(schema)


def test_structured_complete_scrubs_nul_from_parsed_strings(monkeypatch):
    def fake_completion(*_args, **_kwargs):
        return _Response('{"score":3,"verdict":"escalate","feedback":"needs human\\u0000 review"}')

    monkeypatch.setattr(llm.litellm, "completion", fake_completion)

    parsed, _ = llm.structured_complete("review", ReviewOutput)

    assert parsed.feedback == "needs human review"


def test_context_window_exceeded_is_not_retried(monkeypatch):
    """Subclasses BadRequestError -- this asserts the permanent tuple is
    checked before the transient one, which is the ordering the module
    depends on."""
    exc = llm_exceptions.ContextWindowExceededError(
        message="too long", model="m", llm_provider="openai"
    )
    fake = _raiser(exc)
    monkeypatch.setattr(llm.litellm, "completion", fake)

    with pytest.raises(llm.LLMPermanentError):
        llm.complete("hello")

    assert fake.state["calls"] == 1


def test_unknown_exception_propagates_unwrapped(monkeypatch):
    """A bug in our own code must not be retried or disguised as a provider
    failure -- that is how a TypeError becomes three API calls and an
    unreadable error."""
    fake = _raiser(TypeError("this is a bug, not a provider problem"))
    monkeypatch.setattr(llm.litellm, "completion", fake)

    with pytest.raises(TypeError):
        llm.complete("hello")

    assert fake.state["calls"] == 1


# --- fallback model --------------------------------------------------------


def test_fallback_model_is_tried_after_transient_exhaustion(monkeypatch):
    monkeypatch.setattr(settings, "llm_fallback_model", "openai/gpt-4o", raising=False)
    fake = _raiser(_rate_limit(), succeed_after=settings.llm_max_attempts)
    monkeypatch.setattr(llm.litellm, "completion", fake)

    text, _ = llm.complete("hello", model="openai/gpt-4o-mini")

    assert text == "ok"
    assert fake.state["models"][-1] == "openai/gpt-4o", "last attempt should use the fallback"


def test_fallback_is_not_tried_after_a_permanent_error(monkeypatch):
    """A bad key fails identically on any model. Falling back there just
    doubles the error and the bill."""
    monkeypatch.setattr(settings, "llm_fallback_model", "openai/gpt-4o", raising=False)
    fake = _raiser(_auth_error())
    monkeypatch.setattr(llm.litellm, "completion", fake)

    with pytest.raises(llm.LLMPermanentError):
        llm.complete("hello")

    assert fake.state["calls"] == 1


def test_embeddings_never_fall_back_to_a_chat_model(monkeypatch):
    """llm_fallback_model names a CHAT model; handing an embedding call one
    returns something structurally wrong rather than degrading."""
    monkeypatch.setattr(settings, "llm_fallback_model", "openai/gpt-4o", raising=False)
    fake = _raiser(_rate_limit())
    monkeypatch.setattr(llm.litellm, "embedding", fake)

    with pytest.raises(llm.LLMTransientError):
        llm.embed_texts(["hello"])

    assert fake.state["calls"] == settings.llm_max_attempts
    assert "openai/gpt-4o" not in fake.state["models"]


# --- transport details -----------------------------------------------------


def test_timeout_is_passed_to_the_provider(monkeypatch):
    """The absence of this was the most likely availability incident in the
    system: a hung call held a prefork worker for the full Celery limit."""
    seen = {}

    def _call(*_args, **kwargs):
        seen.update(kwargs)
        return _Response()

    monkeypatch.setattr(llm.litellm, "completion", _call)
    llm.complete("hello")

    assert seen["timeout"] == settings.llm_timeout_seconds


def test_retry_after_header_is_honoured_over_computed_backoff():
    exc = _rate_limit()
    exc.response = type("R", (), {"headers": {"retry-after": "7"}})()

    assert llm._retry_after_seconds(exc) == 7.0


def test_missing_retry_after_falls_through_to_backoff():
    assert llm._retry_after_seconds(_rate_limit()) is None


def test_backoff_grows_and_is_jittered(monkeypatch):
    monkeypatch.setattr(settings, "llm_backoff_base_seconds", 1.0, raising=False)
    monkeypatch.setattr(settings, "llm_backoff_max_seconds", 20.0, raising=False)

    # Equal jitter: each delay sits in [d/2, d] for d = base * 2**attempt.
    assert 0.5 <= llm._backoff_seconds(0) <= 1.0
    assert 1.0 <= llm._backoff_seconds(1) <= 2.0
    assert 2.0 <= llm._backoff_seconds(2) <= 4.0

    # Capped, and still jittered at the cap.
    assert 10.0 <= llm._backoff_seconds(20) <= 20.0

    # Not a constant -- a synchronised retry across a worker pool is the
    # thundering herd the jitter exists to prevent.
    assert len({llm._backoff_seconds(3) for _ in range(20)}) > 1


def test_no_caller_adds_its_own_retry_loop():
    """The resilience contract: retrying is a property of the transport, so
    it lives at this seam and nowhere else. If a call site grows its own
    loop, the attempt counts multiply silently."""
    import re

    nodes = (Path(__file__).resolve().parents[1] / "src/agentsys/graph/nodes.py").read_text(
        encoding="utf-8"
    )
    # Look for a retry around a model call, not the reviewer's
    # reject-and-revise loop (which is a product behaviour, not a transport one).
    assert not re.search(r"for\s+\w*attempt\w*\s+in\s+range", nodes)
