from typing import Literal

from pydantic import BaseModel, Field


class MemoryCandidate(BaseModel):
    """One proposed durable memory. The LLM proposes this shape; deterministic
    code still validates source ownership, supported kind/scope, size,
    confidence, dedupe, and merge before anything is persisted."""

    kind: Literal["semantic", "episodic", "pinned_decision", "preference", "artifact_reference", "fact"] = Field(
        description="semantic = reusable generalized knowledge; episodic = compact lesson/reference from this task; "
        "pinned_decision = explicit durable decision/constraint; preference = user/org preference; "
        "artifact_reference = metadata-only pointer to an artifact; fact is accepted as a legacy alias for semantic.",
    )
    scope: Literal["task", "conversation", "user"] = Field(
        default="user",
        description="Smallest scope where this memory is useful. Do not choose user for one-off task-local details.",
    )
    content: str = Field(
        default="",
        description="One or two dense sentences. No secrets, no full artifact bodies, no full transcript.",
    )
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    source: dict = Field(
        default_factory=dict,
        description="Optional evidence hints such as subtask ids or artifact refs. The server validates ownership.",
    )


class MemoryCandidateBatch(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)


class RollingConversationSummary(BaseModel):
    current_goal: str = ""
    important_decisions: list[str] = Field(default_factory=list)
    completed_work: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unresolved_failures: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)

    def has_content(self) -> bool:
        return bool(
            self.current_goal.strip()
            or self.important_decisions
            or self.completed_work
            or self.open_questions
            or self.unresolved_failures
            or self.constraints
            or self.references
            or self.artifact_refs
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
    success_criteria: str | None = Field(
        default=None,
        description="Optional compact postcondition for this step. Use a deterministic criterion "
        "when one is obvious, e.g. 'file_exists: report.txt', 'content_matches', "
        "'result_count >= 5', 'exit_code == 0', or 'output_contains: text'. Use 'semantic' "
        "when only the reviewer can judge whether the step is complete.",
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


class GoalVerificationOutput(BaseModel):
    verified: bool = Field(description="True only if the completed subtasks satisfy the user's goal.")
    reason: str = Field(description="One or two sentences explaining what is satisfied or missing.")
    needs_replan: bool = Field(
        default=False,
        description="True if more agent work could plausibly satisfy the goal.",
    )
    needs_human: bool = Field(
        default=False,
        description="True if a person is needed because the system lacks enough information or capability.",
    )


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
