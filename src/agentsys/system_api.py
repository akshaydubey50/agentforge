"""The System view's backend — what this system IS, as data.

Two endpoints behind one idea: the architecture diagram in the README is the
best explanation of AgentForge that exists, and until now it appeared nowhere
in the product. A hand-drawn picture in a UI rots the moment someone adds a
node, so nothing here is hand-drawn:

    /v1/system/topology   the shape of the system, read off the COMPILED graph
    /v1/system/summary    live counts for that shape, per user

THE HONESTY RULE, which is the whole reason this module is shaped this way:

    node and edge EXISTENCE comes from `build_graph().get_graph()`.
    only the human-readable gloss (label, role, one-line summary) is authored.

So a node added to graph/build.py shows up here the same day, with a fallback
label derived from its own name, rather than being silently missing from the
picture because nobody updated a second list. `_NODE_META` is a lookup that
may miss; it is never the source of what exists. The same applies downward:
the tool list is the live registry (which already degrades tools out when
Docker or Google OAuth isn't configured, see tools/registry.py), and the
guardrail values are read off `settings`, not restated.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlmodel import select

from agentsys import cost, policy
from agentsys.auth import get_current_user
from agentsys.config import settings
from agentsys.db.models import (
    Escalation,
    EscalationStatus,
    LlmCall,
    MemoryEntry,
    Subtask,
    Task,
    TaskStatus,
    ToolCall,
    TraceSpan,
    User,
)
from agentsys.db.session import get_session
from agentsys.tools.registry import get_registry

router = APIRouter(prefix="/v1/system", tags=["system"])


# The gloss for each graph node. Keys match node names in graph/build.py.
# A miss here is not an error -- see _describe_node, which falls back to the
# node's own name so a new node is visible immediately rather than invisible
# until someone remembers this dict exists.
#
# `role` maps onto the four agent-role accent colors the frontend already
# defines (--role-supervisor / -specialist / -reviewer / -human), so the
# diagram is colored by WHO acts, which is the distinction the architecture
# actually turns on.
_NODE_META: dict[str, dict] = {
    "__start__": {
        "label": "Request",
        "role": "human",
        "kind": "entry",
        "summary": "A submitted request, or a resume after a human decision.",
    },
    "triage": {
        "label": "Triage",
        "role": "reviewer",
        "kind": "llm",
        "summary": (
            "The front door. One cheap call decides whether this turn needs the machine at "
            "all. Biased hard toward the full path, and any failure falls open to it — so "
            "this can cost latency, never capability."
        ),
        "href": "/runs",
        "span_type": "triage",
    },
    "quick_reply": {
        "label": "Quick reply",
        "role": "reviewer",
        "kind": "llm",
        "summary": (
            "The fast path: one call, no tools, no subtasks. Its prompt forbids asserting "
            "anything not already in the conversation, so a misrouted turn answers "
            "unhelpfully rather than acting wrongly."
        ),
        "href": "/runs",
        "span_type": "quick_reply",
    },
    "sketch": {
        "label": "Sketch",
        "role": "supervisor",
        "kind": "llm",
        "summary": (
            "A rough, non-binding outline, informed by long-term memory of similar past "
            "tasks. Creates no committed work. Low confidence escalates instead of guessing."
        ),
        "href": "/runs",
        "span_type": "sketch",
    },
    "agent_step": {
        "label": "Agent step",
        "role": "supervisor",
        "kind": "loop",
        "summary": (
            "The continuous loop. Decides one next step in light of everything learned so "
            "far, runs it through a specialist and a reviewer, then self-loops. Ends on "
            "finish, or on one of the four stop conditions."
        ),
        "href": "/runs",
        "span_type": "agent_step",
    },
    "escalate": {
        "label": "Escalate",
        "role": "human",
        "kind": "human",
        "summary": (
            "Pauses the task and opens an approval. Resume is a brand new graph "
            "invocation reading current Postgres state, often on a different worker."
        ),
        "href": "/approvals",
        "span_type": "escalation",
    },
    "synthesize": {
        "label": "Synthesize",
        "role": "supervisor",
        "kind": "llm",
        "summary": (
            "Combines step outputs into the final answer, and reflects the run into "
            "episodic memory for future tasks."
        ),
        "href": "/runs",
        "span_type": "synthesize",
    },
    "__end__": {
        "label": "Done",
        "role": "human",
        "kind": "exit",
        "summary": "The answer, back to whoever asked.",
    },
}


def _describe_node(name: str) -> dict:
    """One node as the frontend wants it. Unknown nodes degrade to a readable
    label rather than disappearing -- see the honesty rule in the module
    docstring."""
    meta = _NODE_META.get(name, {})
    return {
        "id": name,
        "label": meta.get("label") or name.strip("_").replace("_", " ").capitalize(),
        "role": meta.get("role", "supervisor"),
        "kind": meta.get("kind", "node"),
        "summary": meta.get("summary", ""),
        "href": meta.get("href"),
        # Which TraceSpan.span_type this node writes, so the frontend can show
        # live activity on it. Two differ from the node name ("escalate" writes
        # "escalation"), which is exactly why this is declared, not inferred.
        "span_type": meta.get("span_type"),
        # START/END are structural, not work the agent does -- the frontend
        # draws them as terminals rather than as cards.
        "terminal": name in ("__start__", "__end__"),
    }


def _graph_topology() -> dict:
    """The compiled LangGraph's own nodes and edges. This is the same object
    LangGraph would draw its mermaid diagram from, so the picture the UI shows
    and the code that runs cannot disagree."""
    from agentsys.graph.build import build_graph

    drawable = build_graph().get_graph()
    return {
        "nodes": [_describe_node(name) for name in drawable.nodes],
        "edges": [
            {
                "source": edge.source,
                "target": edge.target,
                # A conditional edge is a ROUTER decision (route_entry /
                # route_after) rather than an unconditional hand-off. The
                # frontend dashes these, because "sometimes" and "always" are
                # the most important distinction on the whole diagram.
                "conditional": bool(edge.conditional),
            }
            for edge in drawable.edges
        ],
    }


def _tools() -> list[dict]:
    """The LIVE registry, not a static list. Tools degrade out of it when
    their dependency is absent (no Docker socket, no Google OAuth, an MCP
    server that's down), so this is also the honest answer to "what can the
    agent actually do right now" -- see tools/registry.py."""
    registry = get_registry()
    out = []
    for entry in registry.list_tools():
        tool = registry.get(entry["name"])
        out.append(
            {
                "name": entry["name"],
                # The first sentence only. The full description is written for
                # the model (see tools/base.py) and runs to paragraphs; the
                # diagram needs a label, and /tools shows the rest.
                "summary": entry["description"].strip().split("\n")[0][:180],
                # Policy's verdict for this tool with no specific arguments in
                # hand -- the old static Tool.requires_approval bool it replaces
                # could not express anything else anyway. A tool whose gating
                # depends on its arguments (file_io: read no, write yes) reports
                # its baseline here, which is exactly what the old bool reported
                # too. The per-call decision is the one in the audit chain.
                "requires_approval": policy.decide(tool, {}).decision
                is not policy.PolicyDecisionType.ALLOW,
                # MCPTool carries the server it came from (tools/mcp_tool.py);
                # asking the object beats pattern-matching its name, which
                # would misfile any first-party tool that happens to start
                # with the same prefix.
                "server": getattr(tool, "server_name", None),
            }
        )
    return out


def _subsystems() -> list[dict]:
    """The boxes around the graph: where state lives, what the models are,
    and what stops a runaway task. Every value is read off `settings` rather
    than restated, so this page cannot drift from the deployment it's
    describing."""
    return [
        {
            "id": "models",
            "label": "Models",
            "summary": "Routed through LiteLLM, so a provider swap is config, not code.",
            # No href: these are deployment config, set in .env and read at
            # startup. /settings is an explicitly unwired preview, so linking
            # there would imply they can be changed from the UI.
            "href": None,
            "facts": [
                {"label": "Agent", "value": settings.llm_model},
                # Called out because it is a deliberate architectural choice,
                # not a cost knob: a reviewer sharing blind spots with the
                # thing it reviews misses what an independent judge catches.
                {"label": "Reviewer", "value": settings.reviewer_llm_model, "note": "independent tier"},
            ],
        },
        {
            "id": "memory",
            "label": "Memory",
            "summary": "Two lifetimes: a per-task scratchpad, and durable episodic memory.",
            "href": "/memory",
            "facts": [
                {"label": "Short-term", "value": "Redis", "note": "cleared on completion"},
                {"label": "Long-term", "value": "Chroma + Postgres", "note": "0.7 similarity + 0.3 importance"},
            ],
        },
        {
            "id": "state",
            "label": "State",
            "summary": (
                "Postgres is the source of truth, not the graph checkpointer — which is why "
                "resuming a paused task is just calling run_task again."
            ),
            "href": "/runs",
            "facts": [
                {"label": "Store", "value": "Postgres"},
                {"label": "Queue", "value": "Celery + Redis"},
            ],
        },
        {
            "id": "knowledge",
            "label": "Knowledge",
            "summary": "The hybrid-search RAG service, reached over HTTP — the one seam between the two packages.",
            "href": "/knowledge",
            "facts": [{"label": "Service", "value": settings.rag_api_url}],
        },
        {
            "id": "guardrails",
            "label": "Stop conditions",
            "summary": "Four independent ceilings. A loop that stops making progress doesn't error, it just bills.",
            "href": None,
            "facts": [
                {"label": "Steps", "value": f"{settings.max_task_steps} per turn"},
                {"label": "Spend", "value": f"${settings.max_task_cost_usd:.2f} per task"},
                {"label": "No progress", "value": f"{settings.max_unproductive_steps} consecutive"},
                {"label": "Wall clock", "value": f"{settings.task_soft_time_limit_seconds}s soft"},
            ],
        },
    ]


@router.get("/topology")
def topology(user: User = Depends(get_current_user)) -> dict:
    """The shape of the system. Static per deployment (it describes code and
    config, not this user's data), so the frontend can cache it hard and poll
    only /summary."""
    return {
        "graph": _graph_topology(),
        "tools": _tools(),
        "subsystems": _subsystems(),
    }


@router.get("/summary")
def summary(user: User = Depends(get_current_user)) -> dict:
    """Live counts for this user, shaped for the nav badges and the overview
    tiles in one round trip.

    Deliberately all aggregate queries: this is polled, and /v1/analytics'
    fetch-every-row-then-count-in-Python approach is fine for a report page
    but wrong for something on a timer.
    """
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)

    with get_session() as session:
        owned = select(Task.id).where(Task.owner_id == user.id, Task.is_eval == False)  # noqa: E712

        status_rows = session.exec(
            select(Task.status, func.count())
            .where(Task.owner_id == user.id, Task.is_eval == False)  # noqa: E712
            .group_by(Task.status)
        ).all()
        by_status = {status.value: count for status, count in status_rows}

        pending_approvals = session.exec(
            select(func.count())
            .select_from(Escalation)
            .where(Escalation.status == EscalationStatus.PENDING, Escalation.task_id.in_(owned))
        ).one()

        memory_entries = session.exec(
            select(func.count()).select_from(MemoryEntry).where(MemoryEntry.owner_id == user.id)
        ).one()

        # Rows, not a SUM(cost_usd): spend is derived from tokens at read
        # time so a corrected rate fixes history (see cost.py / pricing.py).
        llm_rows = session.exec(select(LlmCall).where(LlmCall.task_id.in_(owned))).all()

        steps = session.exec(
            select(func.count()).select_from(Subtask).where(Subtask.task_id.in_(owned))
        ).one()

        # ToolCall has no task_id of its own -- it's owned via subtask_id ->
        # Subtask.task_id, the same extra join hop /v1/analytics documents.
        tool_calls, tool_failures = session.exec(
            select(
                func.count(ToolCall.id),
                func.coalesce(func.sum(case((ToolCall.success == False, 1), else_=0)), 0),  # noqa: E712
            )
            .join(Subtask, ToolCall.subtask_id == Subtask.id)
            .where(Subtask.task_id.in_(owned))
        ).one()

        spans_24h = session.exec(
            select(func.count())
            .select_from(TraceSpan)
            .where(TraceSpan.task_id.in_(owned), TraceSpan.started_at >= day_ago)
        ).one()

        # Per-span-type activity, so the diagram shows what has actually been
        # running rather than just what exists. Keyed by span_type (see
        # TraceSpan) -- the frontend maps node -> span_type via _SPAN_TYPE_FOR
        # below, because two of them differ by name.
        activity_rows = session.exec(
            select(
                TraceSpan.span_type,
                func.count(TraceSpan.id),
                func.coalesce(func.sum(case((TraceSpan.status != "ok", 1), else_=0)), 0),
            )
            .where(TraceSpan.task_id.in_(owned), TraceSpan.started_at >= day_ago)
            .group_by(TraceSpan.span_type)
        ).all()

    spend = cost.spend_from_rows(llm_rows)
    # by_model/by_purpose are a report-page concern; the nav badge and tiles
    # want one number, so the polled payload stays small.
    spend = {k: spend[k] for k in ("usd", "llm_calls", "tokens_in", "tokens_out", "estimated")}

    active = by_status.get(TaskStatus.RUNNING.value, 0) + by_status.get(TaskStatus.PENDING.value, 0)

    return {
        "tasks": {
            "by_status": by_status,
            "total": sum(by_status.values()),
            "active": active,
            "awaiting_approval": by_status.get(TaskStatus.AWAITING_APPROVAL.value, 0),
        },
        "approvals_pending": int(pending_approvals or 0),
        "memory_entries": int(memory_entries or 0),
        "tools_registered": len(get_registry().names()),
        "steps_run": int(steps or 0),
        "tool_calls": {"total": int(tool_calls or 0), "failed": int(tool_failures or 0)},
        "spend": spend,
        "trace_spans_24h": int(spans_24h or 0),
        "activity_24h": {
            span_type: {"runs": int(runs or 0), "errors": int(errors or 0)}
            for span_type, runs, errors in activity_rows
        },
    }
