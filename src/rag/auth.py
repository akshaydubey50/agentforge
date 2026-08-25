"""Session validation for rag's browser-facing endpoints. Deliberately a
duplicate of the cookie-check half of agentsys/auth.py, not an import --
the two packages don't import each other (see docs/MERGE.md) -- reading the
same Redis session cache that agentsys's Google sign-in flow populates (see
agentsys/auth.py's _cache_put).

The cache key is `session:{sha256(token)}`, NOT `session:{token}`. That
distinction is load-bearing and this file got it wrong once: agentsys never
stores a raw session token anywhere, so a lookup by raw token matches
nothing and every endpoint here 401s regardless of how validly the caller
is signed in. Hashing here is what keeps the two halves speaking the same
language -- and it means this service, like agentsys, never holds anything
replayable.

What this checks and what it cannot
-----------------------------------
Presence of a live cache entry, and nothing more. agentsys treats Redis as
a cache in front of Postgres (UserSession), where the real expiry policy
lives -- so the absolute-lifetime cap and the revoked_at flag are enforced
there, not here. This service has no Postgres of its own, so it inherits
those guarantees indirectly:
  - the cache entry's TTL is the IDLE timeout, so an abandoned session
    stops working here within that window even if nobody tells us;
  - agentsys deletes the cache key on revoke and on either expiry, so a
    killed session stops working here immediately.
The residual gap is a session revoked in Postgres by some path that fails
to drop the cache key; it would remain usable here until the TTL lapses.
Closing that properly means either a shared datastore or an HTTP call to
agentsys per request, both of which the package split rules out today.

Unlike agentsys, rag has no User table, so there's no per-user document
filtering here yet (see the plan's explicit scope cut -- the knowledge base
is a shared corpus, login-required but not per-user partitioned). It gates
the browser-facing document/ingest endpoints only -- /v1/ask stays open
because agentsys's knowledge_search tool calls it server-to-server, with no
browser session to present (see rag/main.py for exactly which routes use
this).
"""

from __future__ import annotations

import hashlib
import hmac

import redis
from fastapi import HTTPException, Request

from rag.config import settings

_redis: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _hash_token(token: str) -> str:
    """Must stay byte-for-byte identical to agentsys.auth.hash_token -- the
    two compute keys into the same Redis namespace, so any divergence shows
    up as a total authentication failure rather than as anything subtle."""
    return hashlib.sha256(token.encode()).hexdigest()


def require_session(request: Request) -> str:
    """FastAPI dependency -- 401s on a missing/expired session, otherwise
    returns the user_id (unused for now beyond proving *someone* is signed
    in; see module docstring)."""
    token = request.cookies.get(settings.session_cookie)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")

    user_id = _get_redis().get(f"session:{_hash_token(token)}")
    if not user_id:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    return user_id


def _presented_service_token(request: Request) -> str | None:
    """`Authorization: Bearer <token>`. Returns None rather than raising for
    anything malformed -- a caller with no bearer header is not an error, it
    is a browser, and gets tried against the session path instead."""
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def require_service_or_session(request: Request) -> str:
    """Gate for /v1/ask, which has two legitimate callers with nothing in
    common: agentsys's WORKER (server-to-server, no browser, no cookie) and,
    potentially, a signed-in human. Accepts either, rejects everything else.

    Constant-time comparison via hmac.compare_digest, not `==`: a token
    checked with ordinary string equality leaks its prefix through timing,
    and this one is a bearer secret with no expiry.

    The token is NEVER logged or echoed, including in the 401 body -- an
    error message that repeats what was presented is how a secret ends up in
    a log aggregator.
    """
    presented = _presented_service_token(request)
    if presented is not None:
        # `and` order matters: an unset server-side token must never match a
        # caller who also presents an empty one.
        if settings.service_token and hmac.compare_digest(presented, settings.service_token):
            return "service"
        raise HTTPException(status_code=401, detail="invalid service credential")

    # No bearer header -- fall through to the browser session path, which
    # 401s on its own if there is no valid cookie either.
    return require_session(request)
