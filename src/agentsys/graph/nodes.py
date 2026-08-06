import json
from datetime import datetime, timezone

from sqlmodel import select

from agentsys.config import settings
from agentsys.db.models import (
    Escalation,
    Review,
    Subtask,
    SubtaskStatus,
    Task,
    TaskStatus,
    ToolCall,
)
from agentsys.db.session import get_session
from agentsys.graph.prompts import (
    PLAN_PROMPT,
    REASONING_ONLY_PROMPT,
    REVIEW_PROMPT,
    SYNTHESIS_PROMPT,
    TOOL_SELECTION_PROMPT,
)
from agentsys.graph.schemas import PlanOutput, ReviewOutput, ToolChoice
from agentsys.graph.state import AgentState
from agentsys.graph.tracing import span
from agentsys.llm import complete, get_client
from agentsys.memory import long_term, short_term
from agentsys.tools.base import ToolResult
from agentsys.tools.registry import get_registry


def _tool_descriptions() -> str:
    tools = get_registry().list_tools()
    return "\n".join(f"- {t['name']}: {t['description']}" for t in tools) or "(no tools available)"


def _create_escalation(task_id: str, subtask_id: str | None, reason: str) -> None:
    with get_session() as session:
        session.add(Escalation(task_id=task_id, subtask_id=subtask_id, reason=reason, context={}))
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


def _gather_prior_context(task_id: str, current_subtask_id: str) -> str:
    with get_session() as session:
        done = session.exec(
            select(Subtask)
            .where(Subtask.task_id == task_id, Subtask.status == SubtaskStatus.DONE)
            .order_by(Subtask.position)
        ).all()
    lines = [f"- {s.description}: {s.output}" for s in done if s.id != current_subtask_id]

    notes = short_term.get_value(task_id, "scratchpad_notes") or []
    if notes:
        lines.append("Scratchpad notes from earlier tool calls this task:")
        lines.extend(f"  * {n}" for n in notes)

    return "\n".join(lines) if lines else "(no prior context yet)"


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def plan_node(state: AgentState) -> AgentState:
    """Supervisor: decompose the request into a dependency-ordered plan."""
    task_id = state["task_id"]
    with get_session() as session:
        task = session.get(Task, task_id)
        request_text = task.request_text

    memories = long_term.retrieve_relevant(request_text, k=3)
    memory_context = (
        "\n".join(f"- [{m.kind}] {m.content}" for m in memories)
        if memories
        else "(no relevant past memories)"
    )

    prompt = PLAN_PROMPT.format(
        tool_descriptions=_tool_descriptions(), memory_context=memory_context, request=request_text
    )

    with span(task_id, "plan", "supervisor_decompose", input={"request": request_text}) as s:
        completion = get_client().beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format=PlanOutput,
        )
        plan = completion.choices[0].message.parsed
        s["output"] = plan.model_dump()

    built = [
        Subtask(task_id=task_id, position=i, description=spec.description, depends_on=[])
        for i, spec in enumerate(plan.subtasks)
    ]
    position_to_id = {i: s.id for i, s in enumerate(built)}
    for i, (subtask, spec) in enumerate(zip(built, plan.subtasks)):
        # Only positions strictly before this one are eligible — prevents
        # cycles and self-reference by construction rather than by detection.
        valid = [p for p in spec.depends_on_positions if p in position_to_id and p < i]
        subtask.depends_on = [position_to_id[p] for p in valid]

    with get_session() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.RUNNING
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        for subtask in built:
            session.add(subtask)
        session.commit()

    if plan.confidence < settings.plan_confidence_escalation_threshold:
        _create_escalation(
            task_id, None, f"Low plan confidence ({plan.confidence}/5): {plan.reasoning}"
        )
        return {"task_id": task_id, "route": "escalate"}

    return {"task_id": task_id, "route": "select_subtask"}


def select_subtask_node(state: AgentState) -> AgentState:
    """Picks the next subtask whose dependencies are satisfied, or routes to
    synthesis (all done) / escalation (stuck) if there's nothing runnable."""
    task_id = state["task_id"]
    with get_session() as session:
        subtasks = session.exec(select(Subtask).where(Subtask.task_id == task_id)).all()

    terminal = {SubtaskStatus.DONE, SubtaskStatus.SKIPPED, SubtaskStatus.FAILED}
    done_ids = {s.id for s in subtasks if s.status in (SubtaskStatus.DONE, SubtaskStatus.SKIPPED)}
    runnable_statuses = {SubtaskStatus.PENDING, SubtaskStatus.NEEDS_REVISION}
    pending = [s for s in subtasks if s.status in runnable_statuses]

    if not pending:
        if all(s.status in terminal for s in subtasks):
            return {"task_id": task_id, "route": "synthesize"}
        _create_escalation(task_id, None, "No pending subtasks but not all are terminal — inconsistent state.")
        return {"task_id": task_id, "route": "escalate"}

    ready = [s for s in pending if all(dep in done_ids for dep in s.depends_on)]
    if not ready:
        _create_escalation(
            task_id, None, "No subtask is ready to run (unresolved dependencies) — needs human review."
        )
        return {"task_id": task_id, "route": "escalate"}

    next_subtask = ready[0]
    with get_session() as session:
        subtask = session.get(Subtask, next_subtask.id)
        subtask.status = SubtaskStatus.RUNNING
        session.add(subtask)
        session.commit()

    return {"task_id": task_id, "route": "execute", "current_subtask_id": next_subtask.id}


def execute_node(state: AgentState) -> AgentState:
    """Specialist: pick a tool (or pure reasoning) and complete the subtask."""
    task_id = state["task_id"]
    subtask_id = state["current_subtask_id"]

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
        completion = get_client().beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format=ToolChoice,
        )
        choice = completion.choices[0].message.parsed
        s["output"] = choice.model_dump()

    tool_success = True
    if choice.tool_name and choice.tool_name != "none" and choice.tool_name in registry.names():
        try:
            kwargs = json.loads(choice.tool_input_json)
        except json.JSONDecodeError:
            kwargs = {}
        if choice.tool_name == "file_io":
            kwargs.setdefault("task_id", task_id)

        start = datetime.now(timezone.utc)
        with span(task_id, "tool_call", choice.tool_name, subtask_id=subtask_id, input=kwargs) as s:
            try:
                result = registry.get(choice.tool_name).run(**kwargs)
            except TypeError as exc:
                # The specialist's LLM call can construct arguments that don't
                # match the tool's actual signature (wrong/missing kwarg name).
                # That's a fixable mistake, not a system failure — surface it
                # as a failed ToolResult so the reviewer can reject-and-retry
                # with the corrected signature, instead of crashing the graph.
                result = ToolResult(success=False, error=f"invalid arguments for {choice.tool_name}: {exc}")
            s["output"] = result.model_dump()
            s["status"] = "ok" if result.success else "error"
        latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        with get_session() as session:
            session.add(
                ToolCall(
                    subtask_id=subtask_id,
                    tool_name=choice.tool_name,
                    input=kwargs,
                    output=result.output if result.success else {"error": result.error},
                    success=result.success,
                    latency_ms=latency_ms,
                )
            )
            session.commit()

        tool_success = result.success
        output_text = json.dumps(result.output) if result.success else f"Tool call failed: {result.error}"

        if result.success:
            note = f"{choice.tool_name} on '{description[:60]}' -> {json.dumps(result.output)[:200]}"
            notes = short_term.get_value(task_id, "scratchpad_notes") or []
            notes.append(note)
            short_term.set_value(task_id, "scratchpad_notes", notes)
    else:
        with span(task_id, "reasoning", "specialist_reason", subtask_id=subtask_id, input={"description": description}) as s:
            prompt = REASONING_ONLY_PROMPT.format(
                subtask_description=description,
                prior_context=prior_context,
                revision_feedback=revision_feedback,
            )
            output_text = complete(prompt)
            s["output"] = {"text": output_text}

    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        subtask.output = output_text
        subtask.assigned_tool = choice.tool_name if choice.tool_name != "none" else None
        session.add(subtask)
        session.commit()

    return {"task_id": task_id, "current_subtask_id": subtask_id, "route": "review", "_tool_success": tool_success}  # type: ignore[typeddict-item]


def review_node(state: AgentState) -> AgentState:
    """Reviewer: validate the specialist's output. Routes to select (pass),
    back to execute (reject-and-revise, under the retry cap), or escalate."""
    task_id = state["task_id"]
    subtask_id = state["current_subtask_id"]
    tool_success = state.get("_tool_success", True)  # type: ignore[typeddict-item]

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
        completion = get_client().beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format=ReviewOutput,
        )
        review = completion.choices[0].message.parsed
        s["output"] = review.model_dump()

    with get_session() as session:
        session.add(Review(subtask_id=subtask_id, score=review.score, verdict=review.verdict, feedback=review.feedback))
        session.commit()

    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        if review.verdict == "pass":
            subtask.status = SubtaskStatus.DONE
            route = "select_subtask"
        elif review.verdict == "reject" and attempt_count < settings.max_subtask_retries:
            subtask.status = SubtaskStatus.NEEDS_REVISION
            route = "execute"
        else:
            subtask.status = SubtaskStatus.ESCALATED
            _create_escalation(
                task_id, subtask_id,
                f"Reviewer verdict '{review.verdict}' after {attempt_count} attempt(s): {review.feedback}",
            )
            route = "escalate"
        session.add(subtask)
        session.commit()

    return {"task_id": task_id, "current_subtask_id": subtask_id, "route": route}


def escalate_node(state: AgentState) -> AgentState:
    task_id = state["task_id"]
    with get_session() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.AWAITING_APPROVAL
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
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

    outputs = "\n".join(
        f"{i + 1}. {s.description}\n   -> {s.output or '(skipped/failed)'}"
        for i, s in enumerate(subtasks)
    )

    with span(task_id, "synthesize", "supervisor_synthesize", input={"request": request_text}) as s:
        prompt = SYNTHESIS_PROMPT.format(request=request_text, subtask_outputs=outputs)
        final_answer = complete(prompt)
        s["output"] = {"final_answer": final_answer}

    with get_session() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.COMPLETED
        task.final_output = final_answer
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()

    tools_used = sorted({s.assigned_tool for s in subtasks if s.assigned_tool})
    long_term.add_memory(
        f"Task '{request_text}' completed. Tools used: {tools_used}. Outcome: {final_answer[:300]}",
        kind="episodic",
        task_id=task_id,
        importance=3,
    )
    short_term.clear(task_id)
    return {"task_id": task_id}


# ---------------------------------------------------------------------------
# Conditional-edge routers
# ---------------------------------------------------------------------------


def route_entry(state: AgentState) -> str:
    """A task with no subtasks yet needs planning; a task resuming after a
    human decision already has subtasks and just needs to keep executing."""
    task_id = state["task_id"]
    with get_session() as session:
        exists = session.exec(select(Subtask).where(Subtask.task_id == task_id).limit(1)).first()
    return "select_subtask" if exists else "plan"


def route_after(state: AgentState) -> str:
    return state.get("route", "escalate")  # type: ignore[return-value]
