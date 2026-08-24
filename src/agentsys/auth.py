"""Session authentication.

A session is a random opaque token in an httpOnly cookie. Not a JWT: an
opaque server-side token can be revoked the instant it's compromised, while
a signed JWT stays valid until it expires no matter what the server wants --
and "revoke this session now" is a requirement, not a nice-to-have.

Storage is Postgres (UserSession) as the source of truth, with Redis in
front as a hot cache. The split matters:
  - Postgres makes sessions ENUMERABLE (sign out everywhere, show me my
    devices) and keeps history through a cache flush, which is what an
    incident investigation actually reads.
  - Redis keeps the per-request validation cost at one memory lookup, so
    durability doesn't cost latency on every API call.

The token itself is never stored anywhere -- only its SHA-256. Read access
to the session table therefore yields nothing replayable, the same reason
password hashes rather than passwords are stored. A plain SHA-256 (not a
slow KDF) is correct here specifically because the token is 256 bits of
CSPRNG output: there is no dictionary to attack, so the slow-hash property
that protects human-chosen passwords buys nothing and would cost a KDF run
on every single request.

Two independent expiries are enforced on every request -- see
settings.session_idle_timeout_seconds and
settings.session_absolute_lifetime_seconds for what each defends against.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response
from sqlmodel import select

from agentsys import audit
from agentsys.config import settings
from agentsys.db.models import User, UserSession
from agentsys.db.session import get_session
from agentsys.memory.short_term import get_redis

_SYSTEM_GOOGLE_SUB_PREFIX = "system:"

# How stale last_seen_at may get before it's written back. Without this the
# idle timeout would cost a Postgres UPDATE on every single authenticated
# request; a minute of imprecision on a 30-minute window is irrelevant.
_LAST_SEEN_WRITE_INTERVAL = timedelta(seconds=60)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Postgres columns here are TIMESTAMP WITHOUT TIME ZONE, so values come
    back naive. Comparing a naive datetime against an aware one raises, so
    everything is normalised to aware-UTC on the way out."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cache_key(token_hash: str) -> str:
    return f"session:{token_hash}"


def create_session(
    user_id: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    reason: str = "login",
) -> str:
    """Mints a new session and returns the raw token -- the only moment the
    raw value exists outside the caller's cookie.

    `reason` is carried into the audit log only. A session appearing out of
    nowhere is exactly the thing an investigator needs explained, and
    "login" vs "rotation" vs "step_up" is the difference between a normal
    event and one worth chasing."""
    token = secrets.token_urlsafe(32)
    token_hash = hash_token(token)
    now = _now()
    expires_at = now + timedelta(seconds=settings.session_absolute_lifetime_seconds)

    with get_session() as db:
        row = UserSession(
            user_id=user_id,
            token_hash=token_hash,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            ip=ip,
            user_agent=(user_agent or "")[:400] or None,
        )
        db.add(row)
        db.commit()
        session_id = row.id

    _cache_put(token_hash, user_id)
    # The session ROW id, never the token or its hash -- the audit log has to
    # be readable by more people than the session table is, so it must not
    # carry anything derived from a live credential.
    audit.record(
        audit.Action.SESSION_CREATED,
        actor_id=user_id,
        target_type="session",
        target_id=session_id,
        ip=ip,
        user_agent=user_agent,
        meta={"reason": reason, "expires_at": expires_at.isoformat()},
    )
    return token


def _cache_put(token_hash: str, user_id: str) -> None:
    """Cache TTL is the IDLE timeout, so an untouched session falls out of
    cache exactly when it becomes invalid. The absolute cap is still checked
    against Postgres on every hit -- a cache entry must never be able to
    outlive the real expiry."""
    try:
        get_redis().setex(
            _cache_key(token_hash), settings.session_idle_timeout_seconds, user_id
        )
    except Exception:  # noqa: BLE001 -- cache is an optimisation, not the truth
        pass


def _cache_drop(token_hash: str) -> None:
    try:
        get_redis().delete(_cache_key(token_hash))
    except Exception:  # noqa: BLE001
        pass


def revoke_session(token: str, *, reason: str = "explicit") -> None:
    revoke_session_by_hash(hash_token(token), reason=reason)


def revoke_session_by_hash(token_hash: str, *, reason: str = "explicit") -> None:
    """Revoke by hash rather than raw token -- callers that only ever see a
    stored session row (the session-management endpoints) never need the
    raw token, and shouldn't be able to obtain one."""
    revoked: tuple[str, str] | None = None
    with get_session() as db:
        row = db.exec(
            select(UserSession).where(UserSession.token_hash == token_hash)
        ).first()
        if row and row.revoked_at is None:
            row.revoked_at = _now()
            db.add(row)
            db.commit()
            revoked = (row.id, row.user_id)
    _cache_drop(token_hash)
    # Only audit an actual state change. Revoking an already-revoked session
    # is a no-op, and a log full of no-ops is a log nobody reads.
    if revoked:
        audit.record(
            audit.Action.SESSION_REVOKED,
            actor_id=revoked[1],
            target_type="session",
            target_id=revoked[0],
            meta={"reason": reason},
        )


def revoke_all_sessions(
    user_id: str, *, except_token_hash: str | None = None, reason: str = "explicit"
) -> int:
    """Kills every session for a user. Used both by the explicit "sign out
    everywhere" control and automatically on password change -- a credential
    change must not leave sessions established under the old one alive.
    Returns how many were revoked."""
    with get_session() as db:
        rows = db.exec(
            select(UserSession).where(
                UserSession.user_id == user_id,
                UserSession.revoked_at == None,  # noqa: E711 -- SQL IS NULL, not Python
            )
        ).all()
        revoked_ids: list[str] = []
        for row in rows:
            if except_token_hash and row.token_hash == except_token_hash:
                continue
            row.revoked_at = _now()
            db.add(row)
            _cache_drop(row.token_hash)
            revoked_ids.append(row.id)
        db.commit()
    # One event for the whole sweep rather than one per session: "signed out
    # everywhere" is a single human decision, and splitting it across N rows
    # would bury it under its own consequences.
    audit.record(
        audit.Action.SESSION_REVOKED_ALL,
        actor_id=user_id,
        target_type="user",
        target_id=user_id,
        meta={
            "revoked_count": len(revoked_ids),
            "session_ids": revoked_ids,
            "reason": reason,
            "kept_current_session": except_token_hash is not None,
        },
    )
    return len(revoked_ids)


def rotate_session(
    old_token: str, user_id: str, *, ip: str | None = None, user_agent: str | None = None
) -> str:
    """Issues a fresh token and kills the old one. Called on every privilege
    change (login, MFA satisfied, password change) -- if the identifier
    never changed, an attacker who planted or observed a pre-auth token
    would still hold a valid one after the victim authenticated, which is
    session fixation."""
    new_token = create_session(user_id, ip=ip, user_agent=user_agent, reason="rotation")
    revoke_session_by_hash(hash_token(old_token), reason="rotation")
    audit.record(
        audit.Action.SESSION_ROTATED,
        actor_id=user_id,
        target_type="user",
        target_id=user_id,
        ip=ip,
        user_agent=user_agent,
    )
    return new_token


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie,
        value=token,
        max_age=settings.session_absolute_lifetime_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.session_cookie, path="/")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _audit_expiry(row: UserSession, kind: str, request: Request) -> None:
    """Records the moment a session crosses one of its two deadlines.

    Deliberately recorded at the TRANSITION (the request that first finds
    the row past its deadline and revokes it) rather than on every
    subsequent 401. That makes it self-rate-limiting -- exactly one row per
    session, however many times a stale cookie is retried -- while still
    capturing the fact worth having: a credential presented after its window
    closed, and from which address.
    """
    audit.record(
        audit.Action.SESSION_EXPIRED,
        actor_id=row.user_id,
        target_type="session",
        target_id=row.id,
        outcome="denied",
        **audit.request_context(request),
        meta={
            "expiry": kind,
            "created_at": _as_utc(row.created_at).isoformat(),
            "last_seen_at": _as_utc(row.last_seen_at).isoformat(),
        },
    )


def get_current_session(request: Request) -> UserSession:
    """The real validation path. 401s unless the session exists, is not
    revoked, is inside BOTH its idle and absolute windows, and belongs to a
    user who still exists.

    The cache is only ever a fast path to a user id; every expiry decision
    is made against the Postgres row, so a stale cache entry cannot extend a
    session past its real expiry."""
    token = request.cookies.get(settings.session_cookie)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")

    token_hash = hash_token(token)
    now = _now()

    with get_session() as db:
        row = db.exec(
            select(UserSession).where(UserSession.token_hash == token_hash)
        ).first()

        if row is None or row.revoked_at is not None:
            _cache_drop(token_hash)
            raise HTTPException(status_code=401, detail="session expired or invalid")

        if now >= _as_utc(row.expires_at):
            # Absolute cap reached -- no amount of activity extends this.
            row.revoked_at = now
            db.add(row)
            db.commit()
            _cache_drop(token_hash)
            _audit_expiry(row, "absolute", request)
            raise HTTPException(status_code=401, detail="session expired")

        idle_deadline = _as_utc(row.last_seen_at) + timedelta(
            seconds=settings.session_idle_timeout_seconds
        )
        if now >= idle_deadline:
            row.revoked_at = now
            db.add(row)
            db.commit()
            _cache_drop(token_hash)
            _audit_expiry(row, "idle", request)
            raise HTTPException(status_code=401, detail="session expired")

        if now - _as_utc(row.last_seen_at) >= _LAST_SEEN_WRITE_INTERVAL:
            row.last_seen_at = now
            db.add(row)
            db.commit()

        db.refresh(row)
        db.expunge(row)

    _cache_put(token_hash, row.user_id)
    return row


def get_current_user(request: Request) -> User:
    """The dependency almost every route uses. Everything task/escalation/
    memory-scoped in main.py filters by this user's id."""
    session_row = get_current_session(request)
    with get_session() as db:
        user = db.get(User, session_row.user_id)
        if not user:
            raise HTTPException(status_code=401, detail="user not found")
        db.expunge(user)
        return user


def get_or_create_system_user(key: str, email: str, name: str) -> str:
    """Get-or-create a synthetic User for infrastructure code that needs a
    real owner_id (Task.owner_id is NOT NULL, see db/models.py) but has no
    signed-in human behind it -- worker.py's ping_task health check, the
    eval harness (scripts/run_agent_eval.py). Idempotent: safe to call on
    every invocation. google_sub is namespaced with a "system:" prefix so
    it can never collide with a real Google account's sub, and each such
    user is invisible in every real user's dashboard the same way any other
    user's data is (see main.py's owner_id-scoped queries) -- no separate
    is_eval-style flag needed to keep it out of view."""
    google_sub = f"{_SYSTEM_GOOGLE_SUB_PREFIX}{key}"
    with get_session() as db:
        user = db.exec(select(User).where(User.google_sub == google_sub)).first()
        if user is None:
            user = User(google_sub=google_sub, email=email, name=name)
            db.add(user)
            db.commit()
            db.refresh(user)
        return user.id
