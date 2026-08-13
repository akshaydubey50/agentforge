from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlmodel import select

from agentsys.config import settings as agent_settings

import agentsys.db.models  # noqa: F401  registers tables on SQLModel.metadata before init_db()
from agentsys.db.models import (
    Escalation,
    EscalationStatus,
    LlmCall,
    MemoryEntry,
    Subtask,
    SubtaskStatus,
    Task,
    TaskStatus,
    ToolCall,
    TraceSpan,
)
from agentsys.db.session import get_session, init_db
from agentsys.escalations import EscalationError, apply_escalation_decision
from agentsys.memory.chroma_client import get_chroma_client
from agentsys.schemas import (
    CreateTaskRequest,
    EscalationDecisionRequest,
    EscalationListOut,
    EscalationOut,
    MemoryEntryOut,
    MemoryListOut,
    SubtaskOut,
    TaskDetailOut,
    TaskListOut,
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

# Not auth: this is connectivity, not access control. Origins come from
# CORS_ALLOWED_ORIGINS (comma-separated, see config.py) so the deployed web/
# frontend's real origin can be added without a code change -- defaults to
# just the Next.js dev server. Tighten (or replace with per-tenant auth) once
# there's a real auth story -- see docs on the open auth gap.
app.add_middleware(
    CORSMiddleware,
    allow_origins=agent_settings.cors_allowed_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/deep")
def health_deep() -> dict[str, str]:
    with get_session() as session:
        session.exec(select(Task).limit(1)).first()

    get_chroma_client().heartbeat()

    from agentsys.worker import ping_task

    result = ping_task.delay()
    task_id = result.get(timeout=15)

    return {"database": "ok", "chroma": "ok", "celery": "ok" if task_id else "failed"}


@app.get("/v1/tools")
def list_tools() -> dict[str, list[dict[str, str]]]:
    return {"tools": get_registry().list_tools()}


def _subtask_out(s: Subtask) -> SubtaskOut:
    return SubtaskOut(
        id=s.id, position=s.position, description=s.description, depends_on=s.depends_on,
        assigned_tool=s.assigned_tool, status=s.status.value, output=s.output,
        attempt_count=s.attempt_count,
    )


def _task_out(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id, request_text=t.request_text, status=t.status.value, final_output=t.final_output,
        created_at=t.created_at, updated_at=t.updated_at,
    )


@app.post("/v1/tasks", response_model=TaskOut)
def create_task(body: CreateTaskRequest) -> TaskOut:
    from agentsys.worker import run_agent_task

    with get_session() as session:
        task = Task(request_text=body.request_text)
        session.add(task)
        session.commit()
        session.refresh(task)
        out = _task_out(task)

    run_agent_task.delay(out.id)
    return out


# file_io's read action loads with read_text(encoding="utf-8") -- only these
# formats are guaranteed readable by the agent; a PDF/binary upload would
# just throw a decode error inside the tool, so it's rejected up front
# instead of accepted and silently failing later.
_ATTACHABLE_SUFFIXES = {".txt", ".md", ".csv", ".json", ".py", ".log", ".yaml", ".yml"}


@app.post("/v1/tasks/upload", response_model=TaskOut)
def create_task_with_files(
    request_text: str = Form(...), files: list[UploadFile] = File(default=[])
) -> TaskOut:
    from agentsys.worker import run_agent_task

    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in _ATTACHABLE_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported attachment type '{suffix}' -- allowed: {sorted(_ATTACHABLE_SUFFIXES)}",
            )

    with get_session() as session:
        task = Task(request_text=request_text)
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

    run_agent_task.delay(out.id)
    return out


@app.get("/v1/tasks", response_model=TaskListOut)
def list_tasks(status: str = "all", limit: int = 50, offset: int = 0) -> TaskListOut:
    with get_session() as session:
        count_query = select(func.count()).select_from(Task)
        query = select(Task).order_by(Task.created_at.desc())
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
def get_task(task_id: str) -> TaskDetailOut:
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        subtasks = session.exec(
            select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.position)
        ).all()
    return TaskDetailOut(**_task_out(task).model_dump(), subtasks=[_subtask_out(s) for s in subtasks])


@app.get("/v1/tasks/{task_id}/trace", response_model=list[TraceSpanOut])
def get_task_trace(task_id: str) -> list[TraceSpanOut]:
    with get_session() as session:
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
        id=e.id, task_id=e.task_id, subtask_id=e.subtask_id, reason=e.reason,
        status=e.status.value, decision_note=e.decision_note, decided_by=e.decided_by,
        created_at=e.created_at, decided_at=e.decided_at,
    )


@app.get("/v1/escalations", response_model=EscalationListOut)
def list_escalations(
    status: str = "pending", limit: int = 50, offset: int = 0, task_id: str | None = None
) -> EscalationListOut:
    with get_session() as session:
        count_query = select(func.count()).select_from(Escalation)
        query = select(Escalation).order_by(Escalation.created_at.desc())
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
def decide_escalation(escalation_id: str, body: EscalationDecisionRequest) -> EscalationOut:
    from agentsys.worker import run_agent_task

    try:
        escalation, should_resume = apply_escalation_decision(
            escalation_id,
            decision=body.decision,
            note=body.note,
            decided_by=body.decided_by,
            override_output=body.override_output,
        )
    except EscalationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    if should_resume:
        run_agent_task.delay(escalation.task_id)
    return _escalation_out(escalation)


@app.get("/v1/analytics")
def analytics() -> dict:
    with get_session() as session:
        tasks = session.exec(select(Task)).all()
        tool_calls = session.exec(select(ToolCall)).all()
        escalations = session.exec(select(Escalation)).all()
        llm_calls = session.exec(select(LlmCall)).all()

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

    cost_by_purpose: dict[str, float] = {}
    for lc in llm_calls:
        cost_by_purpose[lc.purpose] = round(cost_by_purpose.get(lc.purpose, 0.0) + lc.cost_usd, 6)

    return {
        "tasks_by_status": by_status,
        "tool_stats": by_tool,
        "escalations_by_status": escalations_by_status,
        "total_tasks": len(tasks),
        "total_tool_calls": len(tool_calls),
        "total_cost_usd": round(sum(lc.cost_usd for lc in llm_calls), 6),
        "cost_by_purpose": cost_by_purpose,
    }


@app.get("/v1/memory", response_model=MemoryListOut)
def list_memory(kind: str = "all", limit: int = 50, offset: int = 0) -> MemoryListOut:
    with get_session() as session:
        count_query = select(func.count()).select_from(MemoryEntry)
        query = select(MemoryEntry).order_by(MemoryEntry.created_at.desc())
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
