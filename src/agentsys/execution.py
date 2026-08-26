"""The deterministic answer to *has this already run, and is running it again
safe?* -- the half of tool dispatch that `policy.py` does not cover.

    LLM proposes a tool call
              |
      typed validation          (tools/base.py, Phase 1)
              |
      policy.decide(...)        (policy.py, Phase 2)  -- may this run?
              |
        ALLOW / approved
              |
      execute_tool(...)         <-- this module        -- has it already run?
              |
    reuse prior result  /  execute once  /  refuse as ambiguous
              |
      persist the outcome durably, before and after the effect

WHY THIS EXISTS

Before this, a tool call was recorded by exactly one INSERT, *after* the tool
returned. So the window between "the effect happened" and "Postgres knows it
happened" was invisible: kill the worker in it and nothing survives saying the
call was ever made. The task resumes, the model re-proposes the same step, and
the effect happens a second time. `recovery.py` and ARCHITECTURE_AUDIT §5.3
both already stated this plainly; docs/PHASE3_EXECUTION_NOTE.md §3 has the
exact sequence.

WHAT IT DOES AND DOES NOT PROMISE

It is NOT exactly-once, and must never be described as one. The ordering

    INSERT in-flight  COMMIT  ->  external effect  ->  << crash >>  ->  nothing

is not recoverable by any amount of local bookkeeping: what happened between
the second and third arrow is genuinely unknown here. Exactly-once needs the
EXTERNAL system to deduplicate (an idempotency key it honours) or a
verification read that can prove whether the effect landed. Both belong to the
first tool that has one.

What it does deliver:

  * an effect that already succeeded is not executed again -- durably, across
    a crash, a resume and a different worker;
  * an effect whose outcome is unknown is never blindly repeated: it is
    repeated only when the tool's own semantics make repetition safe;
  * a failure is retried only when it is a KNOWN-retryable kind AND the tool
    is idempotent -- never on a guess.

WHY THE RULES ARE HERE AND NOT IN A TABLE

Same reasoning policy.py gives: one reader, a handful of rules, no operator
who can edit configuration but not code. The classification each tool carries
is a class attribute for the same reason `action_type` is -- it is a property
of the implementation, and the LLM must never be able to influence it.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlmodel import select

from agentsys.config import settings
from agentsys.db.models import ToolCall
from agentsys.db.session import get_session
from agentsys.policy import ActionType

if TYPE_CHECKING:  # pragma: no cover
    # Type-checking only, so the import cycle never closes: tools/base.py
    # imports ExecutionSafety from here to declare it on Tool, exactly as it
    # already does for policy.ActionType/Risk.
    from agentsys.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


class ExecutionSafety(str, Enum):
    """Whether repeating one call is safe, which is a different question from
    whether it was allowed (policy.Risk) or what it touches
    (policy.ActionType).

    Three values. There is deliberately no IDEMPOTENCY_KEY_SUPPORTED: no
    registered tool can carry an external idempotency key, because there is no
    external-write tool at all yet. Adding the mode would mean inventing a
    key-injection protocol for zero callers -- and worse, a future tool author
    could declare it and receive no protection at all. It is the same call
    policy.py already made about a CRITICAL escape hatch: build it alongside
    the first tool that justifies it (Phase 5's send_email), not before.
    """

    IDEMPOTENT = "idempotent"
    """Repeating the same call produces the same end state. Safe to retry
    after a known failure, or after recovery resolves an ambiguous one."""

    VERIFY_BEFORE_RETRY = "verify_before_retry"
    """A blind repeat is unsafe; the current state has to be checked first.
    After an ambiguous outcome this system refuses rather than guessing."""

    NON_RETRYABLE_SIDE_EFFECT = "non_retryable_side_effect"
    """After an ambiguous outcome, do not repeat automatically at all. The
    fail-closed default (see Tool.execution_safety)."""


class FailureKind(str, Enum):
    """What KIND of failure a tool reported, for the one question that
    matters: could an identical second attempt plausibly succeed?

    Validation failures and policy denials are deliberately absent. Neither
    ever reaches execution -- both are refused at their own seam
    (nodes._rejected_call) and nothing can retry them, so a member for each
    would be a value this module can never produce.
    """

    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    """Retryable, but also the AMBIGUOUS one: a call that timed out may well
    have landed at the other end. Only an idempotent tool may repeat it."""

    AUTH = "auth"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"
    """Not recognised, and therefore NOT retried. "Retry only known-retryable
    failures" cuts this way round on purpose: a misclassification then costs
    one un-retried transient failure, never an extra side effect."""


_RETRYABLE_KINDS = (FailureKind.TRANSIENT, FailureKind.RATE_LIMIT, FailureKind.TIMEOUT)


# --- failure classification ------------------------------------------------
#
# llm.py classifies by EXCEPTION TYPE, because litellm raises typed exceptions.
# Tools cannot be classified that way: every first-party tool catches its own
# exceptions and reports failure as a ToolResult with an error STRING (see
# web_search, knowledge_search, db_query, code_execution, mcp_tool). So this
# matches on that string, narrowly, and everything it does not recognise is
# UNKNOWN -- which is not retried. That keeps the brittleness deadcalls.py
# warns about ("guessing permanence from an error string is brittle") on the
# safe side of the decision.
#
# Ordered: the first pattern that matches wins, and the non-retryable ones are
# checked first so an auth failure mentioning a "connection" cannot be read as
# transient.
_PATTERNS: tuple[tuple[FailureKind, re.Pattern[str]], ...] = (
    (FailureKind.AUTH, re.compile(
        r"\b(401|403)\b|unauthor|forbidden|permission denied|invalid[_ ]?(api[_ ]?key|credentials|token)"
        r"|not authenticated|access denied", re.I)),
    (FailureKind.RATE_LIMIT, re.compile(r"\b429\b|rate[ _-]?limit|too many requests|quota exceeded", re.I)),
    (FailureKind.TIMEOUT, re.compile(r"timed out|timeout|deadline exceeded", re.I)),
    (FailureKind.PERMANENT, re.compile(
        r"\b(400|404|409|422)\b|not found|invalid arguments|does not exist|must be a non-empty"
        r"|is not configured|unknown action|escapes the task workspace|syntax error", re.I)),
    (FailureKind.TRANSIENT, re.compile(
        r"\b(500|502|503|504)\b|connection (error|reset|refused|aborted)|temporarily unavailable"
        r"|service unavailable|bad gateway|remote (end closed|disconnected)|read error", re.I)),
)


def classify_failure(error_text: str | None) -> FailureKind:
    """Deterministic, and never asks a model. See the note above for why this
    reads a string rather than an exception type."""
    if not error_text:
        return FailureKind.UNKNOWN
    for kind, pattern in _PATTERNS:
        if pattern.search(error_text):
            return kind
    return FailureKind.UNKNOWN


def is_retryable(kind: FailureKind, safety: ExecutionSafety) -> bool:
    """The whole retry matrix, collapsed to one line, deliberately.

    ONLY an idempotent tool is retried automatically. If repeating is not
    known-safe, do not repeat it automatically -- a rate-limited
    non-idempotent tool is not swallowed, its failure goes back to the agent
    loop, which can re-propose and get a fresh policy evaluation. That is a
    decision; a blind repeat is not.
    """
    return safety is ExecutionSafety.IDEMPOTENT and kind in _RETRYABLE_KINDS


def safety_of(tool: "Tool") -> ExecutionSafety:
    """This tool's declared safety, fail-closed. getattr rather than a direct
    read so a test double or a Tool subclass predating the attribute is gated
    rather than assumed safe."""
    value = getattr(tool, "execution_safety", None)
    return value if isinstance(value, ExecutionSafety) else ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT


# --- effect identity -------------------------------------------------------

_VOLATILE_KEYS = frozenset({"subtask_id", "depth"})
"""Runtime plumbing that differs between two runs of the SAME logical effect.

After a crash, reconcile_orphaned_subtasks fails the stranded subtask and the
loop authors a NEW one -- so subtask_id changes while the effect does not.
depth is the sub-agent recursion bound, likewise not part of what the call
does to the world.

NOT stripped: task_id and user_id. They are the opposite -- user_id decides
whose Gmail a call acts as, task_id decides which sandbox file_io writes to,
so two calls differing in either are two different effects. They are also
passed explicitly to effect_key() below, because only some tools receive them
as injected kwargs (web_search receives neither) and identity has to be
uniform across tools.
"""


def effect_key(tool_name: str, kwargs: dict[str, Any], *, task_id: str, user_id: str | None = None) -> str:
    """Stable identity of one logical effect: this tool, these
    model-controlled arguments, in this task, acting as this user.

    Deliberately a THIRD helper rather than a reuse of either existing one --
    both are correct for their own job and wrong for this one:

      deadcalls._signature   strips user_id and task_id, which is right for
                             "this call is a known dead end regardless of who
                             proposed it" and wrong here, where they are the
                             whole scope of the effect.
      policy.args_fingerprint keeps subtask_id and depth, which is right for
                             binding an approval to one exact call in one
                             exact step, and wrong here, because a crash gives
                             the same effect a new subtask_id.

    sort_keys so key order cannot change identity; default=str so an argument
    that is not JSON-serialisable degrades to a stable string instead of
    raising inside the execution path.
    """
    meaningful = {k: v for k, v in kwargs.items() if k not in _VOLATILE_KEYS}
    payload = json.dumps(
        {"task": task_id, "user": user_id, "tool": tool_name, "args": meaningful},
        sort_keys=True,
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


# --- bounded retry ---------------------------------------------------------


def _backoff_seconds(attempt: int) -> float:
    """Equal jitter, the same curve and the same reasoning as
    llm._backoff_seconds: full jitter can return ~0 and hammer something that
    just said no, while no jitter synchronises every worker onto the same
    tick. Not imported from llm.py -- that one reads the llm_* settings, and
    reaching into another module's private for a different budget is worse
    coupling than four lines."""
    ceiling = min(
        settings.tool_backoff_max_seconds,
        settings.tool_backoff_base_seconds * (2**attempt),
    )
    return ceiling / 2 + random.uniform(0, ceiling / 2)


@dataclass
class ToolOutcome:
    """What execute_tool/run_with_retry produced, beyond the ToolResult itself.
    A dataclass rather than widening the (success, text) contract every caller
    of _run_tool_call already has."""

    result: "ToolResult"
    attempts: int
    safety: ExecutionSafety
    failure: FailureKind | None = None
    """The kind of the FINAL failure, or None if the call ended up succeeding.
    Not the same as retry_reason below: a call that failed transiently and then
    worked has a retry_reason and no failure."""

    retry_reason: FailureKind | None = None
    """Why a retry happened, kept even when the retry SUCCEEDED. Without it a
    recovered call looks identical in the trace to one that worked first time,
    and "this tool has been failing and quietly recovering all week" is exactly
    what an operator needs to be able to see (observability, brief §18)."""

    effect_key: str | None = None
    deduped: bool = False
    """True when nothing ran: a prior successful execution of this exact
    effect was reused."""

    refused: bool = False
    """True when nothing ran and nothing may run: a prior attempt at this
    exact effect has an unknown outcome and this tool may not repeat blindly."""


def run_with_retry(tool: "Tool", kwargs: dict[str, Any]) -> ToolOutcome:
    """Invoke one tool, retrying only a known-retryable failure of an
    idempotent tool, bounded by settings.tool_max_attempts.

    In-process with a sleep, matching llm._attempt_series rather than
    introducing a queue: the delays are sub-second to a few seconds, well
    inside the worker's own wall clock, and a requeue would lose the
    subtask's in-memory position for no gain.

    Exception behaviour is unchanged from before Phase 3: only TypeError is
    caught (the **kwargs tools whose contract is not a local pydantic model --
    an MCP tool trusting a third party's schema, generate_tweet's deliberate
    extra="allow"). Anything else propagates, because swallowing an unexpected
    exception into a retry loop hides a bug behind a retry -- the rule llm.py
    already states.
    """
    from agentsys.tools.base import ToolResult  # local: tools/base imports this module

    safety = safety_of(tool)
    attempts = 0
    result: ToolResult
    retry_reason: FailureKind | None = None

    while True:
        attempts += 1
        try:
            result = tool.run(**kwargs)
        except TypeError as exc:
            result = ToolResult(success=False, error=f"invalid arguments for {tool.name}: {exc}")

        if result.success:
            return ToolOutcome(
                result=result, attempts=attempts, safety=safety, retry_reason=retry_reason
            )

        kind = classify_failure(result.error)
        if not is_retryable(kind, safety) or attempts >= settings.tool_max_attempts:
            return ToolOutcome(
                result=result, attempts=attempts, safety=safety, failure=kind,
                retry_reason=retry_reason,
            )
        retry_reason = kind

        delay = _backoff_seconds(attempts - 1)
        logger.warning(
            "%s failed (%s) on attempt %d/%d, retrying in %.1fs",
            tool.name, kind.value, attempts, settings.tool_max_attempts, delay,
        )
        time.sleep(delay)


# --- the durable ledger ----------------------------------------------------


def _prior_from_rows(rows: list[ToolCall]) -> tuple[ToolCall | None, bool]:
    succeeded = [r for r in rows if r.success is True]
    ambiguous = any(r.success is None for r in rows)
    return (succeeded[-1] if succeeded else None), ambiguous


def _prior(key: str) -> tuple[ToolCall | None, bool]:
    """(a prior SUCCESSFUL call for this effect, whether any prior attempt is
    still ambiguous). Ordered so the newest success wins, which matters only
    if an effect somehow succeeded twice before this existed."""
    with get_session() as session:
        rows = list(
            session.exec(
                select(ToolCall).where(ToolCall.effect_key == key).order_by(ToolCall.created_at)
            ).all()
        )
    return _prior_from_rows(rows)


def _advisory_lock_key(key: str) -> int:
    """A deterministic signed 64-bit key for pg_advisory_xact_lock(bigint)."""
    return int.from_bytes(sha256(key.encode("utf-8")).digest()[:8], "big", signed=True)


def _open_call_in_session(session, subtask_id: str, tool_name: str, kwargs: dict, key: str) -> str:
    row = ToolCall(
        subtask_id=subtask_id,
        tool_name=tool_name,
        input=kwargs,
        output={},
        success=None,
        latency_ms=0,
        effect_key=key,
    )
    session.add(row)
    return row.id


def _open_call(subtask_id: str, tool_name: str, kwargs: dict, key: str) -> str:
    """Record the attempt BEFORE the effect, and commit. This row is the only
    thing that can survive a kill during the call -- success stays NULL, which
    means AMBIGUOUS, not failed."""
    with get_session() as session:
        row_id = _open_call_in_session(session, subtask_id, tool_name, kwargs, key)
        session.commit()
        return row_id


def _ambiguous_refusal(tool_name: str, safety: ExecutionSafety, key: str) -> ToolOutcome:
    from agentsys.tools.base import ToolResult  # local: tools/base imports this module

    return ToolOutcome(
        result=ToolResult(
            success=False,
            error=(
                f"not retried: an earlier attempt at this exact {tool_name} call is still "
                "in progress or was interrupted and its outcome is unknown. It was not "
                "performed again. If a worker died, recovery will clear this block only "
                "for tools whose semantics make repetition safe; otherwise check whether "
                "it already took effect, or report that a person needs to."
            ),
        ),
        attempts=0,
        safety=safety,
        failure=FailureKind.UNKNOWN,
        effect_key=key,
        refused=True,
    )


def _claim_effect(
    *,
    tool_name: str,
    kwargs: dict,
    key: str,
    subtask_id: str,
    safety: ExecutionSafety,
) -> str | ToolOutcome:
    """Atomically claim the right to execute one effect.

    The transaction-scoped advisory lock serialises "look for prior rows,
    decide, insert the in-flight row" for one effect_key. It is released by
    the commit that makes the in-flight row durable before the tool runs, so a
    killed worker cannot leave a database lock behind.
    """
    from agentsys.tools.base import ToolResult  # local: tools/base imports this module

    with get_session() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _advisory_lock_key(key)})
        rows = list(
            session.exec(
                select(ToolCall).where(ToolCall.effect_key == key).order_by(ToolCall.created_at)
            ).all()
        )
        succeeded, ambiguous = _prior_from_rows(rows)

        if succeeded is not None:
            logger.info("effect %s already succeeded (%s); reusing", key[:12], tool_name)
            return ToolOutcome(
                result=ToolResult(success=True, output=succeeded.output),
                attempts=0,
                safety=safety,
                effect_key=key,
                deduped=True,
            )

        if ambiguous:
            return _ambiguous_refusal(tool_name, safety, key)

        row_id = _open_call_in_session(session, subtask_id, tool_name, kwargs, key)
        session.commit()
        return row_id


def _close_call(row_id: str, result: "ToolResult", latency_ms: int) -> None:
    with get_session() as session:
        row = session.get(ToolCall, row_id)
        row.output = result.output if result.success else {"error": result.error}
        row.success = result.success
        row.latency_ms = latency_ms
        session.add(row)
        session.commit()


def _record_call(
    subtask_id: str, tool_name: str, kwargs: dict, result: "ToolResult", latency_ms: int
) -> None:
    """The pre-Phase-3 single write, kept verbatim for reads: one row, after
    the fact, no effect key. A read has no effect to dedupe and none to
    recover, so paying two transactions for it would buy nothing."""
    with get_session() as session:
        session.add(
            ToolCall(
                subtask_id=subtask_id,
                tool_name=tool_name,
                input=kwargs,
                output=result.output if result.success else {"error": result.error},
                success=result.success,
                latency_ms=latency_ms,
            )
        )
        session.commit()


def execute_tool(
    tool: "Tool",
    kwargs: dict[str, Any],
    *,
    task_id: str,
    subtask_id: str,
    action_type: ActionType,
    user_id: str | None = None,
) -> ToolOutcome:
    """Execute one already-validated, already-permitted tool call safely, and
    record it durably. The one seam that turns "the model asked for this" into
    "this was claimed, reused, refused, or recorded in the execution ledger".

    DEDUPE SCOPE IS action_type, NOT A NEW FIELD. Only a non-READ call is
    deduped. That is load-bearing, not a shortcut: google_photos_pick returns
    `_awaiting_human`, the task pauses, a human picks, the task resumes and
    calls the IDENTICAL tool with the IDENTICAL arguments -- and must reach
    the tool, because the second call is what collects the selection.
    Re-searching after a write and polling a status are the same shape, which
    is exactly why deadcalls.py never blocks a repeated success either.
    """
    safety = safety_of(tool)
    started = datetime.now(timezone.utc)

    def elapsed_ms() -> int:
        return int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    if action_type is ActionType.READ:
        outcome = run_with_retry(tool, kwargs)
        _record_call(subtask_id, tool.name, kwargs, outcome.result, elapsed_ms())
        return outcome

    key = effect_key(tool.name, kwargs, task_id=task_id, user_id=user_id)
    claim = _claim_effect(
        tool_name=tool.name,
        kwargs=kwargs,
        key=key,
        subtask_id=subtask_id,
        safety=safety,
    )
    if isinstance(claim, ToolOutcome):
        return claim

    row_id = claim
    outcome = run_with_retry(tool, kwargs)
    _close_call(row_id, outcome.result, elapsed_ms())
    outcome.effect_key = key
    return outcome


def resolve_ambiguous_calls(subtask_ids: list[str]) -> dict[str, int]:
    """Route the tool calls a dead worker left in flight, by the tool's own
    safety mode. Called by recovery.reconcile_orphaned_subtasks, which already
    knows which subtasks were stranded.

        IDEMPOTENT                 -> resolved to failed. Repeating is safe by
                                      classification, so the effect stops
                                      blocking and a re-proposal executes.
        VERIFY_BEFORE_RETRY        -> left ambiguous. execute_tool refuses a
        NON_RETRYABLE_SIDE_EFFECT     matching call, naming why.

    The unresolved NULL row IS the block -- no new escalation kind, no new
    table. A refusal surfaces as a failed step, which the existing
    unproductive-streak and budget machinery already escalates on.
    """
    from agentsys.tools.registry import get_registry

    if not subtask_ids:
        return {"resolved": 0, "held": 0}

    registry = get_registry()
    resolved = held = 0
    with get_session() as session:
        rows = session.exec(
            select(ToolCall).where(
                ToolCall.subtask_id.in_(subtask_ids),  # type: ignore[attr-defined]
                ToolCall.success.is_(None),  # type: ignore[attr-defined]
            )
        ).all()
        for row in rows:
            try:
                safety = safety_of(registry.get(row.tool_name))
            except KeyError:
                # The tool is gone (a config change, a dropped MCP server). We
                # cannot establish that repeating it is safe, so we do not.
                safety = ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT
            if safety is ExecutionSafety.IDEMPOTENT:
                row.success = False
                row.output = {
                    "error": (
                        "a worker died during this call; its outcome is unknown. This tool is "
                        "idempotent, so repeating it is safe."
                    )
                }
                session.add(row)
                resolved += 1
            else:
                held += 1
        if resolved:
            session.commit()

    if resolved or held:
        logger.warning(
            "recovery: %d ambiguous tool call(s) cleared as safe to repeat, %d held as unsafe",
            resolved, held,
        )
    return {"resolved": resolved, "held": held}
