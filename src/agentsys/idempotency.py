"""Idempotency-Key support for the endpoints that enqueue real agent work.

In an ordinary API a duplicate submit is untidy; here it costs money -- a
double-click, a client retry, or a backgrounded mobile request resubmitting
each produce a second real task with a second real LLM bill. Clients send an
opaque `Idempotency-Key` header; a repeat of the same key by the same user
returns the ORIGINAL task instead of creating another.

Keyed by (user_id, key), never key alone: two users could plausibly generate
the same client-side key, and one returning the other's task id would be a
cross-tenant leak, not just a bug.

Redis rather than Postgres because this is short-lived, self-expiring state
with no reporting value -- the same reasoning sessions (auth.py) and OAuth
CSRF state (integrations/google_oauth.py) already use it.
"""

from __future__ import annotations

from agentsys.config import settings
from agentsys.memory.short_term import get_redis


def _key(user_id: str, idempotency_key: str) -> str:
    return f"idempotency:{user_id}:{idempotency_key}"


def lookup(user_id: str, idempotency_key: str | None) -> str | None:
    """The task id this key already created, or None if it's new (or no key
    was supplied -- the header is optional, so callers without one simply
    get no idempotency protection)."""
    if not idempotency_key:
        return None
    return get_redis().get(_key(user_id, idempotency_key))


def record(user_id: str, idempotency_key: str | None, task_id: str) -> None:
    """Remember which task this key produced. Called AFTER the task row is
    committed -- recording before would risk pointing a retry at a task id
    that never actually got created."""
    if not idempotency_key:
        return
    get_redis().setex(
        _key(user_id, idempotency_key), settings.idempotency_ttl_seconds, task_id
    )
