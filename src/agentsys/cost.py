"""Records token usage for every LLM call, and reads spend back off it.

Called from every LLM call site (graph/nodes.py's five, plus
tools/delegate_subagent.py's step loop) right after the call succeeds.

WHAT IS GROUND TRUTH HERE

Tokens. They are what the provider actually counted and they never change.
`cost_usd` is still written on every row, but it is a CACHE of what pricing
said at the time, not the source of truth -- rates get cut, models get
repriced, and a model litellm didn't know yet was recorded at $0.00
permanently.

So every read goes through `spend_for`, which re-derives dollars from tokens
at the moment someone asks (see pricing.py). Correcting a wrong rate then
fixes every historical figure, instead of leaving a bad number baked into
rows nobody will ever backfill.

Nothing should `SELECT SUM(cost_usd)`. That is the thing this module exists
to stop.
"""

import logging
from collections import defaultdict

from sqlmodel import select

from agentsys import otel
from agentsys import pricing
from agentsys.db.models import LlmCall
from agentsys.db.session import get_session
from agentsys.telemetry import exception_category

logger = logging.getLogger(__name__)


def record_llm_call(task_id: str, subtask_id: str | None, purpose: str, completion) -> None:
    model = completion.model
    usage = completion.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    # Half-price cached prompt tokens. Recorded rather than re-derived because
    # only the response knows it, and without it read-time pricing overstates
    # spend on the long-prompt calls that cache best (see pricing.cost_for).
    details = getattr(usage, "prompt_tokens_details", None) if usage else None
    cached_tokens = getattr(details, "cached_tokens", None) if details else None
    if cached_tokens is None and isinstance(details, dict):
        cached_tokens = details.get("cached_tokens")
    cached_tokens = int(cached_tokens or 0)

    cost_usd = pricing.cost_for(model, prompt_tokens, completion_tokens, cached_tokens)

    with get_session() as session:
        row = LlmCall(
            task_id=task_id,
            subtask_id=subtask_id,
            purpose=purpose,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            # Kept for continuity and for anything reading rows directly,
            # but derived the same way a read would derive it -- so the
            # cached value and the live one agree at write time and diverge
            # only when a rate is later corrected.
            cost_usd=cost_usd,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        llm_call_id = row.id
    try:
        otel.record_llm_usage(
            task_id=task_id,
            subtask_id=subtask_id,
            llm_call_id=llm_call_id,
            purpose=purpose,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost_usd,
        )
    except Exception as exc:  # noqa: BLE001 -- external telemetry is fail-open
        logger.debug("LLM usage telemetry export failed (category=%s)", exception_category(exc))


def spend_from_rows(rows) -> dict:
    """Total spend for a set of LlmCall rows, priced now rather than then.

    Pure over the rows it is handed, so it can be unit-tested without a
    database. Returns the breakdown callers actually want -- by purpose and
    by model -- from one pass, since every caller so far needed at least two
    of them and doing it three times meant three chances to disagree.
    """
    total = 0.0
    tokens_in = tokens_out = 0
    by_purpose: dict[str, float] = defaultdict(float)
    by_model: dict[str, dict] = {}
    estimated = False

    for row in rows:
        cached = getattr(row, "cached_tokens", None)
        if cached is None:
            # Written before cached_tokens existed: it is unknowable whether
            # any of that prompt was cached, so the total is an estimate that
            # may read slightly high. Saying so beats a confident wrong number.
            estimated = True
        usd = pricing.cost_for(row.model, row.prompt_tokens, row.completion_tokens, cached)
        total += usd
        tokens_in += row.prompt_tokens
        tokens_out += row.completion_tokens
        by_purpose[row.purpose] += usd

        entry = by_model.setdefault(
            row.model,
            {"calls": 0, "usd": 0.0, "tokens_in": 0, "tokens_out": 0, "estimated": False},
        )
        entry["calls"] += 1
        entry["usd"] += usd
        entry["tokens_in"] += row.prompt_tokens
        entry["tokens_out"] += row.completion_tokens
        if pricing.is_estimated(row.model) or cached is None:
            entry["estimated"] = True
            estimated = True

    return {
        "usd": round(total, 6),
        "llm_calls": len(rows),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        # True when ANY row fell through to a family guess, so a UI can mark
        # the figure rather than presenting an estimate as exact.
        "estimated": estimated,
        "by_purpose": {k: round(v, 6) for k, v in sorted(by_purpose.items())},
        "by_model": {
            k: {**v, "usd": round(v["usd"], 6)} for k, v in sorted(by_model.items())
        },
    }


def spend_for(task_ids) -> dict:
    """Spend across a set of tasks, derived from tokens."""
    task_ids = list(task_ids)
    if not task_ids:
        return spend_from_rows([])
    with get_session() as session:
        rows = session.exec(select(LlmCall).where(LlmCall.task_id.in_(task_ids))).all()
    return spend_from_rows(rows)
