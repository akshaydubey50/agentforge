import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import select

from agentsys import artifacts, audit, cost, deadcalls, events, policy
from agentsys.cancellation import TaskCancelled, is_cancel_requested
from agentsys.config import settings
from agentsys.db.models import (
    Escalation,
    EscalationStatus,
    LlmCall,
    Review,
    Subtask,
    SubtaskStatus,
    Task,
    TaskMessage,
    TaskStatus,
    ToolCall,
    TraceSpan,
)
from agentsys.db.session import get_session
from agentsys.graph.prompts import (
    AGENT_STEP_PROMPT,
    QUICK_REPLY_PROMPT,
    MEMORY_REFLECTION_PROMPT,
    REASONING_ONLY_PROMPT,
    REVIEW_PROMPT,
    SKETCH_PROMPT,
    SYNTHESIS_PROMPT,
    TOOL_SELECTION_PROMPT,
    TRIAGE_PROMPT,
)
from agentsys.graph.schemas import (
    MemoryReflection,
    NextStepDecision,
    ReviewOutput,
    SketchOutput,
    ToolChoice,
    TriageDecision,
)
from agentsys.graph.state import AgentState
from agentsys.graph.tracing import span
from agentsys.llm import complete, structured_complete
from agentsys.memory import long_term, short_term
from agentsys.policy import PolicyDecision, PolicyDecisionType
from agentsys.sanitize import scrub_nul
from agentsys.tools.base import ToolResult, ToolValidationError, validated_kwargs
from agentsys.tools.registry import get_registry

logger = logging.getLogger(__name__)

_GOOGLE_TOOLS = (
    "gmail_search", "gmail_read", "google_drive_search", "google_drive_read", "google_photos_pick",
)


def _tool_descriptions() -> str:
    return get_registry().describe()


def _injected_kwargs(tool_name: str, task_id: str, subtask_id: str, depth: int = 1) -> dict:
    """The arguments the RUNTIME owns for this call.

    One table for both execution seams: this loop and the delegate_subagent
    tool's own loop, which used to carry a shorter, divergent copy that knew
    about file_io and itself but not about the Google tools' user_id -- so a
    sub-agent calling gmail_search produced a missing-argument error rather
    than a search. `depth` is the only thing that differs between them.

    Applied after validation (see tools/base.py's validated_kwargs), so a
    proposal that names one of these is overwritten rather than honoured.
    These were previously kwargs.setdefault(...) applied to the raw parsed
    JSON, which means the model's value won whenever it supplied one -- and
    two of them are security boundaries, not plumbing: user_id decides whose
    connected Gmail/Drive/Photos a call acts as, task_id decides which task's
    sandbox file_io is confined to.
    """
    if tool_name == "file_io":
        return {"task_id": task_id}
    if tool_name == "generate_tweet":
        # Injected so the tool's internal generate/evaluate/optimize steps
        # write real TraceSpans and book their cost against this task.
        return {"task_id": task_id, "subtask_id": subtask_id}
    if tool_name == "delegate_subagent":
        return {"task_id": task_id, "subtask_id": subtask_id, "depth": depth}
    if tool_name in _GOOGLE_TOOLS:
        with get_session() as session:
            owner_id = session.get(Task, task_id).owner_id
        injected = {"user_id": owner_id}
        if tool_name == "google_photos_pick":
            # ONLY the picker: it survives a pause by remembering its session
            # against the task (see tools/google_photos.py). The other four
            # Google tools take no task_id, and injecting one into them passed
            # an argument their run() has no parameter for -- every gmail_* and
            # google_drive_* call has been failing on "invalid arguments" since
            # the picker landed, because this was one setdefault for all five.
            injected["task_id"] = task_id
        return injected
    return {}


def _rejected_call(task_id: str, subtask_id: str, tool_name: str, proposed: str | None, reason: str) -> str:
    """Record a proposed call that deterministic validation refused, and return
    the observation the agent loop should reason about next.

    On the trace as a tool_call span with status=error, so a refusal is as
    visible as a failure. Deliberately NOT a ToolCall row: that table records
    calls that actually happened, and a rejected proposal is not an effect."""
    with span(
        task_id, "tool_call", tool_name, subtask_id=subtask_id,
        input={"proposed_arguments": proposed},
    ) as s:
        s["output"] = {"rejected": reason}
        s["status"] = "error"
    _record_step_productivity(task_id, productive=False)
    return f"Tool call rejected before it ran: {reason}"


def _check_cancelled(task_id: str) -> None:
    """Cooperative cancellation checkpoint. Called at the top of the two
    nodes that begin real work (sketch_node, agent_step_node) -- a worker
    blocked in an LLM call can't be preempted, so the guarantee is "no
    further expensive steps start", not "stops instantly". See
    cancellation.py."""
    if is_cancel_requested(task_id):
        raise TaskCancelled(task_id)


def _set_task_status(session, task: Task, status: TaskStatus) -> None:
    """Writes a task status, EXCEPT over a cancel. CANCELLED is terminal and
    user-chosen, and a node that passed _check_cancelled before the cancel
    landed will still run to the end of its own body and write its status --
    without this guard that write silently resurrects a cancelled task
    (observed live: a task showed 'cancelled', then flipped back to
    'running' when sketch_node finished the step it was already inside)."""
    if task.status == TaskStatus.CANCELLED:
        return
    previous = task.status
    task.status = status
    task.updated_at = datetime.now(timezone.utc)
    session.add(task)
    # The other half of the event seam (see events.py). Spans say what the
    # agent is doing; this says what state the task is in, which is what a
    # live view needs to stop spinning and show a result.
    if previous != status:
        events.publish(task.id, "task_status", {"status": status.value, "previous": previous.value})


UNPRODUCTIVE_FIELD = "unproductive_streak"


def _task_cost_usd(task_id: str, since: datetime | None = None) -> float:
    """Spend on this task, summed from the LlmCall rows cost.record_llm_call
    already writes after every call -- the ceiling reuses that record rather
    than tracking spend a second way.

    `since` bounds it to the current budget window. Lifetime spend would make
    the ceiling unresumable: a human approving a cost escalation would be
    approving a continuation whose very first check re-reads the same
    already-spent total and escalates again (see _budget_window_start)."""
    with get_session() as session:
        query = select(func.sum(LlmCall.cost_usd)).where(LlmCall.task_id == task_id)
        if since is not None:
            query = query.where(LlmCall.created_at >= since)
        total = session.exec(query).one()
    return float(total or 0.0)


def _unproductive_streak(task_id: str) -> int:
    return int(short_term.get_value(task_id, UNPRODUCTIVE_FIELD) or 0)


def _record_step_productivity(task_id: str, *, productive: bool) -> None:
    """A step is unproductive when its tool call failed or was skipped as a
    known dead call. The counter is a STREAK, not a total -- one good step
    means the agent found a way forward, so the budget for exploring should
    reset with it."""
    streak = 0 if productive else _unproductive_streak(task_id) + 1
    short_term.set_value(task_id, UNPRODUCTIVE_FIELD, streak)


def _task_owner_id(task_id: str) -> str | None:
    """Who this task belongs to, for the policy context. Task.owner_id is the
    isolation boundary the whole app already enforces (see db/models.py's
    User), so this is the same identity the API authorises against -- not a
    second notion of "current user" invented for policy."""
    with get_session() as session:
        task = session.get(Task, task_id)
        return task.owner_id if task else None


def _record_policy_decision(
    task_id: str, subtask_id: str | None, tool_name: str, kwargs: dict, decision: PolicyDecision
) -> None:
    """Put a non-trivial policy decision on the existing audit chain.

    Called for REQUIRE_APPROVAL and DENY only. An ALLOW is the ordinary path
    and is already fully recorded -- a tool_call TraceSpan with its arguments
    plus a ToolCall row with its result -- so writing an audit row for every
    one of those would add a locked, hash-chained insert to the hot path and
    bury the decisions that matter under the ones that don't.

    Arguments are summarised to their KEYS, not their values. The values are a
    proposal shaped by attacker-influenceable content (a web page, an email
    body, a read file), they are already stored in full on the escalation and
    the span, and audit.record's redact() is a key-name filter -- it would
    strip a key called "token" but not a token pasted into a `sql` or `code`
    argument. Naming the fields is what an audit reader needs ("which
    arguments did this decision see?"); reproducing them is a third copy in
    the one table that must never carry a credential.
    """
    audit.record(
        audit.Action.TOOL_POLICY_DECISION,
        actor_id=None,
        actor_label="policy",
        target_type="task",
        target_id=task_id,
        outcome="denied" if decision.decision is PolicyDecisionType.DENY else "pending",
        meta={
            "tool_name": tool_name,
            "action_type": decision.action_type.value,
            "risk": decision.risk.value,
            "decision": decision.decision.value,
            "reason": decision.reason,
            "argument_keys": sorted(kwargs),
            "subtask_id": subtask_id,
        },
    )


def _create_escalation(
    task_id: str, subtask_id: str | None, reason: str, *, kind: str = "plan", context: dict | None = None
) -> None:
    with get_session() as session:
        session.add(
            Escalation(task_id=task_id, subtask_id=subtask_id, reason=reason, kind=kind, context=context or {})
        )
        session.commit()
    with span(task_id, "escalation", "escalation_created", subtask_id=subtask_id, input={"reason": reason}) as s:
        s["output"] = {"reason": reason}


def _revision_feedback(subtask_id: str, attempt_count: int) -> str:
    """On a retry (attempt_count > 1), the specialist needs to know WHY the
    last attempt was rejected, or it just repeats the identical mistake —
    this is what makes reject-and-revise an actual revision, not a re-roll."""
    if attempt_count <= 1:
        return ""
    with get_session() as session:
        latest = session.exec(
            select(Review).where(Review.subtask_id == subtask_id).order_by(Review.created_at.desc())
        ).first()
    if not latest:
        return ""
    return (
        f"\nThis is retry attempt {attempt_count}. Your previous attempt was rejected "
        f"(score {latest.score}/5): {latest.feedback}\nAddress this specifically — don't repeat "
        f"the same approach.\n"
    )


def _gather_prior_context(task_id: str, current_subtask_id: str | None = None) -> str:
    """What's been done so far, under a context budget rather than in full.

    Every completed step used to be included verbatim on every subsequent
    step, so the prompt only ever grew -- measured at 2,772 -> 19,305 tokens
    across twelve steps of one real task. The most recent
    settings.context_recent_steps_full steps stay verbatim (those are what
    the next decision actually turns on); older ones are truncated to a
    recognizable head. Nothing is lost -- the full text stays in Postgres,
    and any output big enough to matter was already spilled to a file the
    agent can re-read on demand (see artifacts.spill)."""
    with get_session() as session:
        done = session.exec(
            select(Subtask)
            .where(Subtask.task_id == task_id, Subtask.status == SubtaskStatus.DONE)
            .order_by(Subtask.position)
        ).all()

    relevant = [s for s in done if s.id != current_subtask_id]
    if not relevant:
        return "(no prior context yet)"

    cutoff = len(relevant) - settings.context_recent_steps_full
    lines = []
    for index, subtask in enumerate(relevant):
        output = subtask.output or ""
        if index < cutoff and len(output) > settings.context_older_step_chars:
            output = (
                f"{output[: settings.context_older_step_chars]}"
                f"… [truncated, {len(output)} chars total — this is an earlier step; "
                f"its full output is still available if a later step needs it]"
            )
        lines.append(f"- {subtask.description}: {output}")

    return "\n".join(lines)


def _turn_start(task_id: str, task_created_at: datetime) -> datetime:
    """Start-of-current-turn timestamp: the most recent follow-up
    TaskMessage, or the task's own creation if there's never been one. Used
    to give every follow-up its own fresh max_task_steps budget (see
    agent_step_node) without needing a new Task column/migration --
    everything's derived from rows that already exist."""
    with get_session() as session:
        last_message = session.exec(
            select(TaskMessage).where(TaskMessage.task_id == task_id).order_by(TaskMessage.created_at.desc())
        ).first()
    return last_message.created_at if last_message else task_created_at


def _naive(value: datetime) -> datetime:
    """These timestamps are compared across three tables and two origins:
    rows read back from Postgres are naive-UTC (TIMESTAMP WITHOUT TIME ZONE),
    while a value just written in Python is aware. Comparing the two raises
    TypeError, which inside the budget check would fail the whole task over a
    tzinfo mismatch. Normalizing to naive-UTC is the cheap way to make the
    comparison total."""
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


BUDGET_ESCALATION_KIND = "budget"
"""The kind covering all four "stop, this has gone far enough" conditions:
step cap, cost ceiling, unproductive streak, and finishing with no work done.
Named because escalations.py has to recognise the same set when a human
approves one."""


def _budget_resume_at(task_id: str) -> datetime | None:
    """When a human last approved a budget escalation for this task.

    This is the fix for the approve/re-escalate loop. Approving a budget
    escalation set the task RUNNING and re-enqueued it, but nothing moved the
    window the budget is measured over -- so agent_step_node recomputed the
    same over-budget count from the same unchanged rows and immediately
    created another identical escalation. Approving was a no-op that
    manufactured a new escalation every time.

    Deliberately derived from the Escalation row that already exists rather
    than from a new column: `decided_at` on the most recent approved budget
    escalation IS the "you may continue from here" marker, it is already
    durable in Postgres, and reusing it needs no migration."""
    with get_session() as session:
        latest = session.exec(
            select(Escalation)
            .where(
                Escalation.task_id == task_id,
                Escalation.kind == BUDGET_ESCALATION_KIND,
                Escalation.status == EscalationStatus.APPROVED,
            )
            .order_by(Escalation.decided_at.desc())
        ).first()
    return _naive(latest.decided_at) if latest and latest.decided_at else None


def _budget_window_start(task_id: str, task: Task) -> datetime:
    """Where the current budget window begins: the later of this turn's start
    and the last budget approval.

    Kept separate from _turn_start, which route_entry uses to decide
    resume-vs-new-turn. Advancing _turn_start itself would make a resumed
    task look like it had done no work this turn, sending it back through
    triage -- exactly what route_entry documents must never happen to a
    resume."""
    turn_start = _naive(_turn_start(task_id, task.created_at))
    resume_at = _budget_resume_at(task_id)
    return max(turn_start, resume_at) if resume_at else turn_start


def _gather_conversation_history(task_id: str, task: Task) -> str:
    """Reconstructs the full back-and-forth for a continued task: the
    original request, each turn's final answer (one 'synthesize' TraceSpan
    per completed turn), and each user follow-up (TaskMessage), interleaved
    in the order they actually happened. Empty string on a task's first
    turn -- {request} alone already covers that case, so the prompt stays
    identical to before this feature existed."""
    with get_session() as session:
        messages = session.exec(
            select(TaskMessage).where(TaskMessage.task_id == task_id).order_by(TaskMessage.created_at)
        ).all()
        if not messages:
            return ""
        # quick_reply answers count as answers. Without them here, a turn
        # answered on the fast path would vanish from the next turn's history
        # and the agent would re-answer a question it had already answered.
        synth_spans = session.exec(
            select(TraceSpan)
            .where(
                TraceSpan.task_id == task_id,
                TraceSpan.span_type.in_(("synthesize", "quick_reply")),
            )
            .order_by(TraceSpan.started_at)
        ).all()

    turns: list[tuple[datetime, str]] = [(task.created_at, f"User (original request): {task.request_text}")]
    for s in synth_spans:
        answer = (s.output or {}).get("final_answer")
        if answer:
            turns.append((s.started_at, f"Assistant (final answer): {answer}"))
    for m in messages:
        turns.append((m.created_at, f"User (follow-up): {m.content}"))
    turns.sort(key=lambda t: t[0])

    return (
        "This is a continued conversation -- here is everything said so far, in order "
        "(the original request above is repeated as the first line for context):\n"
        + "\n".join(text for _, text in turns)
        + "\n"
    )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def sketch_node(state: AgentState) -> AgentState:
    """Supervisor: produce a rough, non-binding outline of likely steps --
    advisory context for agent_step_node, not a committed plan. Creates no
    Subtask rows and no dependency graph; the loop is free to ignore,
    extend, reorder, or abandon anything here based on what it actually
    learns. Persisted only via the trace span below (see _load_sketch) --
    no DB column, no migration."""
    task_id = state["task_id"]
    _check_cancelled(task_id)
    with get_session() as session:
        task = session.get(Task, task_id)
        request_text = task.request_text
        owner_id = task.owner_id

    memories = long_term.retrieve_relevant(request_text, owner_id=owner_id, k=3)
    memory_context = (
        "\n".join(f"- [{m.kind}] {m.content}" for m in memories)
        if memories
        else "(no relevant past memories)"
    )

    prompt = SKETCH_PROMPT.format(
        tool_descriptions=_tool_descriptions(), memory_context=memory_context, request=request_text
    )

    with span(task_id, "sketch", "supervisor_sketch", input={"request": request_text}) as s:
        sketch, completion = structured_complete(prompt, SketchOutput, model=settings.llm_model)
        s["output"] = sketch.model_dump()
    cost.record_llm_call(task_id, None, "sketch", completion)

    with get_session() as session:
        task = session.get(Task, task_id)
        _set_task_status(session, task, TaskStatus.RUNNING)
        session.commit()

    if sketch.confidence < settings.plan_confidence_escalation_threshold:
        _create_escalation(
            task_id, None, f"Low sketch confidence ({sketch.confidence}/5): {sketch.reasoning}", kind="plan"
        )
        return {"task_id": task_id, "route": "escalate"}

    return {"task_id": task_id, "route": "agent_step"}


def _load_sketch(task_id: str) -> str:
    """The sketch is advisory-only and deliberately NOT a DB column -- it's
    persisted purely as a TraceSpan (see sketch_node), so this reloads it on
    every agent_step_node call, including after a resume from a brand new
    graph.invoke() where nothing survives in AgentState across the process
    boundary."""
    with get_session() as session:
        span_row = session.exec(
            select(TraceSpan)
            .where(TraceSpan.task_id == task_id, TraceSpan.span_type == "sketch")
            .order_by(TraceSpan.started_at.desc())
        ).first()
    if not span_row or not span_row.output:
        return "(no sketch available)"
    outline = span_row.output.get("outline", [])
    return "\n".join(f"- {step}" for step in outline) if outline else "(no sketch available)"


def _load_current_plan(task_id: str) -> str:
    """The living to-do list. Reads the most recent 'plan' TraceSpan the agent
    wrote via updated_plan; before it has revised anything, falls back to the
    initial sketch outline as the plan's seed -- so the loop always has a plan
    to work from and revise, and (like the sketch) it survives a fresh
    graph.invoke() on resume with no DB column or migration, since it's just
    the latest trace row."""
    with get_session() as session:
        span_row = session.exec(
            select(TraceSpan)
            .where(TraceSpan.task_id == task_id, TraceSpan.span_type == "plan")
            .order_by(TraceSpan.started_at.desc())
        ).first()
    if span_row and span_row.output.get("plan"):
        return "\n".join(f"- {step}" for step in span_row.output["plan"])
    return _load_sketch(task_id)


def _save_plan(task_id: str, plan: list[str], steps_taken: int) -> None:
    """Persists an agent-revised plan as a 'plan' TraceSpan. Not rendered in
    the feed (buildFeed ignores unknown span types) -- the dashboard reads the
    latest one for the right-hand 'Plan' panel, so the human sees the to-do
    list update in real time."""
    with span(task_id, "plan", "agent_update_plan", input={"steps_taken": steps_taken}) as s:
        s["output"] = {"plan": plan}


def _is_artifact_read(tool_name: str, kwargs: dict) -> bool:
    """Is this call following a spill pointer? Matched on the artifact
    directory rather than "any file_io read", so reading a normal workspace
    file still gets the usual size discipline."""
    if tool_name != "file_io" or kwargs.get("action") != "read":
        return False
    return str(kwargs.get("path", "")).replace("\\", "/").startswith(f"{artifacts.ARTIFACT_DIRNAME}/")


def _awaiting_human(output_text: str) -> dict | None:
    """Did the tool hand back something only a person can act on?

    Read off the serialized result rather than passed out of band, because
    _run_tool_call's contract is (success, text) and widening it for one
    tool would touch every call site. Malformed input returns None -- a
    tool that garbles this should not be able to wedge a task."""
    if "_awaiting_human" not in (output_text or ""):
        return None
    try:
        parsed = json.loads(output_text)
    except (TypeError, ValueError):
        return None
    awaiting = parsed.get("_awaiting_human") if isinstance(parsed, dict) else None
    return awaiting if isinstance(awaiting, dict) else None


def _capped(text: str) -> str:
    """A hard ceiling for the one path that skips spilling. Generous enough
    that a real document arrives whole, small enough that a pathological file
    can't blow the context window."""
    limit = settings.max_dereference_chars
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f'… [truncated: {len(text)} chars total, showing the first {limit}. '
        f"This is the full stored result; there is no further pointer to follow.]"
    )


def _run_tool_call(task_id: str, subtask_id: str, tool_name: str, kwargs: dict) -> tuple[bool, str]:
    """Actually invokes a tool and records the ToolCall row + tool_call
    TraceSpan. Returns (tool_success, output_text) for the caller to fold
    into the subtask's output. Shared by _execute_subtask's normal path and
    run_gated_tool_call's post-approval resume path -- same recording,
    scrubbing, spillover, and dead-call bookkeeping either way, so a gated
    call that gets approved looks identical in the trace to one that never
    needed gating."""
    registry = get_registry()
    start = datetime.now(timezone.utc)
    with span(task_id, "tool_call", tool_name, subtask_id=subtask_id, input=kwargs) as s:
        try:
            result = registry.get(tool_name).run(**kwargs)
        except TypeError as exc:
            # A backstop now, not the primary guard: every proposed call is
            # validated against the tool's args_model before reaching here
            # (see validated_kwargs), so a plain kwarg mismatch can no longer
            # get this far. What still can is a **kwargs tool whose contract
            # isn't a local pydantic model -- an MCP tool trusting a third
            # party's schema, or generate_tweet's deliberate extra="allow".
            # Surface it as a failed ToolResult so the reviewer can
            # reject-and-retry, instead of crashing the graph.
            result = ToolResult(success=False, error=f"invalid arguments for {tool_name}: {exc}")
        # A tool result can carry a NUL byte from a scraped page or a read
        # file; scrub before any of it reaches a JSONB/text column below.
        result.output = scrub_nul(result.output)
        if result.error:
            result.error = scrub_nul(result.error)
        s["output"] = result.model_dump()
        s["status"] = "ok" if result.success else "error"
    latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

    with get_session() as session:
        session.add(
            ToolCall(
                subtask_id=subtask_id,
                tool_name=tool_name,
                input=kwargs,
                output=result.output if result.success else {"error": result.error},
                success=result.success,
                latency_ms=latency_ms,
            )
        )
        session.commit()

    if not result.success:
        # Remembered for the rest of this task so a later step can't
        # re-propose the identical call -- see deadcalls.py.
        deadcalls.record_failure(task_id, tool_name, kwargs)
        return False, f"Tool call failed: {result.error}"

    output_text = json.dumps(result.output)
    if _is_artifact_read(tool_name, kwargs):
        # THE DEREFERENCE PATH IS EXEMPT FROM SPILLING.
        #
        # Spilling parks an oversized result in a file and hands the model a
        # pointer, on the promise that it can read the file back when the
        # preview isn't enough (see artifacts.py). But the read is itself a
        # tool call, so it was spilled too -- the escape hatch sat behind the
        # very door it exists to open, and following a pointer could never
        # succeed for anything above the threshold.
        #
        # Observed: a 28KB Drive file spilled, then re-read and re-spilled 13
        # times, growing to 2.6MB, until the step budget ran out. Drive worked
        # perfectly; the pointer was simply un-followable.
        #
        # Reading the whole thing is the POINT here -- artifacts.py calls it
        # "one deliberate step rather than a permanent tax on every step", and
        # _gather_prior_context truncates it back down on later steps. The cap
        # below only stops a pathological file from blowing the context window.
        return True, _capped(output_text)
    if len(output_text) > settings.max_tool_output_chars:
        # Spill the payload to the workspace and keep only a preview +
        # pointer in what goes back into the prompt. The ToolCall row above
        # already holds the FULL output, so this shrinks context without
        # touching the audit trail.
        output_text = json.dumps(
            artifacts.spill(task_id, subtask_id, tool_name, output_text)
        )

    return True, output_text


def _approval_refusal(context: dict, proposed_at: datetime) -> str | None:
    """Why this approved call must NOT run after all, or None to proceed.

    Three checks, all of which must pass before an approved tool executes.
    They exist because "a human clicked approve" and "this exact call is still
    the one they approved, and it is still safe to make" are different claims:

    1. EXPIRY. An approval granted against a proposal the agent made hours ago
       is an approval of arguments chosen for a world that has moved on -- the
       architecture audit's stale-approval risk (5.2). Measured from when the
       agent proposed, not from when the human decided.

    2. THE SNAPSHOT STILL MATCHES. The fingerprint written at gate time is
       recomputed from the stored kwargs. This is what makes "ask approval for
       send_email(to=A), resume with send_email(to=B)" a refusal rather than a
       silent substitution. Nothing in the API mutates Escalation.context
       today, so this guards against a future code path -- an approver UI that
       lets a human tweak a field, a resume that re-derives kwargs from the
       model -- changing the call without re-evaluating policy. Both halves
       live in the same row, so it is drift resistance, not tamper-proofing.

    3. POLICY STILL DOESN'T DENY IT. Re-evaluated against the current rules,
       because a rule may have changed (a tool reclassified, a deploy) between
       proposal and approval. A REQUIRE_APPROVAL result is expected and fine
       -- that is why we are here, and the human just supplied it. A DENY
       means no approval is sufficient, so the human's cannot be either.

    A refusal is not a dead end: the subtask fails with this reason, the agent
    loop reads it, re-proposes, and a fresh policy evaluation raises a fresh
    approval request. The one thing that must never happen is executing.
    """
    tool_name = context.get("tool_name")
    kwargs = context.get("kwargs", {})

    # _naive on both sides: Escalation.created_at comes back from Postgres as
    # naive-UTC and datetime.now() is aware, and subtracting the two raises.
    age = (_naive(datetime.now(timezone.utc)) - _naive(proposed_at)).total_seconds()
    if age > settings.approval_expiry_seconds:
        return (
            f"Approval expired: this call was proposed {int(age // 60)} minutes ago and "
            f"approvals are only executable for {settings.approval_expiry_seconds // 60} "
            "minutes. Nothing ran. Propose the step again if it is still what you need -- "
            "it will be re-checked against the current situation and re-approved."
        )

    expected = context.get("args_fingerprint")
    if expected and expected != policy.args_fingerprint(tool_name, kwargs):
        return (
            "Approval refused: the arguments recorded for this call no longer match the "
            "ones the approval was granted for. Nothing ran. A changed call needs its own "
            "approval."
        )

    try:
        tool = get_registry().get(tool_name)
    except KeyError as exc:
        # The tool went away between proposal and approval (a config change, a
        # dropped MCP server). Fail closed: an approval for a tool we can no
        # longer classify is an approval we cannot honour.
        return f"Approval refused: {exc}. Nothing ran."

    decision = policy.decide(tool, kwargs, user_id=_task_owner_id(context.get("task_id") or ""))
    if decision.decision is PolicyDecisionType.DENY:
        return (
            f"Approval refused: policy now denies this call ({decision.reason}). Nothing ran. "
            "A denied action cannot be unlocked by approving it."
        )
    return None


def run_gated_tool_call(
    task_id: str, subtask_id: str, context: dict, proposed_at: datetime
) -> tuple[bool, str]:
    """Entry point for escalations.apply_escalation_decision's approve path
    on a tool_approval escalation -- actually executes the tool call that
    was held at the gate in _execute_subtask below, using the exact
    tool_name/kwargs stored in Escalation.context (see the gate's
    _create_escalation call, and policy.snapshot for what that dict holds).

    Not re-validated here, deliberately: these kwargs are the ones that came
    out of validated_kwargs before the gate, complete with injected plumbing,
    and a second pass would now reject task_id/user_id as unknown fields. The
    stored dict is a validated call, and what a human approved is that exact
    call -- re-deriving it would be approving one thing and running another.

    It is however re-CHECKED, which is a different thing: see
    _approval_refusal above for the three conditions an approval has to still
    satisfy at the moment it is cashed in."""
    tool_name = context.get("tool_name")
    kwargs = context.get("kwargs", {})

    refusal = _approval_refusal({**context, "task_id": task_id}, proposed_at)
    if refusal:
        # Recorded on the audit chain as its own event: "a human approved this
        # and it still did not run" is exactly the thing an incident review
        # asks about, and it must not look like an ordinary tool failure.
        audit.record(
            audit.Action.TOOL_APPROVAL_STALE,
            actor_label="policy",
            target_type="task",
            target_id=task_id,
            outcome="denied",
            meta={"tool_name": tool_name, "subtask_id": subtask_id, "reason": refusal},
        )
        with span(
            task_id, "tool_call", tool_name or "unknown", subtask_id=subtask_id,
            input={"approved_call": kwargs},
        ) as s:
            s["output"] = {"refused": refusal}
            s["status"] = "error"
        return False, refusal

    return _run_tool_call(task_id, subtask_id, tool_name, kwargs)


def _execute_subtask(
    task_id: str, subtask_id: str, preselected_choice: ToolChoice | None = None
) -> bool | None:
    """Specialist: pick a tool (or pure reasoning) and complete the subtask.
    Returns tool_success for the immediately-following review step, or None
    if the chosen tool needed human approval and got gated instead of run
    (see the policy gate below) -- the caller (agent_step_node)
    must treat None as "stop, do not call _review_subtask, this turn ends
    in an escalation" rather than a falsy tool_success. Kept as a plain
    function (not a graph node) so agent_step_node can call it inside its
    own retry loop without going through a LangGraph edge.

    preselected_choice lets the caller skip a redundant tool-selection LLM
    call on a fresh subtask's first attempt -- agent_step_node's own single
    decide-next-step call already chose the tool, and reusing that choice is
    the entire point of collapsing step-authoring and tool-selection into
    one call. It's only honored on attempt 1; a retry (attempt_count > 1)
    always re-asks TOOL_SELECTION_PROMPT with the reviewer's feedback in
    hand, since the preselected choice already failed review."""
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        subtask.attempt_count += 1
        session.add(subtask)
        session.commit()
        description = subtask.description
        attempt_count = subtask.attempt_count

    registry = get_registry()
    prior_context = _gather_prior_context(task_id, subtask_id)
    revision_feedback = _revision_feedback(subtask_id, attempt_count)

    if preselected_choice is not None and attempt_count == 1:
        choice = preselected_choice
        with span(
            task_id, "tool_selection", "specialist_choose_tool", subtask_id=subtask_id,
            input={"description": description, "source": "agent_step_decision"},
        ) as s:
            s["output"] = choice.model_dump()
    else:
        with span(
            task_id, "tool_selection", "specialist_choose_tool", subtask_id=subtask_id,
            input={"description": description},
        ) as s:
            prompt = TOOL_SELECTION_PROMPT.format(
                subtask_description=description,
                tool_descriptions=_tool_descriptions(),
                prior_context=prior_context,
                revision_feedback=revision_feedback,
            )
            choice, completion = structured_complete(prompt, ToolChoice, model=settings.llm_model)
            s["output"] = choice.model_dump()
        cost.record_llm_call(task_id, subtask_id, "tool_selection", completion)

    tool_success = True
    tool = registry.get(choice.tool_name) if choice.tool_name in registry.names() else None
    if choice.tool_name and choice.tool_name != "none" and tool is None:
        # UNKNOWN TOOL. Kept distinct from a validation failure and from a
        # runtime failure: the fix is a different tool_name, not different
        # arguments and not a different approach. It used to fall through to
        # the reasoning branch below, so a step that had explicitly asked for
        # a tool was quietly answered out of the model's own head instead.
        tool_success = False
        output_text = _rejected_call(
            task_id, subtask_id, choice.tool_name, choice.tool_input_json,
            f"unknown tool '{choice.tool_name}'. Available tools: {', '.join(registry.names())}",
        )
    elif tool is not None:
        try:
            kwargs = validated_kwargs(
                tool,
                choice.tool_input_json,
                # goal is the model's to choose but has a sensible runtime
                # fallback, so it goes in BEFORE validation where a proposed
                # value still wins -- unlike the injected plumbing below.
                defaults={"goal": description} if choice.tool_name == "delegate_subagent" else None,
                injected=_injected_kwargs(choice.tool_name, task_id, subtask_id),
            )
        except ToolValidationError as exc:
            # MALFORMED JSON or SCHEMA FAILURE -- the exception message says
            # which, and names the field. Nothing ran. This replaces
            # `except JSONDecodeError: kwargs = {}`, which did not reject a
            # bad proposal at all: it called the tool with no arguments and
            # let every default stand in for whatever the model meant.
            tool_success = False
            output_text = _rejected_call(
                task_id, subtask_id, choice.tool_name, choice.tool_input_json, str(exc)
            )
        else:
            # THE POLICY BOUNDARY. Everything above this line is the model's
            # proposal; nothing below it is the model's decision. Deterministic
            # code decides whether this call runs, needs a human, or is refused
            # -- see policy.py. It replaces `tool.needs_approval(kwargs)`, a
            # bool on a Python class that two of fifteen tools set.
            #
            # It receives VALIDATED kwargs, never the raw tool_input_json: the
            # validated_kwargs call above either produced them or raised, so a
            # malformed proposal never reaches a policy rule that might read a
            # field it does not have.
            decision = policy.decide(tool, kwargs, user_id=_task_owner_id(task_id))

            if decision.decision is PolicyDecisionType.DENY:
                # Refused outright, and the tool is never called. Recorded the
                # same way a validation refusal is (a tool_call span with
                # status=error, an unproductive step, an observation the loop
                # can reason about) because from the agent's side they are the
                # same event: nothing ran, and repeating it will not help.
                _record_policy_decision(task_id, subtask_id, choice.tool_name, kwargs, decision)
                tool_success = False
                output_text = _rejected_call(
                    task_id, subtask_id, choice.tool_name, choice.tool_input_json,
                    f"refused by policy: {decision.reason}",
                )

            elif decision.decision is PolicyDecisionType.REQUIRE_APPROVAL:
                # Gate BEFORE the tool ever runs -- unlike the other three
                # escalation kinds (plan/review/budget), which all react to an
                # outcome that already happened, this one heads it off. See
                # run_gated_tool_call below for what "approve" actually does on
                # resume, and escalations.apply_escalation_decision for why that
                # can't just be "mark this subtask DONE" like the others.
                _record_policy_decision(task_id, subtask_id, choice.tool_name, kwargs, decision)
                with get_session() as session:
                    subtask = session.get(Subtask, subtask_id)
                    subtask.assigned_tool = choice.tool_name
                    subtask.status = SubtaskStatus.ESCALATED
                    subtask.output = f"Awaiting human approval to run '{choice.tool_name}'."
                    session.add(subtask)
                    session.commit()
                _create_escalation(
                    task_id, subtask_id,
                    f"Tool '{choice.tool_name}' requires approval before it runs "
                    f"({decision.reason}). Proposed call: {json.dumps(kwargs)}",
                    kind="tool_approval",
                    # The approval snapshot: tool name, the exact validated
                    # kwargs, the decision that asked for approval, and a
                    # fingerprint the resume path re-checks. Stored in the
                    # JSONB column that already carried tool_name and kwargs,
                    # so this needs no migration and no new table.
                    context=policy.snapshot(choice.tool_name, kwargs, decision),
                )
                return None  # sentinel: gated, agent_step_node must not call _review_subtask

            elif deadcalls.is_dead(task_id, choice.tool_name, kwargs):
                # This exact call already failed earlier in this task. Running it
                # again costs a real API call to get the identical error -- on the
                # run that motivated this, the same unreadable .docx and .zip were
                # each retried a second time, four wasted steps out of eleven.
                tool_success = False
                output_text = (
                    f"Tool call skipped: this exact {choice.tool_name} call already failed earlier in "
                    f"this task and was not retried. Try a different approach or a different input -- "
                    f"repeating it will not produce a different result."
                )
            else:
                tool_success, output_text = _run_tool_call(task_id, subtask_id, choice.tool_name, kwargs)

                # A tool can ask for a human MID-CALL, which the approval gate
                # above cannot express: that one pauses BEFORE running, on the
                # question "may I?". This one pauses AFTER, because the tool has
                # produced something only a person can act on -- a picker URL, a
                # consent link, a device code. Generic on purpose; Google Photos
                # is just the first caller.
                awaiting = _awaiting_human(output_text)
                if awaiting:
                    with get_session() as session:
                        subtask = session.get(Subtask, subtask_id)
                        subtask.assigned_tool = choice.tool_name
                        subtask.status = SubtaskStatus.ESCALATED
                        subtask.output = awaiting.get("reason") or "Waiting on a person."
                        session.add(subtask)
                        session.commit()
                    _create_escalation(
                        task_id, subtask_id, awaiting.get("reason") or "This step needs you.",
                        kind=awaiting.get("kind", "human_action"),
                        context=awaiting.get("context", {}),
                    )
                    # Same sentinel as the approval gate: nothing to review, and
                    # approving re-runs the tool, which is why a tool using this
                    # must be idempotent across the pause.
                    return None

            # A step is unproductive when it errored OR when it succeeded at
            # something already done. The second half used to be invisible: one
            # real task made 13 successful, near-identical file_io reads and only
            # the step budget stopped it, because every guard keyed on failure.
            #
            # Skipped on a policy DENY: nothing ran, so there is no success to
            # record, and _rejected_call already marked the step unproductive.
            # Running this as well would count one refused call twice against
            # settings.max_unproductive_steps.
            if decision.decision is not PolicyDecisionType.DENY:
                repeated = tool_success and deadcalls.record_success(task_id, choice.tool_name, kwargs)
                if repeated:
                    output_text = (
                        f"{output_text}\n\n[Note: this exact {choice.tool_name} call was already made "
                        f"earlier in this task and returned the same result. Use what you already have "
                        f"rather than fetching it again.]"
                    )
                _record_step_productivity(task_id, productive=tool_success and not repeated)
    else:
        with span(task_id, "reasoning", "specialist_reason", subtask_id=subtask_id, input={"description": description}) as s:
            prompt = REASONING_ONLY_PROMPT.format(
                subtask_description=description,
                prior_context=prior_context,
                revision_feedback=revision_feedback,
            )
            output_text, completion = complete(prompt)
            s["output"] = {"text": output_text}
        cost.record_llm_call(task_id, subtask_id, "reasoning", completion)
        # A reasoning step that produced output is progress -- the streak
        # tracks tool-call dead ends, not "didn't call a tool".
        _record_step_productivity(task_id, productive=True)

    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        subtask.output = output_text
        subtask.assigned_tool = choice.tool_name if choice.tool_name != "none" else None
        session.add(subtask)
        session.commit()

    return tool_success


def _review_subtask(task_id: str, subtask_id: str, tool_success: bool) -> str:
    """Reviewer: validate the specialist's output. Returns the next step for
    agent_step_node's own retry loop to act on: "agent_step" (pass, this
    step is done -- the loop moves on to its next decide-next-step call),
    "execute" (reject-and-revise, under the retry cap), or "escalate"
    (reject exhausted, or the reviewer says this isn't fixable by a retry)."""
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        description, output, tool_used, attempt_count = (
            subtask.description, subtask.output, subtask.assigned_tool, subtask.attempt_count
        )

    with span(task_id, "review", "reviewer_validate", subtask_id=subtask_id, input={"output": output}) as s:
        prompt = REVIEW_PROMPT.format(
            subtask_description=description,
            tool_used=tool_used or "none",
            tool_success=tool_success,
            output=output,
        )
        review, completion = structured_complete(prompt, ReviewOutput, model=settings.reviewer_llm_model)
        s["output"] = review.model_dump()
    cost.record_llm_call(task_id, subtask_id, "review", completion)

    with get_session() as session:
        session.add(Review(subtask_id=subtask_id, score=review.score, verdict=review.verdict, feedback=review.feedback))
        session.commit()

    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        if review.verdict == "pass":
            subtask.status = SubtaskStatus.DONE
            route = "agent_step"
        elif review.verdict == "reject" and attempt_count < settings.max_subtask_retries:
            subtask.status = SubtaskStatus.NEEDS_REVISION
            route = "execute"
        else:
            subtask.status = SubtaskStatus.ESCALATED
            _create_escalation(
                task_id, subtask_id,
                f"Reviewer verdict '{review.verdict}' after {attempt_count} attempt(s): {review.feedback}",
                kind="review",
            )
            route = "escalate"
        session.add(subtask)
        session.commit()

    return route


def agent_step_node(state: AgentState) -> AgentState:
    """The continuous loop: gather everything learned so far, make ONE
    structured decision (author the next unit of work + pick its tool, or
    declare the request done), then run that unit of work to a terminal
    outcome via the same _execute_subtask/_review_subtask machinery a
    plan-time subtask used to go through. Self-loops (see graph/build.py)
    until the model finishes, escalates, or the step budget runs out.

    steps_taken is computed by counting Subtask rows in Postgres, not read
    from AgentState -- that's what makes the step budget survive a resume
    after a human escalation decision, which is a brand new graph.invoke()
    call with a fresh, empty AgentState. The step *budget* itself, however,
    is per-turn, not lifetime: steps_taken_this_turn only counts subtasks
    created since the current turn started (the last TaskMessage follow-up,
    or task creation if there's never been one -- see _turn_start), so a
    task that already used most of its budget on the original request isn't
    penalized when the user continues the conversation afterward. The
    "did the model try to finish having done nothing at all" guard below
    stays lifetime-based on purpose: on a follow-up turn, finishing with
    zero *new* steps can be entirely correct (the existing subtask outputs
    already answer the follow-up) -- it's only suspicious on a task's very
    first decision, when nothing has ever been done."""
    task_id = state["task_id"]
    _check_cancelled(task_id)

    with get_session() as session:
        task = session.get(Task, task_id)
        request_text = task.request_text
        existing = session.exec(
            select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.position)
        ).all()

    steps_taken = len(existing)
    # All three budgets below are measured over the SAME window, so a human
    # approving any one of them grants a genuine fresh allowance rather than
    # resuming into the identical guard (see _budget_window_start).
    window_start = _budget_window_start(task_id, task)
    steps_taken_this_turn = sum(1 for s in existing if _naive(s.created_at) >= window_start)
    if steps_taken_this_turn >= settings.max_task_steps:
        _create_escalation(
            task_id, None,
            f"Exceeded max_task_steps ({settings.max_task_steps}) without the agent choosing to "
            "finish -- needs human review.",
            kind=BUDGET_ESCALATION_KIND,
        )
        return {"task_id": task_id, "route": "escalate"}

    # Stop condition three: spend. max_task_steps bounds how MANY steps run
    # and the worker's wall clock bounds how LONG, but neither bounds what
    # they cost -- a few document-sized contexts can outspend a dozen cheap
    # steps. Checked before deciding, so the ceiling is never blown by the
    # very call that discovers it.
    spent = _task_cost_usd(task_id, since=window_start)
    if spent >= settings.max_task_cost_usd:
        _create_escalation(
            task_id, None,
            f"Reached the cost ceiling for one task (${spent:.4f} of "
            f"${settings.max_task_cost_usd:.2f}) -- needs human review before spending more.",
            kind=BUDGET_ESCALATION_KIND,
        )
        return {"task_id": task_id, "route": "escalate"}

    # Stop condition four: progress. A loop that keeps failing doesn't
    # error, it just bills -- so consecutive steps that produced nothing
    # usable end the turn rather than spending the rest of the budget
    # discovering the same thing again.
    if _unproductive_streak(task_id) >= settings.max_unproductive_steps:
        _create_escalation(
            task_id, None,
            f"Stopped making progress: {settings.max_unproductive_steps} consecutive steps failed "
            "or repeated a known-dead tool call. Needs a human to redirect the approach.",
            kind=BUDGET_ESCALATION_KIND,
        )
        return {"task_id": task_id, "route": "escalate"}

    plan_text = _load_current_plan(task_id)
    prior_context = _gather_prior_context(task_id)
    conversation = _gather_conversation_history(task_id, task)

    with span(
        task_id, "agent_step", f"decide_step_{steps_taken + 1}",
        input={"steps_taken": steps_taken, "steps_taken_this_turn": steps_taken_this_turn},
    ) as s:
        prompt = AGENT_STEP_PROMPT.format(
            request=request_text,
            conversation=conversation,
            plan=plan_text,
            tool_descriptions=_tool_descriptions(),
            prior_context=prior_context,
            dead_calls=deadcalls.describe(task_id),
            steps_taken=steps_taken_this_turn,
            steps_remaining=settings.max_task_steps - steps_taken_this_turn,
        )
        decision, completion = structured_complete(prompt, NextStepDecision, model=settings.llm_model)
        s["output"] = decision.model_dump()
    cost.record_llm_call(task_id, None, "agent_step", completion)

    # Persist the living to-do list whenever the agent revised it, so the next
    # step (and the dashboard) sees the current plan rather than the stale one.
    if decision.updated_plan:
        _save_plan(task_id, decision.updated_plan, steps_taken)

    if decision.next_action == "finish":
        if steps_taken == 0 and _budget_resume_at(task_id) is None:
            # Safety net, not expected in normal operation: the model tried
            # to declare the request done without doing any work on a
            # task's very first decision. Treat this like the old
            # "stuck/inconsistent state" escalations rather than let
            # synthesize_node run with zero subtask outputs.
            #
            # Suppressed once a human has approved a budget escalation on
            # this task: steps_taken is LIFETIME, so a task that escalated
            # here and was approved would arrive back at this same guard with
            # the same zero and escalate again forever. An approval on this
            # kind means "yes, finish anyway" -- honour it.
            _create_escalation(
                task_id, None,
                "Agent chose to finish before completing any step -- needs human review.",
                kind=BUDGET_ESCALATION_KIND,
            )
            return {"task_id": task_id, "route": "escalate"}
        return {"task_id": task_id, "route": "synthesize"}

    # next_action == "act"
    with get_session() as session:
        subtask = Subtask(
            task_id=task_id,
            position=steps_taken,
            description=decision.subtask_description or "(no description provided)",
            depends_on=[],
            status=SubtaskStatus.RUNNING,
        )
        session.add(subtask)
        session.commit()
        session.refresh(subtask)
        subtask_id = subtask.id

    preselected = None
    if decision.tool_name:
        preselected = ToolChoice(
            tool_name=decision.tool_name,
            tool_input_json=decision.tool_input_json or "{}",
            rationale=decision.rationale,
        )

    route = "execute"
    while route == "execute":
        tool_success = _execute_subtask(task_id, subtask_id, preselected_choice=preselected)
        preselected = None  # only ever valid for the first attempt
        if tool_success is None:
            # Gated by a policy REQUIRE_APPROVAL -- _execute_subtask already
            # created the tool_approval escalation and set the subtask
            # ESCALATED; nothing to review, this turn just ends here.
            return {"task_id": task_id, "route": "escalate"}
        route = _review_subtask(task_id, subtask_id, tool_success)

    return {"task_id": task_id, "route": route}  # "agent_step" (pass) or "escalate"


def _classify_turn(request_text: str, conversation: str):
    """Fails open to "full". A broken classifier must cost latency, never
    capability -- the same rule tool execution follows when a tool errors, and
    the reason this is safe to have on by default."""
    try:
        return structured_complete(
            TRIAGE_PROMPT.format(
                request=request_text, conversation=conversation or "(nothing yet)"
            ),
            TriageDecision,
            model=settings.triage_model,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("triage failed open to the full path: %s", exc)
        return TriageDecision(route="full", reason=f"triage error ({type(exc).__name__})"), None


def _turn_text(task_id: str, task: Task) -> str:
    """What the user actually said this turn: the newest follow-up if there is
    one, otherwise the original request."""
    with get_session() as session:
        latest = session.exec(
            select(TaskMessage)
            .where(TaskMessage.task_id == task_id)
            .order_by(TaskMessage.created_at.desc())
        ).first()
    return latest.content if latest else task.request_text


def triage_node(state: AgentState) -> AgentState:
    """The front door. Most turns need the machine; some are just talking.

    Before this, every turn paid sketch + at least one agent_step +
    synthesize, so "thanks" cost three model calls and a row of subtasks.
    This decides once, cheaply, whether that is warranted.

    The asymmetry is deliberate and the prompt states it: routing real work to
    "quick" means the user asked for something and got chat, which is far
    worse than spending a few extra calls. Anything not obviously
    conversational goes full."""
    task_id = state["task_id"]
    _check_cancelled(task_id)

    with get_session() as session:
        task = session.get(Task, task_id)
        has_subtasks = bool(
            session.exec(select(Subtask).where(Subtask.task_id == task_id).limit(1)).first()
        )

    conversation = _gather_conversation_history(task_id, task)
    turn = _turn_text(task_id, task)

    with span(task_id, "triage", "triage_turn", input={"turn": turn}) as s:
        decision, completion = _classify_turn(turn, conversation)
        s["output"] = decision.model_dump()
    if completion is not None:
        cost.record_llm_call(task_id, None, "triage", completion)

    if decision.route == "quick":
        return {"task_id": task_id, "route": "quick_reply"}
    # Full path picks up exactly where route_entry used to send it.
    return {"task_id": task_id, "route": "agent_step" if has_subtasks else "sketch"}


def quick_reply_node(state: AgentState) -> AgentState:
    """One model call. No tools, no subtasks, no memory write.

    Nothing here can act, and the prompt forbids asserting anything not
    already in the conversation -- so the worst a misrouted turn can do is
    answer unhelpfully, never take a wrong action or invent a figure. That
    bound is what makes the fast path safe to leave on."""
    task_id = state["task_id"]

    with get_session() as session:
        task = session.get(Task, task_id)
    turn = _turn_text(task_id, task)
    conversation = _gather_conversation_history(task_id, task)

    with span(task_id, "quick_reply", "quick_reply", input={"turn": turn}) as s:
        try:
            reply, completion = complete(
                QUICK_REPLY_PROMPT.format(
                    request=turn, conversation=conversation or "(nothing yet)"
                ),
                model=settings.triage_model,
            )
            cost.record_llm_call(task_id, None, "quick_reply", completion)
        except Exception as exc:  # noqa: BLE001
            # Fails toward the machine, not toward a broken turn.
            s["output"] = {"error": str(exc)}
            s["status"] = "error"
            return {"task_id": task_id, "route": "sketch"}
        # Keyed "final_answer" so _gather_conversation_history reads a quick
        # turn exactly like a synthesized one.
        s["output"] = {"final_answer": reply}

    with get_session() as session:
        task = session.get(Task, task_id)
        task.final_output = scrub_nul(reply)
        _set_task_status(session, task, TaskStatus.COMPLETED)
        session.commit()

    return {"task_id": task_id, "route": "end"}


def escalate_node(state: AgentState) -> AgentState:
    task_id = state["task_id"]
    with get_session() as session:
        task = session.get(Task, task_id)
        _set_task_status(session, task, TaskStatus.AWAITING_APPROVAL)
        session.commit()
    return {"task_id": task_id}


def synthesize_node(state: AgentState) -> AgentState:
    """Supervisor: combine subtask outputs into the final answer, and record
    an episodic memory of how this task went for future planning."""
    task_id = state["task_id"]
    with get_session() as session:
        task = session.get(Task, task_id)
        request_text = task.request_text
        subtasks = session.exec(
            select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.position)
        ).all()

    # Spill plumbing is stripped here and only here: agent_step needs the
    # pointer to decide whether to go and fetch the rest, while synthesis
    # writes for a human who has no workspace -- see artifacts.for_synthesis.
    outputs = "\n".join(
        f"{i + 1}. {s.description}\n   -> {artifacts.for_synthesis(s.output) or '(skipped/failed)'}"
        for i, s in enumerate(subtasks)
    )
    conversation = _gather_conversation_history(task_id, task)

    with span(task_id, "synthesize", "supervisor_synthesize", input={"request": request_text}) as s:
        prompt = SYNTHESIS_PROMPT.format(request=request_text, conversation=conversation, subtask_outputs=outputs)
        final_answer, completion = complete(prompt)
        s["output"] = {"final_answer": final_answer}
    cost.record_llm_call(task_id, None, "synthesize", completion)

    with get_session() as session:
        task = session.get(Task, task_id)
        if task.status != TaskStatus.CANCELLED:
            task.final_output = final_answer
        _set_task_status(session, task, TaskStatus.COMPLETED)
        session.commit()

    _reflect_and_save_memory(task_id, request_text, subtasks, final_answer)
    short_term.clear(task_id)
    return {"task_id": task_id}


def _reflect_and_save_memory(task_id: str, request_text: str, subtasks, final_answer: str) -> None:
    """Curated long-term memory, not a firehose. The old behavior saved an
    episodic summary of EVERY completed task at a flat importance -- so the
    store filled with 'Task test completed', 'Task hello completed' noise that
    then polluted the retrieval sketch_node relies on. Instead: one reflection
    call decides whether this task produced a durable, generalizable lesson
    worth recalling on a future task, and only THAT gets saved -- with a real,
    model-assigned kind (episodic/fact/preference) and importance -- followed
    by a prune so the store can't grow unbounded. This mirrors how production
    agent memory (e.g. LangGraph's store + reflection patterns) curates rather
    than logs. Best-effort: a memory hiccup must never fail an
    already-completed task, so everything here is guarded."""
    tools_used = sorted({s.assigned_tool for s in subtasks if s.assigned_tool})
    try:
        with get_session() as session:
            owner_id = session.get(Task, task_id).owner_id

        with span(task_id, "memory", "memory_reflection", input={"request": request_text}) as s:
            reflection, completion = structured_complete(
                MEMORY_REFLECTION_PROMPT.format(
                    request=request_text, tools_used=tools_used, outcome=final_answer[:500]
                ),
                MemoryReflection,
                model=settings.llm_model,
            )
            s["output"] = reflection.model_dump()
        cost.record_llm_call(task_id, None, "reflection", completion)

        if reflection.worth_saving and reflection.content.strip():
            long_term.add_memory(
                reflection.content,
                kind=reflection.kind,
                owner_id=owner_id,
                task_id=task_id,
                importance=reflection.importance,
            )
            # Bounded growth -- nothing else calls this, so a memory that's
            # never pruned is a memory that grows forever.
            long_term.prune_low_value_memories(owner_id)
    except Exception as exc:  # noqa: BLE001 -- memory is best-effort, never fail the task
        logger.warning("memory reflection/save failed for task %s (continuing): %s", task_id, exc)


# ---------------------------------------------------------------------------
# Conditional-edge routers
# ---------------------------------------------------------------------------


def route_entry(state: AgentState) -> str:
    """Where a graph invocation starts.

    The distinction that matters is RESUME vs NEW TURN:

      resume     work already happened this turn (mid-loop, or a human just
                 decided an escalation) -> straight back into the loop. Never
                 triaged: the user already committed to this work, so
                 re-classifying it would add a call and could only get it
                 wrong.
      new turn   a fresh task, or a follow-up on an existing one -> the front
                 door, which decides whether this turn needs the machine.

    "This turn" is measured the way agent_step_node measures its step budget
    -- subtasks created since _turn_start -- so the two cannot disagree about
    where a turn begins."""
    task_id = state["task_id"]
    with get_session() as session:
        task = session.get(Task, task_id)
        subtasks = session.exec(select(Subtask).where(Subtask.task_id == task_id)).all()

    turn_start = _turn_start(task_id, task.created_at)
    work_this_turn = any(s.created_at >= turn_start for s in subtasks)

    if work_this_turn or task.status == TaskStatus.AWAITING_APPROVAL:
        return "agent_step"
    if not settings.enable_triage:
        return "agent_step" if subtasks else "sketch"
    return "triage"


def route_after(state: AgentState) -> str:
    return state.get("route", "escalate")  # type: ignore[return-value]
