"""The judge-free tier — did the right thing happen, yes or no.

Everything in src/agentsys/eval today is graded by an LLM. That is the right
tool for "is this answer good", and the wrong tool for "did it call db_query
with the quarter the user asked for", which has an answer that does not
depend on a model's mood, costs nothing to check, and cannot regress
silently because a judge model was updated underneath you.

So this is the other tier, and the two are never mixed:

    battery (here)   0/1, scored by a PURE FUNCTION. Gates the release.
    judge            0.0-1.0, scored by a model. Reported, thresholded.

WHY THE SCORER IS A PURE FUNCTION

`check_case` takes a case, the tool calls that happened, and the final text.
No database, no network, no model, no clock. That means the scoring logic is
unit-testable with no API key and no running stack (see
tests/test_eval_battery.py), which matters because a scorer nobody can test
is just another thing that can be quietly wrong -- and a scorer that decides
whether you ship had better not be.

Running a case still costs LLM calls, of course; the agent has to actually
do the work. What's free and deterministic is the VERDICT.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from agentsys.config import PROJECT_ROOT


class BatteryCase(BaseModel):
    id: str
    category: str
    request_text: str

    expect_tool: str | None = None
    """The tool a correct run must call. None means "no specific tool"; pair
    it with expect_no_tool=True for a case that should be answered by
    reasoning alone."""

    expect_in_args: dict[str, str] = {}
    """Substrings that must appear in the expected tool's arguments, matched
    case-insensitively. Substring rather than equality on purpose: the point
    is that the agent carried the user's actual constraint into the call
    ('2026-Q1' appears in the SQL), not that it produced one exact string."""

    expect_min_tool_calls: int = 0
    """For multi-step cases -- "this genuinely needs two lookups" is a real
    property that a single expect_tool can't express."""

    expect_no_tool: bool = False
    """A correct run uses no tools at all. Catches the opposite failure from
    everything above: reaching for a tool on a question that didn't need one
    is waste, and on some questions it's a fabrication risk."""

    expect_output_contains: list[str] = []
    """Substrings the final answer must contain, case-insensitive. Kept
    deliberately narrow -- a number, an identifier, a filename. Anything
    needing judgement belongs in the judge tier, not here."""

    should_escalate: bool = False
    """Whether a correct run ends paused for a human rather than answered."""

    expect_max_steps: int | None = None
    """The run must reach its outcome within this many steps.

    Added after a real failure that no other expectation could have caught:
    a task where every tool call SUCCEEDED and the answer was never wrong --
    it just never arrived, because the agent looped re-reading its own
    spilled artifact until the step budget ran out. Correctness assertions
    are all silent on that; "did it get there, and did it get there without
    thrashing" is a separate property and needs its own check."""


@dataclass
class CaseVerdict:
    case_id: str
    category: str
    passed: bool
    reason: str
    tools_called: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "passed": self.passed,
            "reason": self.reason,
            "tools_called": self.tools_called,
        }


def check_case(
    case: BatteryCase,
    tool_calls: list[dict],
    final_output: str | None = None,
    did_escalate: bool = False,
    steps_taken: int | None = None,
) -> CaseVerdict:
    """The whole contract, in one pure function.

    `tool_calls` is [{"tool": name, "args": {...}}, ...] in call order.
    Returns the FIRST failed expectation rather than a list: a verdict you
    act on is more useful than an audit of everything that went wrong, and
    the next run re-checks the rest anyway.
    """
    called = [call["tool"] for call in tool_calls]
    verdict = lambda ok, why: CaseVerdict(case.id, case.category, ok, why, called)  # noqa: E731

    # The step ceiling is checked before everything else, INCLUDING the
    # escalation branch below. A run that thrashed its way to the right
    # outcome still thrashed, and the failure this exists for looked correct
    # by every other measure -- every call succeeded, nothing was wrong, the
    # answer just never arrived.
    if case.expect_max_steps is not None and steps_taken is not None:
        if steps_taken > case.expect_max_steps:
            return verdict(
                False, f"took {steps_taken} steps, expected at most {case.expect_max_steps}"
            )

    # Escalation next: a run that correctly paused for a human hasn't got
    # tool calls or a final answer to check, and grading it against them
    # would fail a case that did exactly the right thing.
    if case.should_escalate != did_escalate:
        return verdict(
            False,
            "expected an escalation, task completed instead"
            if case.should_escalate
            else "escalated, but this request should have been handled",
        )
    if case.should_escalate:
        return verdict(True, "ok")

    if case.expect_no_tool:
        return verdict(not called, "ok" if not called else f"expected no tool call, called {called}")

    if case.expect_tool:
        if case.expect_tool not in called:
            return verdict(False, f"expected {case.expect_tool}, called {called or 'nothing'}")
        args = next(c["args"] for c in tool_calls if c["tool"] == case.expect_tool)
        for key, needle in case.expect_in_args.items():
            if needle.lower() not in str(args.get(key, "")).lower():
                return verdict(False, f"'{needle}' not in args[{key}]")

    if len(called) < case.expect_min_tool_calls:
        return verdict(
            False, f"only {len(called)} tool call(s), expected at least {case.expect_min_tool_calls}"
        )

    text = (final_output or "").lower()
    for needle in case.expect_output_contains:
        if needle.lower() not in text:
            return verdict(False, f"final answer is missing '{needle}'")

    return verdict(True, "ok")


def battery_path() -> Path:
    return PROJECT_ROOT / "data" / "eval" / "agent_battery.jsonl"


def load_battery() -> list[BatteryCase]:
    """One case per line. JSONL rather than a JSON array so a case can be
    added in a one-line diff and a merge conflict is confined to the case
    that caused it."""
    path = battery_path()
    if not path.exists():
        return []
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            cases.append(BatteryCase(**json.loads(line)))
    return cases
