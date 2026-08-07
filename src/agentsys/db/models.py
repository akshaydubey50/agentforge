import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class SubtaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    NEEDS_REVISION = "needs_revision"
    DONE = "done"
    ESCALATED = "escalated"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReviewVerdict(str, Enum):
    PASS = "pass"
    REJECT = "reject"
    ESCALATE = "escalate"


class EscalationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TOOK_OVER = "took_over"


class SubAgentRunStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Task(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    request_text: str
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    final_output: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Subtask(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    position: int
    description: str
    depends_on: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    assigned_tool: str | None = None
    status: SubtaskStatus = Field(default=SubtaskStatus.PENDING)
    output: str | None = None
    attempt_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ToolCall(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    subtask_id: str = Field(foreign_key="subtask.id", index=True)
    tool_name: str
    input: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    output: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    success: bool
    latency_ms: int
    created_at: datetime = Field(default_factory=_now)


class Review(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    subtask_id: str = Field(foreign_key="subtask.id", index=True)
    score: int
    verdict: ReviewVerdict
    feedback: str
    created_at: datetime = Field(default_factory=_now)


class Escalation(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    subtask_id: str | None = Field(default=None, foreign_key="subtask.id")
    reason: str
    context: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    status: EscalationStatus = Field(default=EscalationStatus.PENDING)
    decision_note: str | None = None
    decided_by: str | None = None
    created_at: datetime = Field(default_factory=_now)
    decided_at: datetime | None = None


class TraceSpan(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    subtask_id: str | None = Field(default=None, foreign_key="subtask.id")
    span_type: str
    """One of: plan, tool_call, review, memory, escalation, synthesize."""
    name: str
    input: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    output: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    status: str = "ok"
    started_at: datetime = Field(default_factory=_now)
    ended_at: datetime | None = None


class SampleMetric(SQLModel, table=True):
    """Small seeded business-data table the db_query tool is allowed to read
    from — gives the agent something concrete to query during the demo
    scenario without touching the app's own operational tables."""

    __tablename__ = "sample_metric"

    id: int | None = Field(default=None, primary_key=True)
    company: str
    quarter: str
    revenue_usd: int
    headcount: int


class SubAgentRun(SQLModel, table=True):
    """A bounded, isolated tool-use loop delegated to from one subtask via
    the delegate_subagent tool (tools/delegate_subagent.py). Deliberately a
    separate table from Subtask -- it's a runtime-spawned execution, not a
    planned unit of work, and must never be visible to select_subtask_node's
    dependency scheduler."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    subtask_id: str = Field(foreign_key="subtask.id", index=True)
    depth: int
    goal: str
    status: SubAgentRunStatus = Field(default=SubAgentRunStatus.RUNNING)
    output: str | None = None
    created_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None


class LlmCall(SQLModel, table=True):
    """One row per OpenAI call, written by agentsys.cost.record_llm_call right
    after every call site in graph/nodes.py and tools/delegate_subagent.py --
    this is what /v1/analytics' cost figures are aggregated from."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    subtask_id: str | None = Field(default=None, foreign_key="subtask.id")
    purpose: str
    """One of: plan, tool_selection, reasoning, review, synthesize, subagent_step."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    created_at: datetime = Field(default_factory=_now)


class MemoryEntry(SQLModel, table=True):
    """Metadata row for a long-term memory; the embedding + text live in Chroma
    under the same id, so this table is queryable structured metadata while
    Chroma handles semantic retrieval."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str | None = Field(default=None, foreign_key="task.id")
    kind: str
    """One of: episodic (task summary), fact, preference."""
    content: str
    importance: int = Field(default=3)
    created_at: datetime = Field(default_factory=_now)
    last_accessed_at: datetime = Field(default_factory=_now)
