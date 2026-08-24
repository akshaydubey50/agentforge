from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentsys.config import settings

# Request models (the ones carrying client-supplied input) forbid unknown
# fields and bound their text. extra="forbid" turns a typo'd field name into
# a 422 instead of a silently-ignored value; the length bound is the
# cheapest place to stop an oversized body from becoming an expensive
# prompt (see config.py's max_request_text_length). Response models below
# are built from our own DB rows, so neither applies to them.
_REQUEST_CONFIG = ConfigDict(extra="forbid")


class CreateTaskRequest(BaseModel):
    model_config = _REQUEST_CONFIG

    request_text: str = Field(min_length=1, max_length=settings.max_request_text_length)


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
    created_at: datetime


class TaskMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime


class SendTaskMessageRequest(BaseModel):
    model_config = _REQUEST_CONFIG

    content: str = Field(min_length=1, max_length=settings.max_request_text_length)


class TaskDetailOut(TaskOut):
    subtasks: list[SubtaskOut]
    messages: list[TaskMessageOut]


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
    model_config = _REQUEST_CONFIG

    decision: Literal["approve", "reject", "take_over"]
    note: str = Field(default="", max_length=settings.max_request_text_length)
    decided_by: str = "human"
    """Ignored by the API -- decide_escalation records the signed-in user's
    real email instead, since an approve/reject is audit-relevant and
    shouldn't be spoofable from a request body (see main.py). Kept on the
    model only so an older client still sending it doesn't now trip
    extra="forbid"."""
    override_output: str | None = Field(default=None, max_length=settings.max_request_text_length)


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


class MemoryListOut(BaseModel):
    items: list[MemoryEntryOut]
    total: int


class UserOut(BaseModel):
    id: str
    email: str
    name: str | None = None
    picture_url: str | None = None


class SessionOut(BaseModel):
    """One of the caller's own active sessions. Deliberately carries no
    token or token hash -- the id is enough to revoke one, and shipping a
    credential (or anything derived from it) to the client would undo the
    reason sessions are stored hashed in the first place."""

    id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None = None
    user_agent: str | None = None
    current: bool = False
    """True for the session making this request, so the UI can label it and
    avoid offering to revoke the one you're sitting in."""


class GoogleConnectionOut(BaseModel):
    connected: bool
    configured: bool
    """False when GOOGLE_CLIENT_ID/SECRET aren't set -- the UI uses this to
    show 'ask your admin to configure Google' instead of a dead Connect
    button that would just error on click."""
    google_email: str | None = None
    scopes: list[str] = []
    connected_at: datetime | None = None


class AuditEventOut(BaseModel):
    """One audit row as the dashboard sees it.

    Carries seq and hash: the chain is only useful if someone outside the
    database can actually check it, and that means the client has to be able
    to see the linkage. Nothing here is a credential -- meta is redacted at
    write time (see audit.redact), and target_id for a session event is the
    session ROW id, never the token or its hash.

    prev_hash is deliberately included too, so a client holding a page of
    events can verify the links between them without another round trip."""

    id: str
    seq: int
    created_at: datetime
    actor_label: str
    action: str
    target_type: str | None = None
    target_id: str | None = None
    outcome: str
    ip: str | None = None
    user_agent: str | None = None
    meta: dict = {}
    prev_hash: str
    hash: str


class AuditListOut(BaseModel):
    items: list[AuditEventOut]
    total: int
