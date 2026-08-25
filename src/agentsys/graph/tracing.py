from contextlib import contextmanager
from datetime import datetime, timezone

from agentsys import events, otel
from agentsys.db.models import TraceSpan
from agentsys.db.session import get_session
from agentsys.sanitize import scrub_nul


@contextmanager
def span(task_id: str, span_type: str, name: str, *, subtask_id: str | None = None, input: dict | None = None):
    """Every node wraps its work in this so the trace explorer and replay
    system have a complete record without each node hand-rolling span writes.

    It is also the event seam: because every node and every tool call already
    passes through here, publishing span_start/span_end at this one point
    gives a complete live stream of a run without a single line changing in
    nodes.py. See events.py. Publishing is fail-open and cannot affect the
    span row written around it."""
    record = TraceSpan(
        task_id=task_id,
        subtask_id=subtask_id,
        span_type=span_type,
        name=name,
        input=input or {},
        started_at=datetime.now(timezone.utc),
    )
    with get_session() as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        span_id = record.id

    events.publish(
        task_id,
        "span_start",
        {
            "span_id": span_id,
            "span_type": span_type,
            "name": name,
            "subtask_id": subtask_id,
            "input": events.truncate(input or {}),
        },
    )

    box = {"output": {}, "status": "ok"}
    started = datetime.now(timezone.utc)
    # Third sink on the same seam (Postgres row, event stream, OTel span).
    # A real `with` rather than manual __enter__/__exit__: this is a core
    # execution path, and hand-driving a generator context manager gets the
    # exception case subtly wrong. When export is off this is a null context
    # manager -- see otel.py.
    with otel.span(name, span_type=span_type, task_id=task_id, subtask_id=subtask_id) as otel_span:
        try:
            yield box
        except Exception as exc:
            box["status"] = "error"
            box["output"] = {"error": str(exc)}
            otel.set_error(otel_span, str(exc))
            raise
        finally:
            # A node can report failure by setting box["status"] without
            # raising (see nodes.py), so the OTel status is set from the box,
            # not only from the exception path.
            if box["status"] != "ok":
                otel.set_error(otel_span, str(box["output"].get("error", "failed")))
            _finish(span_id, task_id, span_type, name, subtask_id, box, started)


def _finish(span_id, task_id, span_type, name, subtask_id, box, started) -> None:
    """Close out the durable row and emit span_end. Split out of span() only
    so the context managers above stay readable."""
    with get_session() as session:
        record = session.get(TraceSpan, span_id)
        # Defense in depth: this JSONB write is where a stray NUL byte from
        # any source (LLM or a scraped tool result) actually reaches Postgres
        # and would crash. scrub_nul at the source (llm.py) plus here covers
        # both LLM- and tool-originated content.
        record.output = scrub_nul(box["output"])
        record.status = box["status"]
        record.ended_at = datetime.now(timezone.utc)
        session.add(record)
        session.commit()

    events.publish(
        task_id,
        "span_end",
        {
            "span_id": span_id,
            "span_type": span_type,
            "name": name,
            "subtask_id": subtask_id,
            "status": box["status"],
            "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "output": events.truncate(box["output"]),
        },
    )
