from typing import Literal

from pydantic import BaseModel, Field


class MemoryReflection(BaseModel):
    """The 'should this be remembered' decision made after a task completes.
    Replaces the old unconditional 'save a summary of every task' behavior:
    most tasks produce nothing worth recalling later, so long-term memory
    should hold curated, generalized lessons, not a log of everything."""

    worth_saving: bool = Field(
        description="True ONLY if this task produced a durable, generalizable lesson a FUTURE, "
        "DIFFERENT task would genuinely benefit from. Routine/trivial/one-off tasks (greetings, "
        "tests, a single lookup with no reusable pattern) and anything specific to only this exact "
        "request are worth_saving=false.",
    )
    kind: Literal["episodic", "fact", "preference"] = Field(
        default="episodic",
        description="episodic = a reusable approach to a CLASS of task; fact = a durable objective "
        "fact learned about the tools or data; preference = how the user/org likes work done.",
    )
    content: str = Field(
        default="",
        description="The distilled lesson in one or two sentences, written GENERALIZED to help a "
        "future task -- NOT a log of this one. Good: 'Revenue-growth comparisons: query both "
        "quarters in one db_query, they're in the same sample_metric table.' Bad: 'Task X "
        "completed, used db_query.' Empty when worth_saving is false.",
    )
    importance: int = Field(
        default=3, ge=1, le=5,
        description="1 = marginal, 5 = highly reusable across many future tasks. Drives retrieval "
        "ranking, so be honest -- not everything is a 5.",
    )


class SketchOutput(BaseModel):
    outline: list[str] = Field(
        default_factory=list,
        description="A rough, ordered list of steps you currently expect this request to need. "
        "Non-binding -- purely advisory context for the agent loop that follows; it will not "
        "create any committed work items.",
    )
    confidence: int = Field(ge=1, le=5, description="1 = very unsure this request is achievable, 5 = confident")
    reasoning: str


class NextStepDecision(BaseModel):
    next_action: Literal["act", "finish"]
    subtask_description: str | None = Field(
        default=None,
        description="Required when next_action is 'act' -- a concrete, self-contained "
        "description of the ONE next unit of work.",
    )
    tool_name: str | None = Field(
        default=None,
        description="Required when next_action is 'act'. Set to 'none' if this step is pure "
        "reasoning with no tool call.",
    )
    tool_input_json: str | None = Field(
        default=None,
        description="A JSON object (as a string) satisfying the chosen tool's argument schema -- "
        "every required property, correct types, no property the schema does not list. Arguments "
        "that fail it are rejected before the tool runs. Required when next_action is 'act' and "
        "tool_name isn't 'none'.",
    )
    updated_plan: list[str] | None = Field(
        default=None,
        description="Your current best plan for the WHOLE task, as a short ordered list of "
        "step phrases (e.g. ['Query Q1 revenue', 'Query Q2 revenue', 'Compute growth', "
        "'Write report']). Set this whenever what you've learned changes the plan -- add a "
        "step you now realize is needed, drop one that turned out unnecessary, or reorder. "
        "Leave it null to keep the existing plan unchanged. This keeps the plan a living "
        "to-do list instead of a stale one-time sketch, and is shown to the human watching "
        "the task, so keep the phrases short and readable.",
    )
    rationale: str


class ToolChoice(BaseModel):
    tool_name: str
    tool_input_json: str = Field(
        description="A JSON object (as a string) satisfying the chosen tool's argument schema."
    )
    rationale: str


class ReviewOutput(BaseModel):
    score: int = Field(ge=1, le=5)
    verdict: Literal["pass", "reject", "escalate"]
    feedback: str


class SubAgentStep(BaseModel):
    next_action: Literal["call_tool", "finish"]
    tool_name: str | None = Field(
        default=None, description="Required when next_action is 'call_tool'."
    )
    tool_input_json: str | None = Field(
        default=None,
        description="A JSON object (as a string) satisfying the chosen tool's argument schema. "
        "Required when next_action is 'call_tool'.",
    )
    final_answer: str | None = Field(
        default=None, description="Required when next_action is 'finish'."
    )
    rationale: str


class TriageDecision(BaseModel):
    """The front-door decision: does this turn need the machine, or just an
    answer? Deliberately only two routes -- a classifier with more options is
    a classifier with more ways to be wrong, and everything that isn't
    obviously conversational belongs on the full path anyway."""

    route: Literal["quick", "full"] = Field(
        default="full",
        description=(
            "quick = the turn needs NO tool, NO lookup and NO new work: a greeting, a thanks, an "
            "acknowledgement, or a question answerable purely from what has already been said in "
            "this conversation. full = anything else. When in doubt, choose full."
        ),
    )
    reason: str = Field(
        default="", description="Five words or fewer explaining the choice, shown on the trace."
    )
