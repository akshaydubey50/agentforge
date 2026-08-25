from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agentsys.config import PROJECT_ROOT

QualityLevel = Literal["task", "trajectory", "tool", "rag"]
QualityMetric = Literal[
    "task_completion",
    "answer_relevance",
    "tool_correctness",
    "argument_correctness",
    "step_efficiency",
    "faithfulness",
    "contextual_relevance",
]
ArgMatchMode = Literal["exact", "contains"]


class ExpectedArg(BaseModel):
    tool: str
    arg: str
    value: str
    match: ArgMatchMode = "contains"


class QualityCase(BaseModel):
    id: str
    category: str
    levels: list[QualityLevel]
    input: str
    expected_outcome: str
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_args: list[ExpectedArg] = Field(default_factory=list)
    expected_verification_route: str | None = None
    should_complete: bool | None = None
    should_escalate: bool | None = None
    max_steps: int | None = None
    reference_answer: str | None = None
    reference_evidence: list[str] = Field(default_factory=list)
    metrics: list[QualityMetric]
    deterministic_invariants: list[str] = Field(default_factory=list)

    @field_validator("id", "category", "input", "expected_outcome")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("levels", "metrics")
    @classmethod
    def _non_empty_list(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("must not be empty")
        return value


def quality_dataset_path() -> Path:
    return PROJECT_ROOT / "data" / "eval" / "quality_golden_tasks.json"


def load_quality_dataset(path: Path | None = None) -> list[QualityCase]:
    path = path or quality_dataset_path()
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("quality dataset must be a JSON array")

    cases = [QualityCase(**item) for item in raw]
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate quality case ids: {', '.join(duplicates)}")
    return cases
