import json

from pydantic import BaseModel

from agentsys.config import PROJECT_ROOT


class GoldenTask(BaseModel):
    id: str
    category: str
    request_text: str
    expected_tool_calls: list[str] = []
    """Tool names a correct run should have used. Order-insensitive, and not
    exhaustive -- scored as coverage (tool_call_precision in metrics.py), not
    an exact-match assertion, since a correct run can reasonably use extra
    tools (a retry, an exploratory lookup) the golden case didn't anticipate."""
    expected_outcome: str
    """Free-text description of what a correct final_output looks like, fed
    to an LLM judge (judge_outcome in metrics.py) -- same pattern as
    src/rag/eval's expected_facts, just prose instead of a fact list, since
    an agent task's "correct answer" is often a behavior description
    ("should decline", "should escalate") rather than a checkable fact."""
    should_escalate: bool = False
    """Whether a CORRECT run of this task ends in an escalation (a
    deliberately ambiguous request, or a requires_approval tool -- see
    tools/base.py's Tool.needs_approval) rather than a completed answer.
    Getting this wrong in either direction is itself a real failure: acting
    on an ambiguous request is as bad as needlessly escalating a clear one."""
    max_acceptable_steps: int | None = None
    """None when should_escalate is true (an escalated run's step count
    isn't a meaningful efficiency signal -- it stopped on purpose, not
    because it ran out of budget)."""


def load_golden_dataset() -> list[GoldenTask]:
    path = PROJECT_ROOT / "data" / "eval" / "agent_golden_tasks.json"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [GoldenTask(**item) for item in raw]
