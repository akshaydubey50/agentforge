"""Server-sent events for one task — the read side of the event seam.

    GET /v1/tasks/{task_id}/events

Why SSE and not WebSockets: this is strictly one-directional (the server
narrates, the client listens), SSE is plain HTTP so it rides the existing
cookie auth and CORS setup with no second handshake to secure, and browsers
reconnect it automatically. A WebSocket would be more machinery for a
capability nothing here needs.

WHAT A CLIENT GETS, AND WHY IT STARTS WITH A SNAPSHOT

A live stream alone has a race: connect a moment after the worker finished
and you wait forever for an event that already happened. So the first frame
is always a `snapshot` carrying the task's CURRENT status, read from
Postgres at connect time. From there the client is caught up, and everything
after is live. The durable history still comes from /v1/tasks/{id}/trace --
this stream never replays, so there is exactly one source of truth for what
happened and no chance of the two disagreeing.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlmodel import select

from agentsys import events
from agentsys.auth import get_current_user
from agentsys.config import settings
from agentsys.db.models import Task, TaskStatus, User
from agentsys.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/tasks", tags=["events"])

# Statuses after which nothing more will be published for this task, so the
# server can close rather than hold an idle connection open forever.
# AWAITING_APPROVAL is deliberately NOT here: a human decision resumes the
# task from a fresh Celery run that publishes to this same channel, and a
# client watching an approval should see the resume without reconnecting.
_TERMINAL = {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}

_HEARTBEAT_SECONDS = 15


def _frame(kind: str, data: dict) -> str:
    """One SSE frame. The blank line at the end is the record separator and
    is not optional -- without it the browser buffers the event forever."""
    return f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/{task_id}/events")
async def task_events(task_id: str, request: Request, user: User = Depends(get_current_user)):
    """Live narration of one task. Ownership is checked once, here, before
    any subscription is opened -- the channel name is derived from a task id,
    so without this check knowing an id would be enough to watch someone
    else's run."""
    with get_session() as session:
        task = session.exec(select(Task).where(Task.id == task_id, Task.owner_id == user.id)).first()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        current_status = task.status.value

    async def stream():
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = client.pubsub()
        try:
            # Subscribe BEFORE sending the snapshot. The other order has a
            # gap: an event published between reading the status and
            # subscribing would be lost, and it is exactly the interesting
            # one (the run moving on the moment you started watching).
            await pubsub.subscribe(events.channel(task_id))
            yield _frame("snapshot", {"task_id": task_id, "status": current_status})

            if current_status in _TERMINAL:
                return

            last_beat = asyncio.get_event_loop().time()
            while True:
                if await request.is_disconnected():
                    return

                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                now = asyncio.get_event_loop().time()

                if message is None:
                    # A comment frame. Keeps proxies and load balancers from
                    # reaping an idle connection during a long LLM call,
                    # which is precisely when the user is most likely to be
                    # staring at the screen waiting.
                    if now - last_beat >= _HEARTBEAT_SECONDS:
                        last_beat = now
                        yield ": heartbeat\n\n"
                    continue

                try:
                    payload = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue  # a malformed publish must not kill the stream

                yield _frame(payload.get("kind", "event"), payload)
                last_beat = now

                if payload.get("kind") == "task_status" and payload.get("status") in _TERMINAL:
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Redis unavailable, connection reset mid-stream, etc. Tell the
            # client rather than dropping silently: it can fall back to
            # polling, which is what it did before this endpoint existed.
            logger.warning("event stream for %s ended: %s", task_id, exc)
            yield _frame("stream_error", {"task_id": task_id, "detail": str(exc)})
        finally:
            try:
                await pubsub.aclose()
            finally:
                await client.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which turns a live
            # stream into one delivery at the end -- the exact failure this
            # endpoint exists to avoid.
            "X-Accel-Buffering": "no",
        },
    )
