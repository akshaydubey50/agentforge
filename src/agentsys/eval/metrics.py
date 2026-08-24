from pydantic import BaseModel, Field

from agentsys.config import settings
from agentsys.eval.golden_dataset import GoldenTask
from agentsys.llm import structured_complete

_JUDGE_PROMPT = """You are grading an autonomous agent's run against an expected outcome.

Original request: {request}

Expected outcome (what a CORRECT run looks like -- this may describe a behavior, like \
declining or escalating, rather than a fact to state):
{expected_outcome}

What actually happened -- the agent's final output ({final_status}):
{final_output}

Score correctness from 0.0 to 1.0:
- 1.0: the actual outcome matches the expected outcome (a factual answer that's right, or \
  behavior -- declining, escalating -- that matches what was expected)
- partial credit for a broadly right answer with a real inaccuracy, or a plausible-but-not-quite \
  match to expected behavior
- 0.0: wrong answer, fabricated information, or behavior that contradicts what was expected \
  (e.g. confidently answering when escalating was the correct call)
"""


class OutcomeJudgment(BaseModel):
    correctness: float = Field(ge=0.0, le=1.0)
    reasoning: str


def judge_outcome(task: GoldenTask, final_status: str, final_output: str | None) -> OutcomeJudgment:
    prompt = _JUDGE_PROMPT.format(
        request=task.request_text,
        expected_outcome=task.expected_outcome,
        final_status=final_status,
        final_output=final_output or "(no final output -- the task did not complete)",
    )
    # reviewer_llm_model, not llm_model -- same "don't let the judge share
    # blind spots with the thing it's judging" reasoning as the graph's own
    # reviewer node (see config.py's reviewer_llm_model docstring).
    parsed, _ = structured_complete(prompt, OutcomeJudgment, model=settings.reviewer_llm_model)
    return parsed or OutcomeJudgment(correctness=0.0, reasoning="judge returned no output")


def tool_call_precision(task: GoldenTask, actual_tool_names: list[str]) -> float | None:
    """Fraction of expected_tool_calls that actually got used. None (excluded
    from aggregates) when the case expects no specific tool (a pure-reasoning
    or escalation case), where there's nothing to check coverage against."""
    if not task.expected_tool_calls:
        return None
    expected = set(task.expected_tool_calls)
    actual = set(actual_tool_names)
    return len(expected & actual) / len(expected)


def escalation_correctness(task: GoldenTask, did_escalate: bool) -> bool:
    return did_escalate == task.should_escalate


def step_efficiency(task: GoldenTask, steps_taken: int) -> float | None:
    """1.0 at or under budget, degrading smoothly past it rather than a hard
    cliff -- a run that took one extra step isn't as bad as one that took
    five. None (excluded from aggregates) for should_escalate cases, whose
    max_acceptable_steps is deliberately unset (see golden_dataset.py)."""
    if task.max_acceptable_steps is None:
        return None
    if steps_taken <= task.max_acceptable_steps:
        return 1.0
    return max(0.0, task.max_acceptable_steps / steps_taken)
