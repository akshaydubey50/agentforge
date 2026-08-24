"""Per-task ledger of tool calls that already failed -- the loop-engineering
half of not burning budget on known-dead actions.

Measured motivation: on one real run, steps 3 and 7 read the identical
.docx and got the identical "isn't readable as text" error, and steps 4 and
8 did the same with a .zip. Four of eleven steps were re-attempts of
something that had already proven impossible, because nothing carried a
failure ACROSS steps -- _revision_feedback only carries it within a single
subtask's retry loop.

Deliberately NOT a permanent-vs-transient classifier. Guessing permanence
from an error string is brittle, and it isn't needed: retrying a transient
failure is already handled inside one subtask by max_subtask_retries. What
was missing is stopping a LATER, DIFFERENT step from re-proposing a call
that already failed. So this records failures only, matches on the exact
(tool, args) pair, and never blocks a successful call (those are legitimately
repeatable -- searching again after a write, polling a status, etc).

Redis via memory/short_term, so it shares the per-task lifecycle already in
place: short_term.clear(task_id) at the end of synthesize_node wipes it
along with the rest of the task's working memory.
"""

from __future__ import annotations

import json

from agentsys.memory import short_term

_FIELD = "dead_calls"

# Injected by _execute_subtask rather than chosen by the model (task
# plumbing and, for the Google tools, the acting user's id). They'd differ
# or be absent between an original call and a later repeat of the "same"
# call, so they're stripped before matching -- otherwise the ledger would
# silently never match anything.
_INJECTED_KEYS = {"task_id", "subtask_id", "user_id", "depth", "goal"}


def _signature(tool_name: str, kwargs: dict) -> str:
    """Canonical (tool, args) identity. sort_keys so two dicts that differ
    only in key order are the same call, and default=str so an unexpected
    non-JSON-serializable argument degrades to a stable string instead of
    raising inside the agent loop."""
    meaningful = {k: v for k, v in kwargs.items() if k not in _INJECTED_KEYS}
    return f"{tool_name}:{json.dumps(meaningful, sort_keys=True, default=str)}"


def record_failure(task_id: str, tool_name: str, kwargs: dict) -> None:
    signature = _signature(tool_name, kwargs)
    if signature in _signatures(task_id):
        return  # already known; don't grow the list with duplicates
    short_term.append_value(task_id, _FIELD, signature)


def _signatures(task_id: str) -> list[str]:
    try:
        return short_term.get_list(task_id, _FIELD)
    except Exception:  # noqa: BLE001 -- a Redis blip must not break the agent loop
        return []


def is_dead(task_id: str, tool_name: str, kwargs: dict) -> bool:
    return _signature(tool_name, kwargs) in _signatures(task_id)


def describe(task_id: str) -> str:
    """Rendered into AGENT_STEP_PROMPT so the model can route around dead
    calls when CHOOSING a step, rather than only being blocked after it
    proposes one. Blocking alone still costs a whole step; this is what
    makes the model stop proposing them."""
    signatures = _signatures(task_id)
    if not signatures:
        return ""
    listed = "\n".join(f"- {s}" for s in signatures)
    return (
        "\nThese exact tool calls ALREADY FAILED earlier in this task and will be skipped if "
        "you propose them again -- they are dead ends, not retry candidates. Choose a different "
        f"tool, different arguments, or a different approach:\n{listed}\n"
    )
