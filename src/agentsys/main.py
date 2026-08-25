from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlmodel import select

from agentsys.config import settings as agent_settings

import agentsys.db.models  # noqa: F401  registers tables on SQLModel.metadata before init_db()
from agentsys import audit, cancellation, cost, idempotency
from agentsys.auth import get_current_user
from agentsys.ratelimit import enforce_task_rate_limit
from agentsys.security_headers import SecurityHeadersMiddleware
from agentsys.db.models import (
    AuditEvent,
    Escalation,
    EscalationStatus,
    LlmCall,
    MemoryEntry,
    Subtask,
    SubtaskStatus,
    Task,
    TaskMessage,
    TaskStatus,
    ToolCall,
    TraceSpan,
    User,
)
from agentsys.db.session import get_session, init_db
from agentsys.escalations import EscalationError, apply_escalation_decision
from agentsys.memory.chroma_client import get_chroma_client
from agentsys.schemas import (
    AuditEventOut,
    AuditListOut,
    CreateTaskRequest,
    EscalationDecisionRequest,
    EscalationListOut,
    EscalationOut,
    MemoryEntryOut,
    MemoryListOut,
    SendTaskMessageRequest,
    SubtaskOut,
    TaskDetailOut,
    TaskListOut,
    TaskMessageOut,
    TaskOut,
    TraceSpanOut,
)
from agentsys.tools.registry import get_registry

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Replaces the older @app.on_event("startup") hook, which FastAPI
    deprecates -- the deprecation became a startup warning once the MCP
    dependency forced fastapi/starlette forward (see requirements.txt)."""
    init_db()
    yield


app = FastAPI(title="Agent Orchestration System", version="0.1.0", lifespan=lifespan)

# Origins come from CORS_ALLOWED_ORIGINS (comma-separated, see config.py) so
# the deployed web/ frontend's real origin can be added without a code
# change -- defaults to just the Next.js dev server. allow_credentials=True
# is required for the session cookie (agentsys/auth.py) to ride along on
# cross-origin fetches from the dashboard -- safe here specifically because
# allow_origins is an explicit list, never "*" (the two are mutually
# exclusive per the CORS spec, and FastAPI enforces it).
app.add_middleware(
    CORSMiddleware,
    allow_origins=agent_settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added AFTER CORSMiddleware, which in Starlette means it wraps it: the
# stack is built inside-out, so the last middleware added is the outermost
# one to see a response. That is what makes these headers appear on CORS
# preflight replies too -- CORSMiddleware answers an OPTIONS itself and
# never calls downstream, so a middleware registered inside it would never
# run for a preflight at all.
app.add_middleware(SecurityHeadersMiddleware)

from agentsys.auth_router import router as auth_router  # noqa: E402
from agentsys.integrations.router import router as google_router  # noqa: E402
from agentsys.system_api import router as system_router  # noqa: E402
from agentsys.events_api import router as events_router  # noqa: E402
from agentsys.artifacts_api import router as artifacts_router  # noqa: E402

app.include_router(auth_router)
app.include_router(google_router)
app.include_router(system_router)
app.include_router(events_router)
app.include_router(artifacts_router)


_CELERY_PING_TIMEOUT_S = 2.0
"""Bounded so a broker with no workers costs a two-second health check, not
a hung request. Long enough for a worker under normal load to answer on the
control channel."""


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/deep")
def health_deep() -> dict[str, str]:
    """Readiness: are the dependencies reachable? Deliberately READ-ONLY and
    bounded.

    This used to enqueue a real Celery task (`ping_task`) that INSERTED a
    `Task` row, then blocked up to 15s on the result. Pointed at a load
    balancer's health check that is unbounded growth in the application's own
    business table plus a request that can hang a worker thread -- a health
    check that degrades the thing it is checking (see
    docs/ARCHITECTURE_AUDIT.md §7.6).

    `control.ping` is the broker-level equivalent: it asks live workers to
    answer over the control channel, creating no queue entry, no result row
    and nothing durable. It proves what readiness actually needs -- a worker
    is up and consuming -- and it takes a timeout.

    Never 5xx. A degraded dependency is reported in the body with 200 so the
    caller can distinguish "this endpoint is broken" from "Chroma is down",
    which an exception would flatten into the same response."""
    checks: dict[str, str] = {}

    try:
        with get_session() as session:
            session.exec(select(func.count()).select_from(Task).limit(1)).first()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 -- report, don't raise
        checks["database"] = f"failed: {type(exc).__name__}"

    try:
        get_chroma_client().heartbeat()
        checks["chroma"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["chroma"] = f"failed: {type(exc).__name__}"

    try:
        from agentsys.worker import celery_app

        replies = celery_app.control.ping(timeout=_CELERY_PING_TIMEOUT_S)
        checks["celery"] = "ok" if replies else "failed: no workers responded"
    except Exception as exc:  # noqa: BLE001
        checks["celery"] = f"failed: {type(exc).__name__}"

    checks["status"] = "ok" if all(v == "ok" for k, v in checks.items() if k != "status") else "degraded"
    return checks


@app.get("/v1/tools")
def list_tools() -> dict[str, list[dict[str, str]]]:
    return {"tools": get_registry().list_tools()}


def _subtask_out(s: Subtask) -> SubtaskOut:
    return SubtaskOut(
        id=s.id, position=s.position, description=s.description, depends_on=s.depends_on,
        assigned_tool=s.assigned_tool, status=s.status.value, output=s.output,
        attempt_count=s.attempt_count, created_at=s.created_at,
    )


def _task_out(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id, request_text=t.request_text, status=t.status.value, final_output=t.final_output,
        created_at=t.created_at, updated_at=t.updated_at,
    )


def _task_message_out(m: TaskMessage) -> TaskMessageOut:
    return TaskMessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at)


# A follow-up only makes sense once the agent has actually stopped and
# produced a result to react to -- while running/pending there's no output
# yet to follow up on, and awaiting_approval already has its own resolution
# path (POST /v1/escalations/{id}/decide), so a chat message there would
# just race the escalation decision.
_MESSAGEABLE_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED}


@app.post("/v1/tasks", response_model=TaskOut)
def create_task(
    body: CreateTaskRequest,
    user: User = Depends(get_current_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    _rate_limited: None = Depends(enforce_task_rate_limit),
) -> TaskOut:
    from agentsys.worker import run_agent_task

    existing_id = idempotency.lookup(user.id, idempotency_key)
    if existing_id:
        with get_session() as session:
            existing = session.get(Task, existing_id)
            if existing:
                return _task_out(existing)

    with get_session() as session:
        task = Task(request_text=body.request_text, owner_id=user.id)
        session.add(task)
        session.commit()
        session.refresh(task)
        out = _task_out(task)

    # Recorded only after the row is committed -- see idempotency.record.
    idempotency.record(user.id, idempotency_key, out.id)
    run_agent_task.delay(out.id)
    return out


# file_io's read action loads with read_text(encoding="utf-8") -- only these
# formats are guaranteed readable by the agent; a PDF/binary upload would
# just throw a decode error inside the tool, so it's rejected up front
# instead of accepted and silently failing later.
_ATTACHABLE_SUFFIXES = {".txt", ".md", ".csv", ".json", ".py", ".log", ".yaml", ".yml"}


@app.post("/v1/tasks/upload", response_model=TaskOut)
def create_task_with_files(
    request_text: str = Form(..., max_length=agent_settings.max_request_text_length),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    _rate_limited: None = Depends(enforce_task_rate_limit),
) -> TaskOut:
    from agentsys.worker import run_agent_task

    existing_id = idempotency.lookup(user.id, idempotency_key)
    if existing_id:
        with get_session() as session:
            existing = session.get(Task, existing_id)
            if existing:
                return _task_out(existing)

    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in _ATTACHABLE_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported attachment type '{suffix}' -- allowed: {sorted(_ATTACHABLE_SUFFIXES)}",
            )

    with get_session() as session:
        task = Task(request_text=request_text, owner_id=user.id)
        session.add(task)
        session.commit()
        session.refresh(task)
        out = _task_out(task)

    saved_names: list[str] = []
    if files:
        task_dir = Path(agent_settings.workspace_dir) / out.id
        task_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            filename = Path(f.filename or "").name  # strip any path components -- traversal guard
            (task_dir / filename).write_bytes(f.file.read())
            saved_names.append(filename)

        # The specialist only sees request_text -- this is how it learns the
        # file exists at all, same reasoning as db_query's tool description
        # needing to state the exact data format (see README's "real bugs
        # found" #1): give it upfront, don't make it guess.
        attachment_note = (
            "\n\n(Attached file"
            + ("s" if len(saved_names) > 1 else "")
            + ": "
            + ", ".join(saved_names)
            + " -- already in your workspace, read with file_io.)"
        )
        with get_session() as session:
            task = session.get(Task, out.id)
            task.request_text = task.request_text + attachment_note
            session.add(task)
            session.commit()
            session.refresh(task)
            out = _task_out(task)

    idempotency.record(user.id, idempotency_key, out.id)
    run_agent_task.delay(out.id)
    return out


@app.get("/v1/tasks", response_model=TaskListOut)
def list_tasks(
    status: str = "all", limit: int = 50, offset: int = 0, user: User = Depends(get_current_user)
) -> TaskListOut:
    with get_session() as session:
        count_query = select(func.count()).select_from(Task).where(Task.owner_id == user.id, Task.is_eval == False)  # noqa: E712
        query = select(Task).where(Task.owner_id == user.id, Task.is_eval == False).order_by(Task.created_at.desc())  # noqa: E712
        if status != "all":
            try:
                status_value = TaskStatus(status)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"unknown status '{status}'")
            count_query = count_query.where(Task.status == status_value)
            query = query.where(Task.status == status_value)
        total = session.exec(count_query).one()
        tasks = session.exec(query.offset(offset).limit(limit)).all()
    return TaskListOut(items=[_task_out(t) for t in tasks], total=total)


@app.get("/v1/tasks/{task_id}", response_model=TaskDetailOut)
def get_task(task_id: str, user: User = Depends(get_current_user)) -> TaskDetailOut:
    with get_session() as session:
        task = session.get(Task, task_id)
        # 404, not 403, on a task owned by someone else -- same reasoning as
        # a private repo returning 404 for a non-collaborator: 403 would
        # confirm the task id exists at all.
        if not task or task.owner_id != user.id:
            raise HTTPException(status_code=404, detail="task not found")
        subtasks = session.exec(
            select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.position)
        ).all()
        messages = session.exec(
            select(TaskMessage).where(TaskMessage.task_id == task_id).order_by(TaskMessage.created_at)
        ).all()
    return TaskDetailOut(
        **_task_out(task).model_dump(),
        subtasks=[_subtask_out(s) for s in subtasks],
        messages=[_task_message_out(m) for m in messages],
    )


# A task that has already stopped has nothing to cancel; these are the
# states where a worker could still be doing (or about to do) real work.
_CANCELLABLE_STATUSES = {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL}


@app.post("/v1/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel_task(task_id: str, user: User = Depends(get_current_user)) -> TaskOut:
    """Stops a task at its next step boundary. Sets status immediately so
    the dashboard reflects the cancel right away, and sets the Redis flag
    the running worker actually checks (see cancellation.py) -- the worker
    can't be preempted mid-LLM-call, so this stops the NEXT step rather
    than the current one."""
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task or task.owner_id != user.id:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status not in _CANCELLABLE_STATUSES:
            raise HTTPException(
                status_code=409, detail=f"task is already {task.status.value} and cannot be cancelled"
            )
        task.status = TaskStatus.CANCELLED
        task.final_output = f"Cancelled by {user.email}."
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
        session.refresh(task)
        out = _task_out(task)

    cancellation.request_cancel(task_id)
    return out


@app.post("/v1/tasks/{task_id}/messages", response_model=TaskOut)
def send_task_message(
    task_id: str,
    body: SendTaskMessageRequest,
    user: User = Depends(get_current_user),
    _rate_limited: None = Depends(enforce_task_rate_limit),
) -> TaskOut:
    """Continues a task past its terminal state: records the follow-up as
    its own TaskMessage row (so the feed can render "you said X" instead of
    silently folding it into request_text), flips the task back to running,
    and re-invokes the same agent_step loop -- a fresh graph.invoke() just
    like an escalation resume, since agent_step_node already re-derives
    everything it needs from Postgres each time. See
    graph/nodes.py's _gather_conversation_history for how the loop picks
    the follow-up up."""
    from agentsys.worker import run_agent_task

    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="message content cannot be empty")

    with get_session() as session:
        task = session.get(Task, task_id)
        if not task or task.owner_id != user.id:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status not in _MESSAGEABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"task must be completed or failed to continue the conversation "
                    f"(currently {task.status.value})"
                ),
            )
        session.add(TaskMessage(task_id=task_id, content=content))
        task.status = TaskStatus.RUNNING
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
        session.refresh(task)
        out = _task_out(task)

    # Deliberate restart -- drop any stale cancel flag, or the resumed run
    # would immediately cancel itself on a flag from an earlier stop.
    cancellation.clear_cancel(task_id)
    run_agent_task.delay(task_id)
    return out


@app.get("/v1/tasks/{task_id}/trace", response_model=list[TraceSpanOut])
def get_task_trace(task_id: str, user: User = Depends(get_current_user)) -> list[TraceSpanOut]:
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task or task.owner_id != user.id:
            raise HTTPException(status_code=404, detail="task not found")
        spans = session.exec(
            select(TraceSpan).where(TraceSpan.task_id == task_id).order_by(TraceSpan.started_at)
        ).all()
    return [
        TraceSpanOut(
            id=s.id, subtask_id=s.subtask_id, span_type=s.span_type, name=s.name,
            input=s.input, output=s.output, status=s.status, started_at=s.started_at,
            ended_at=s.ended_at,
        )
        for s in spans
    ]


def _escalation_out(e: Escalation) -> EscalationOut:
    return EscalationOut(
        id=e.id, task_id=e.task_id, subtask_id=e.subtask_id, kind=e.kind, reason=e.reason,
        context=e.context or {}, status=e.status.value, decision_note=e.decision_note,
        decided_by=e.decided_by, created_at=e.created_at, decided_at=e.decided_at,
    )


@app.get("/v1/escalations", response_model=EscalationListOut)
def list_escalations(
    status: str = "pending",
    limit: int = 50,
    offset: int = 0,
    task_id: str | None = None,
    user: User = Depends(get_current_user),
) -> EscalationListOut:
    with get_session() as session:
        # Escalation has no owner_id of its own -- ownership is inherited
        # through its task, same as every other task-scoped table.
        count_query = (
            select(func.count()).select_from(Escalation).join(Task, Escalation.task_id == Task.id)
            .where(Task.owner_id == user.id)
        )
        query = (
            select(Escalation).join(Task, Escalation.task_id == Task.id)
            .where(Task.owner_id == user.id).order_by(Escalation.created_at.desc())
        )
        if status != "all":
            try:
                status_value = EscalationStatus(status)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"unknown status '{status}'")
            count_query = count_query.where(Escalation.status == status_value)
            query = query.where(Escalation.status == status_value)
        if task_id is not None:
            count_query = count_query.where(Escalation.task_id == task_id)
            query = query.where(Escalation.task_id == task_id)
        total = session.exec(count_query).one()
        escalations = session.exec(query.offset(offset).limit(limit)).all()
    return EscalationListOut(items=[_escalation_out(e) for e in escalations], total=total)


@app.post("/v1/escalations/{escalation_id}/decide", response_model=EscalationOut)
def decide_escalation(
    escalation_id: str,
    body: EscalationDecisionRequest,
    request: Request,
    user: User = Depends(get_current_user),
) -> EscalationOut:
    from agentsys.worker import run_agent_task

    with get_session() as session:
        escalation = session.get(Escalation, escalation_id)
        if not escalation:
            raise HTTPException(status_code=404, detail="escalation not found")
        task = session.get(Task, escalation.task_id)
        if not task or task.owner_id != user.id:
            raise HTTPException(status_code=404, detail="escalation not found")

    try:
        escalation, should_resume = apply_escalation_decision(
            escalation_id,
            decision=body.decision,
            note=body.note,
            # The signed-in user's real identity, not the client-supplied
            # body.decided_by -- an approve/reject is an audit-relevant
            # action, it shouldn't be spoofable via request body.
            decided_by=user.email,
            override_output=body.override_output,
            # Same reasoning as decided_by: the audit row records who the
            # server knows made the call and from where, not what the client
            # claimed about itself.
            actor_id=user.id,
            **audit.request_context(request),
        )
    except EscalationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    if should_resume:
        # Same reasoning as send_task_message -- a deliberate restart must
        # not inherit a stale cancel flag.
        cancellation.clear_cancel(escalation.task_id)
        run_agent_task.delay(escalation.task_id)
    return _escalation_out(escalation)


@app.get("/v1/audit", response_model=AuditListOut)
def list_audit_events(
    action: str = "all",
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
) -> AuditListOut:
    """The caller's own security activity.

    Scoped to actor_id == the signed-in user, the same isolation boundary
    every other endpoint here enforces. Note what that deliberately excludes:
    failed sign-ins, which have no actor_id at all because no identity was
    ever established. They are in the table and they are the events an
    investigator most wants -- but attributing an anonymous failure to an
    account by guessing at the attempted email would be inventing a fact,
    and showing every user every failed login attempt would leak far more
    than it explains. Surfacing those belongs behind the admin role that
    arrives with Phase 4, not here.

    No verification endpoint alongside this one, for the same reason:
    verify_chain() covers the whole chain across all users, so exposing it
    before roles exist would mean any account could trigger a full-table
    scan of everyone's history. It stays a library call for a scheduled job
    (see audit.chain_head) until there is a role to gate it behind.
    """
    with get_session() as session:
        query = select(AuditEvent).where(AuditEvent.actor_id == user.id)
        count_query = (
            select(func.count()).select_from(AuditEvent).where(AuditEvent.actor_id == user.id)
        )
        if action != "all":
            query = query.where(AuditEvent.action == action)
            # The count has to carry the same filter, or a filtered page
            # reports the unfiltered total and the client's pagination walks
            # off the end of a result set that was never that long.
            count_query = count_query.where(AuditEvent.action == action)
        total = session.exec(count_query).one()
        rows = session.exec(
            query.order_by(AuditEvent.seq.desc()).offset(offset).limit(min(limit, 200))
        ).all()
    return AuditListOut(
        items=[
            AuditEventOut(
                id=r.id,
                seq=r.seq,
                created_at=r.created_at,
                actor_label=r.actor_label,
                action=r.action,
                target_type=r.target_type,
                target_id=r.target_id,
                outcome=r.outcome,
                ip=r.ip,
                user_agent=r.user_agent,
                meta=r.meta,
                prev_hash=r.prev_hash,
                hash=r.hash,
            )
            for r in rows
        ],
        total=total,
    )


@app.get("/v1/analytics")
def analytics(user: User = Depends(get_current_user)) -> dict:
    with get_session() as session:
        tasks = session.exec(select(Task).where(Task.owner_id == user.id, Task.is_eval == False)).all()  # noqa: E712
        # ToolCall has no task_id of its own -- it's owned via subtask_id ->
        # Subtask.task_id -> Task.owner_id, one join hop further than
        # Escalation/LlmCall (which do carry task_id directly).
        tool_calls = session.exec(
            select(ToolCall)
            .join(Subtask, ToolCall.subtask_id == Subtask.id)
            .join(Task, Subtask.task_id == Task.id)
            .where(Task.owner_id == user.id)
        ).all()
        escalations = session.exec(
            select(Escalation).join(Task, Escalation.task_id == Task.id).where(Task.owner_id == user.id)
        ).all()
        llm_calls = session.exec(
            select(LlmCall).join(Task, LlmCall.task_id == Task.id).where(Task.owner_id == user.id)
        ).all()

    by_status = {}
    for t in tasks:
        by_status[t.status.value] = by_status.get(t.status.value, 0) + 1

    by_tool: dict[str, dict] = {}
    for tc in tool_calls:
        stats = by_tool.setdefault(tc.tool_name, {"calls": 0, "successes": 0, "total_latency_ms": 0})
        stats["calls"] += 1
        stats["successes"] += 1 if tc.success else 0
        stats["total_latency_ms"] += tc.latency_ms
    for name, stats in by_tool.items():
        stats["success_rate"] = round(stats["successes"] / stats["calls"], 3) if stats["calls"] else None
        stats["avg_latency_ms"] = round(stats["total_latency_ms"] / stats["calls"]) if stats["calls"] else None

    escalations_by_status = {}
    for e in escalations:
        escalations_by_status[e.status.value] = escalations_by_status.get(e.status.value, 0) + 1

    # Priced NOW from stored tokens, not summed off the cached cost_usd
    # column -- see cost.py. Correcting a rate then fixes this page's history
    # instead of leaving a wrong number baked into old rows.
    spend = cost.spend_from_rows(llm_calls)

    return {
        "tasks_by_status": by_status,
        "tool_stats": by_tool,
        "escalations_by_status": escalations_by_status,
        "total_tasks": len(tasks),
        "total_tool_calls": len(tool_calls),
        "total_cost_usd": spend["usd"],
        "cost_by_purpose": spend["by_purpose"],
        "cost_by_model": spend["by_model"],
        "cost_is_estimated": spend["estimated"],
    }


@app.get("/v1/memory", response_model=MemoryListOut)
def list_memory(
    kind: str = "all", limit: int = 50, offset: int = 0, user: User = Depends(get_current_user)
) -> MemoryListOut:
    with get_session() as session:
        count_query = select(func.count()).select_from(MemoryEntry).where(MemoryEntry.owner_id == user.id)
        query = select(MemoryEntry).where(MemoryEntry.owner_id == user.id).order_by(MemoryEntry.created_at.desc())
        if kind != "all":
            count_query = count_query.where(MemoryEntry.kind == kind)
            query = query.where(MemoryEntry.kind == kind)
        total = session.exec(count_query).one()
        entries = session.exec(query.offset(offset).limit(limit)).all()
    return MemoryListOut(
        items=[
            MemoryEntryOut(
                id=e.id, task_id=e.task_id, kind=e.kind, content=e.content, importance=e.importance,
                created_at=e.created_at, last_accessed_at=e.last_accessed_at,
            )
            for e in entries
        ],
        total=total,
    )
