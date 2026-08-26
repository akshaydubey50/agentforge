"""Phase 3's pure half: effect identity, failure classification, the retry
matrix, and the bounded retry loop.

Tier 1. execution.py's identity/classification/retry functions touch no
database, no network and no model -- the same promise policy.py makes -- so
the whole decision table is free to verify and a broken rule never costs an
API call to discover. The DB-backed half (the ledger, dedupe, crash recovery)
is tests/test_execution_ledger.py.

Failure injection uses test-only tools declared here. No production tool is
modified to make it fail: a tool that can be made to fail on request is a tool
whose failure path is not the real one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from agentsys import deadcalls
from agentsys.config import settings
from agentsys.execution import (
    ExecutionSafety,
    FailureKind,
    classify_failure,
    effect_key,
    is_retryable,
    run_with_retry,
    safety_of,
)
from agentsys.tools.base import Tool, ToolResult


# --- test-only tools -------------------------------------------------------


class ScriptedTool(Tool):
    """Returns a scripted sequence of results, one per attempt, and records
    every call. The last entry repeats once the script runs out, so an
    exhaustion test does not have to guess how many attempts will happen."""

    name = "scripted_tool"
    description = "Test double."

    def __init__(self, results: list[ToolResult], safety: ExecutionSafety = ExecutionSafety.IDEMPOTENT) -> None:
        self.results = results
        self.execution_safety = safety
        self.calls: list[dict] = []

    def validate_args(self, proposed: dict) -> dict:
        return dict(proposed)

    def run(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.results) - 1)
        return self.results[index]


class UnclassifiedTool(Tool):
    """A tool whose author never declared execution_safety -- the case the
    fail-closed default exists for."""

    name = "unclassified_tool"
    description = "Test double."

    def run(self, **kwargs) -> ToolResult:  # pragma: no cover -- never reached
        return ToolResult(success=True)


def _ok() -> ToolResult:
    return ToolResult(success=True, output={"ran": True})


def _fail(error: str) -> ToolResult:
    return ToolResult(success=False, error=error)


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch):
    """Zero the backoff so a retry test measures the retry, not the clock.
    The curve itself is llm.py's, and equal jitter of zero is zero."""
    monkeypatch.setattr(settings, "tool_backoff_base_seconds", 0.0)
    monkeypatch.setattr(settings, "tool_backoff_max_seconds", 0.0)


# --- effect identity -------------------------------------------------------


def test_the_same_logical_action_has_the_same_key():
    a = effect_key("file_io", {"action": "write", "path": "r.txt", "content": "x"}, task_id="T", user_id="U")
    b = effect_key("file_io", {"content": "x", "path": "r.txt", "action": "write"}, task_id="T", user_id="U")
    assert a == b, "key order must not change identity"


def test_different_arguments_are_different_effects():
    base = {"action": "write", "path": "r.txt", "content": "x"}
    assert effect_key("file_io", base, task_id="T") != effect_key(
        "file_io", {**base, "content": "y"}, task_id="T"
    )
    assert effect_key("file_io", base, task_id="T") != effect_key(
        "file_io", {**base, "path": "other.txt"}, task_id="T"
    )


def test_a_different_acting_user_is_a_different_effect():
    """The one deadcalls._signature deliberately cannot express. user_id
    decides WHOSE account a call acts on -- two users sending the identical
    arguments are two effects, and sharing a key would let one user's
    completed action suppress another's."""
    args = {"message_id": "m1"}
    assert effect_key("gmail_read", args, task_id="T", user_id="alice") != effect_key(
        "gmail_read", args, task_id="T", user_id="bob"
    )


def test_a_different_task_is_a_different_effect():
    args = {"action": "write", "path": "r.txt", "content": "x"}
    assert effect_key("file_io", args, task_id="T1") != effect_key("file_io", args, task_id="T2")


def test_a_different_tool_is_a_different_effect():
    assert effect_key("a", {"x": 1}, task_id="T") != effect_key("b", {"x": 1}, task_id="T")


def test_volatile_plumbing_does_not_change_identity():
    """subtask_id and depth differ between two runs of the SAME effect: after
    a crash the loop authors a new subtask, so keying on it would make every
    post-crash call look new -- which is exactly the duplicate this prevents."""
    a = effect_key("t", {"q": 1, "subtask_id": "s1", "depth": 1}, task_id="T", user_id="U")
    b = effect_key("t", {"q": 1, "subtask_id": "s2", "depth": 2}, task_id="T", user_id="U")
    assert a == b


def test_effect_identity_is_not_the_dead_call_signature():
    """Both exist on purpose. This pins the difference so a future tidy-up
    that collapses them has to delete this test first."""
    args = {"message_id": "m1", "user_id": "alice"}
    other = {"message_id": "m1", "user_id": "bob"}
    assert deadcalls._signature("gmail_read", args) == deadcalls._signature("gmail_read", other)
    assert effect_key("gmail_read", args, task_id="T", user_id="alice") != effect_key(
        "gmail_read", other, task_id="T", user_id="bob"
    )


def test_unserialisable_arguments_do_not_raise():
    key = effect_key("t", {"when": object()}, task_id="T")
    assert len(key) == 64


# --- failure classification ------------------------------------------------


@pytest.mark.parametrize(
    "error,expected",
    [
        ("web search request failed: connection reset by peer", FailureKind.TRANSIENT),
        ("knowledge search failed with status 503", FailureKind.TRANSIENT),
        ("Tavily search request failed: 429 too many requests", FailureKind.RATE_LIMIT),
        ("DuckDuckGo returned a bot-detection challenge (likely rate-limited)", FailureKind.RATE_LIMIT),
        ("timed out after 10s", FailureKind.TIMEOUT),
        ("MCP call mcp_x_y timed out after 120s", FailureKind.TIMEOUT),
        ("knowledge search was refused (401): SERVICE_TOKEN does not match", FailureKind.AUTH),
        ("permission denied for table sample_metric", FailureKind.AUTH),
        ("file not found: /app/data/workspace/t/r.txt", FailureKind.PERMANENT),
        ("invalid arguments for file_io: unexpected keyword", FailureKind.PERMANENT),
        ("path escapes the task workspace", FailureKind.PERMANENT),
        ("something nobody has ever seen", FailureKind.UNKNOWN),
        ("", FailureKind.UNKNOWN),
        (None, FailureKind.UNKNOWN),
    ],
)
def test_failure_classification(error, expected):
    assert classify_failure(error) is expected


def test_auth_wins_over_a_transient_looking_word():
    """Order matters: a permanent failure that happens to mention a
    connection must not be read as retryable."""
    assert classify_failure("403 forbidden on this connection") is FailureKind.AUTH


# --- the retry matrix ------------------------------------------------------


@pytest.mark.parametrize("kind", [FailureKind.TRANSIENT, FailureKind.RATE_LIMIT, FailureKind.TIMEOUT])
def test_idempotent_tools_retry_known_retryable_failures(kind):
    assert is_retryable(kind, ExecutionSafety.IDEMPOTENT)


@pytest.mark.parametrize("kind", [FailureKind.AUTH, FailureKind.PERMANENT, FailureKind.UNKNOWN])
def test_nothing_retries_a_failure_that_will_repeat_identically(kind):
    for safety in ExecutionSafety:
        assert not is_retryable(kind, safety)


@pytest.mark.parametrize(
    "safety", [ExecutionSafety.VERIFY_BEFORE_RETRY, ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT]
)
def test_a_tool_that_is_not_safe_to_repeat_is_never_auto_retried(safety):
    """Even a rate limit. The failure is not swallowed -- it goes back to the
    agent loop, which can re-propose and get a fresh policy evaluation. That
    is a decision; a blind repeat is not."""
    for kind in FailureKind:
        assert not is_retryable(kind, safety)


def test_a_tool_that_declares_nothing_is_gated_not_assumed_safe():
    assert safety_of(UnclassifiedTool()) is ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT


def test_a_test_double_without_the_attribute_is_also_fail_closed():
    assert safety_of(object()) is ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT  # type: ignore[arg-type]


# --- the bounded retry loop ------------------------------------------------


def test_a_transient_failure_is_retried_and_succeeds():
    tool = ScriptedTool([_fail("connection reset by peer"), _ok()])

    outcome = run_with_retry(tool, {"q": 1})

    assert outcome.result.success is True
    assert outcome.attempts == 2
    assert len(tool.calls) == 2


def test_a_permanent_failure_is_not_retried():
    tool = ScriptedTool([_fail("file not found: r.txt"), _ok()])

    outcome = run_with_retry(tool, {"q": 1})

    assert outcome.result.success is False
    assert outcome.attempts == 1, "a second identical call gets the identical rejection"
    assert outcome.failure is FailureKind.PERMANENT
    assert len(tool.calls) == 1


def test_an_unrecognised_failure_is_not_retried():
    tool = ScriptedTool([_fail("something nobody has ever seen"), _ok()])

    outcome = run_with_retry(tool, {"q": 1})

    assert outcome.attempts == 1
    assert outcome.failure is FailureKind.UNKNOWN


def test_retry_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "tool_max_attempts", 3)
    tool = ScriptedTool([_fail("503 service unavailable")])

    outcome = run_with_retry(tool, {"q": 1})

    assert outcome.result.success is False
    assert outcome.attempts == 3, "must stop at the cap, not retry forever"
    assert len(tool.calls) == 3


def test_a_rate_limit_is_classified_and_retried():
    tool = ScriptedTool([_fail("429 rate limit exceeded"), _ok()])

    outcome = run_with_retry(tool, {"q": 1})

    assert outcome.attempts == 2
    assert outcome.result.success is True


def test_a_non_idempotent_tool_is_not_retried_even_on_a_transient_failure():
    tool = ScriptedTool(
        [_fail("connection reset by peer"), _ok()], safety=ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT
    )

    outcome = run_with_retry(tool, {"q": 1})

    assert outcome.result.success is False
    assert outcome.attempts == 1
    assert len(tool.calls) == 1


def test_a_kwarg_mismatch_becomes_a_failed_result_and_is_not_retried():
    """The TypeError backstop that used to live in _run_tool_call and in the
    sub-agent loop, now in one place. It must not crash the graph, and it must
    not be retried -- the same arguments fail the same way."""

    class NarrowTool(ScriptedTool):
        def run(self, expected_only: str) -> ToolResult:  # type: ignore[override]
            return _ok()

    outcome = run_with_retry(NarrowTool([]), {"unexpected": 1})

    assert outcome.result.success is False
    assert "invalid arguments" in outcome.result.error
    assert outcome.attempts == 1


def test_an_unexpected_exception_still_propagates():
    """Phase 3 must not widen the catch. Swallowing an unexpected exception
    into a retry loop hides a bug behind a retry -- the rule llm.py states."""

    class BrokenTool(ScriptedTool):
        def run(self, **kwargs) -> ToolResult:  # type: ignore[override]
            raise RuntimeError("a real bug")

    with pytest.raises(RuntimeError):
        run_with_retry(BrokenTool([]), {})
