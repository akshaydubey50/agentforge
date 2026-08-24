from contextlib import contextmanager
from datetime import datetime, timezone

from agentsys.db.models import TraceSpan
from agentsys.db.session import get_session
from agentsys.sanitize import scrub_nul


@contextmanager
def span(task_id: str, span_type: str, name: str, *, subtask_id: str | None = None, input: dict | None = None):
    """Every node wraps its work in this so the trace explorer and replay
    system have a complete record without each node hand-rolling span writes."""
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

    box = {"output": {}, "status": "ok"}
    try:
        yield box
    except Exception as exc:
        box["status"] = "error"
        box["output"] = {"error": str(exc)}
        raise
    finally:
        with get_session() as session:
            record = session.get(TraceSpan, span_id)
            # Defense in depth: this JSONB write is where a stray NUL byte
            # from any source (LLM or a scraped tool result) actually reaches
            # Postgres and would crash. scrub_nul at the source (llm.py) plus
            # here covers both LLM- and tool-originated content.
            record.output = scrub_nul(box["output"])
            record.status = box["status"]
            record.ended_at = datetime.now(timezone.utc)
            session.add(record)
            session.commit()
