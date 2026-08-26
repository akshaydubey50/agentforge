"""The event seam — one stream of "what is happening", many subscribers.

Until now the only record of a running task was TraceSpan rows, which meant
the only way for a UI to follow along was to poll for them. Polling is why
the dashboard refetches every 1.5s and still shows a step late.

This adds the missing seam. It is deliberately ONE function wide:

    publish(task_id, kind, payload)

and it is called from exactly two places that every node already passes
through — `graph.tracing.span()` (so every node and every tool call emits,
with no changes to nodes.py) and the task status setter. That is the whole
trick: the seam goes where the existing choke point already is, rather than
being threaded through 800 lines of node code.

Subscribers today: the SSE endpoint (see events_api.py). Subscribers this
makes possible without touching the emitters again: an OTel exporter, a
webhook fan-out, a live eval harness watching a golden run.

THE FAIL-OPEN RULE:

    publishing must never break a task.

Redis being down, a serialization failure, a subscriber going away — none of
that is a reason for an agent run to die. Every publish is wrapped and
failures are logged at debug, not raised. This is telemetry; the run is the
product. (The durable record is still the TraceSpan row written next to it,
which is unaffected by anything happening here.)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from agentsys.memory.short_term import get_redis
from agentsys.sanitize import scrub_nul

logger = logging.getLogger(__name__)

# Pub/sub only -- no history is kept here. A client that connects mid-run
# gets its backfill from Postgres (the spans it already writes), not from a
# replayed buffer, so there is exactly one source of truth for "what
# happened" and this stream is only ever "what is happening NOW".
def channel(task_id: str) -> str:
    return f"task:{task_id}:events"


def publish(task_id: str, kind: str, payload: dict[str, Any] | None = None) -> None:
    """Emit one event. Never raises -- see the fail-open rule above."""
    try:
        event = {
            "kind": kind,
            "task_id": task_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            **(payload or {}),
        }
        # Same NUL discipline as everywhere else a model- or tool-originated
        # string crosses a boundary: a raw NUL breaks the JSON consumer just
        # as reliably as it breaks a Postgres write.
        get_redis().publish(channel(task_id), json.dumps(scrub_nul(event), default=str))
    except Exception as exc:  # noqa: BLE001 -- telemetry must not fail a run
        logger.debug("event publish failed (%s): %s", kind, exc)


def truncate(value: Any, limit: int = 600) -> Any:
    """Events ride a pub/sub channel and land in a browser; a spilled 28k
    tool result has no business in either. The full payload is in Postgres —
    this stream carries enough to render a live row, and the UI fetches
    detail on demand.

    Applied to the SHAPE, not just strings: a dict of three long values is
    the common case (a tool result), and stringifying the whole thing first
    would truncate away the keys that make it legible.
    """
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {k: truncate(v, limit) for k, v in list(value.items())[:20]}
    if isinstance(value, list):
        return [truncate(v, limit) for v in value[:20]]
    return value
