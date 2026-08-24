"""Per-user rate limiting on the endpoints that enqueue real agent work.

Keyed on the authenticated user, never IP -- a corporate NAT shares one IP
across many people and a mobile client changes IP mid-session, so IP-based
limits punish the wrong users and miss the right ones.

A fixed window (INCR + EXPIRE on a bucket key) rather than a sliding window
or token bucket: it's two Redis ops, needs no stored history, and the
imprecision it's known for (up to 2x the limit across a window boundary)
doesn't matter for what this is actually protecting against -- a runaway
client loop or a stuck retry burning LLM spend, not a precise fairness SLA.

Note this bounds REQUESTS, not tokens. A proper agent-backend limiter would
count tokens, since one request can be 500 or 500,000 -- but request_text is
already capped (settings.max_request_text_length, see schemas.py) and each
task carries its own step budget (settings.max_task_steps), so per-request
cost here has a real ceiling. Token-aware quotas are the next tier of this,
not solved here.
"""

from __future__ import annotations

import math
import time

from fastapi import Depends, HTTPException

from agentsys.auth import get_current_user
from agentsys.config import settings
from agentsys.db.models import User
from agentsys.memory.short_term import get_redis

_WINDOW_SECONDS = 3600


def check_rate_limit(user_id: str, bucket: str, limit: int, window_seconds: int = _WINDOW_SECONDS) -> None:
    """Raises 429 (with Retry-After, so a well-behaved client backs off
    correctly instead of hammering) once this user exceeds `limit` calls in
    the current window. Fails OPEN on a Redis error: rate limiting is a cost
    guard, not an authorization control -- auth has already run by this
    point -- so a Redis blip should degrade to "unlimited" rather than lock
    every user out of their own tasks."""
    client = get_redis()
    # Window number in the key means each window gets a fresh counter with
    # no cleanup pass -- the old key just expires on its own.
    window = math.floor(time.time() / window_seconds)
    key = f"ratelimit:{bucket}:{user_id}:{window}"

    try:
        count = client.incr(key)
        if count == 1:
            client.expire(key, window_seconds)
    except Exception:  # noqa: BLE001 -- see fail-open note above
        return

    if count > limit:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded ({limit} per hour) -- try again shortly",
            headers={"Retry-After": str(window_seconds)},
        )


def enforce_task_rate_limit(user: User = Depends(get_current_user)) -> None:
    """FastAPI dependency for the endpoints that actually enqueue agent work
    (create_task, create_task_with_files, send_task_message -- see main.py).
    Read-only endpoints are deliberately unlimited: they're cheap, and
    throttling a dashboard's polling would break the UI for no saving."""
    check_rate_limit(user.id, "task", settings.task_rate_limit_per_hour)
