from typing import Literal

from pydantic import BaseModel, Field


class SubtaskSpec(BaseModel):
    description: str
    depends_on_positions: list[int] = Field(
        default_factory=list,
        description="0-indexed positions of other subtasks in this same plan that must complete first.",
    )


class PlanOutput(BaseModel):
    subtasks: list[SubtaskSpec]
    confidence: int = Field(ge=1, le=5, description="1 = very unsure this plan will work, 5 = confident")
    reasoning: str


class ToolChoice(BaseModel):
    tool_name: str
    tool_input_json: str = Field(
        description="A JSON object (as a string) with exactly the keyword arguments that tool's run() expects."
    )
    rationale: str


class ReviewOutput(BaseModel):
    score: int = Field(ge=1, le=5)
    verdict: Literal["pass", "reject", "escalate"]
    feedback: str
