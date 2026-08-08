from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CreateTaskRequest(BaseModel):
    request_text: str


class TaskOut(BaseModel):
    id: str
    request_text: str
    status: str
    final_output: str | None
    created_at: datetime
    updated_at: datetime


class SubtaskOut(BaseModel):
    id: str
    position: int
    description: str
    depends_on: list[str]
    assigned_tool: str | None
    status: str
    output: str | None
    attempt_count: int


class TaskDetailOut(TaskOut):
    subtasks: list[SubtaskOut]


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int


class EscalationOut(BaseModel):
    id: str
    task_id: str
    subtask_id: str | None
    reason: str
    status: str
    decision_note: str | None
    decided_by: str | None
    created_at: datetime
    decided_at: datetime | None


class EscalationListOut(BaseModel):
    items: list[EscalationOut]
    total: int


class EscalationDecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "take_over"]
    note: str = ""
    decided_by: str = "human"
    override_output: str | None = None


class TraceSpanOut(BaseModel):
    id: str
    subtask_id: str | None
    span_type: str
    name: str
    input: dict
    output: dict
    status: str
    started_at: datetime
    ended_at: datetime | None


class MemoryEntryOut(BaseModel):
    id: str
    task_id: str | None
    kind: str
    content: str
    importance: int
    created_at: datetime
    last_accessed_at: datetime
