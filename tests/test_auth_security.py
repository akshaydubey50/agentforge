"""Security properties of the session layer.

These assert behaviour an attacker would try to violate, not just that the
happy path works: that a session cannot outlive its absolute window however
actively it's used, that an abandoned one dies, that revocation is real and
immediate, and that nothing replayable is ever written to the database.

Fully deterministic -- no LLM calls, no network.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from agentsys import auth
from agentsys.config import settings
from agentsys.db.models import User, UserSession
from agentsys.db.session import get_session, init_db
from agentsys.main import app


@pytest.fixture
def user() -> User:
    init_db()
    sub = f"test-auth-{uuid.uuid4()}"
    with get_session() as db:
        u = User(google_sub=sub, email=f"{sub}@example.com", name="Auth Test")
        db.add(u)
        db.commit()
        db.refresh(u)
        db.expunge(u)
        return u


def _client(token: str) -> TestClient:
    c = TestClient(app)
    c.cookies.set(settings.session_cookie_name, token)
    return c


def _session_row(token: str) -> UserSession:
    with get_session() as db:
        return db.exec(
            select(UserSession).where(UserSession.token_hash == auth.hash_token(token))
        ).one()


def _backdate(token: str, *, created: timedelta | None = None, last_seen: timedelta | None = None):
    """Shifts a session's timestamps into the past so an expiry that would
    take hours of real time can be asserted in milliseconds."""
    with get_session() as db:
        row = db.exec(
            select(UserSession).where(UserSession.token_hash == auth.hash_token(token))
        ).one()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if created is not None:
            row.created_at = now - created
            row.expires_at = row.created_at + timedelta(
                seconds=settings.session_absolute_lifetime_seconds
            )
        if last_seen is not None:
            row.last_seen_at = now - last_seen
        db.add(row)
        db.commit()


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def test_raw_token_is_never_persisted(user: User):
    """The whole point of hashing: read access to the session table must not
    yield anything an attacker can replay as a cookie."""
    token = auth.create_session(user.id)
    row = _session_row(token)

    assert row.token_hash != token
    assert row.token_hash == auth.hash_token(token)
    # No column anywhere on the row holds the raw value.
    assert token not in str(row.model_dump())


def test_session_records_device_metadata(user: User):
    token = auth.create_session(user.id, ip="203.0.113.7", user_agent="Firefox/1.0")
    row = _session_row(token)

    assert row.ip == "203.0.113.7"
    assert row.user_agent == "Firefox/1.0"


# --------------------------------------------------------------------------
# Expiry -- the defect this work exists to fix
# --------------------------------------------------------------------------


def test_absolute_lifetime_kills_a_continuously_used_session(user: User):
    """The core regression. The previous design refreshed the TTL on every
    request, so a token that kept being used never expired -- a stolen
    cookie was valid forever. Activity must NOT extend the absolute cap."""
    token = auth.create_session(user.id)
    client = _client(token)
    assert client.get("/v1/auth/me").status_code == 200

    # Actively used the whole time: last_seen is now, so the idle timeout is
    # nowhere near firing. Only the absolute cap can end this session.
    _backdate(
        token,
        created=timedelta(seconds=settings.session_absolute_lifetime_seconds + 60),
        last_seen=timedelta(seconds=0),
    )

    assert client.get("/v1/auth/me").status_code == 401


def test_idle_timeout_kills_an_unused_session(user: User):
    token = auth.create_session(user.id)
    client = _client(token)
    assert client.get("/v1/auth/me").status_code == 200

    _backdate(token, last_seen=timedelta(seconds=settings.session_idle_timeout_seconds + 60))

    assert client.get("/v1/auth/me").status_code == 401


def test_active_use_holds_a_session_open_within_its_window(user: User):
    """The counterpart: expiry must not be so eager that ordinary use logs
    someone out mid-work."""
    token = auth.create_session(user.id)
    client = _client(token)

    _backdate(token, last_seen=timedelta(seconds=settings.session_idle_timeout_seconds // 2))

    assert client.get("/v1/auth/me").status_code == 200


def test_expired_session_is_marked_revoked_not_silently_dropped(user: User):
    """An expiry should leave evidence. A row that just vanishes tells an
    investigator nothing."""
    token = auth.create_session(user.id)
    _backdate(token, last_seen=timedelta(seconds=settings.session_idle_timeout_seconds + 60))

    _client(token).get("/v1/auth/me")

    assert _session_row(token).revoked_at is not None


# --------------------------------------------------------------------------
# Revocation
# --------------------------------------------------------------------------


def test_revoked_session_is_rejected_immediately(user: User):
    token = auth.create_session(user.id)
    client = _client(token)
    assert client.get("/v1/auth/me").status_code == 200

    auth.revoke_session(token)

    # Immediately, not after the cache TTL lapses -- revocation that waits
    # for a cache to expire isn't revocation.
    assert client.get("/v1/auth/me").status_code == 401


def test_logout_revokes_the_session_server_side(user: User):
    token = auth.create_session(user.id)
    client = _client(token)

    assert client.post("/v1/auth/logout").status_code == 200

    # Clearing the cookie isn't enough; a copied token must also be dead.
    assert _client(token).get("/v1/auth/me").status_code == 401


def test_revoke_all_kills_other_sessions_but_keeps_the_caller(user: User):
    other = auth.create_session(user.id)
    current = auth.create_session(user.id)

    resp = _client(current).post("/v1/auth/sessions/revoke-all")

    assert resp.status_code == 200
    assert resp.json()["revoked"] == 1
    assert _client(other).get("/v1/auth/me").status_code == 401
    # Logging someone out of the browser they're using to secure the account
    # would be hostile -- the calling session survives on purpose.
    assert _client(current).get("/v1/auth/me").status_code == 200


def test_rotation_issues_a_new_token_and_kills_the_old(user: User):
    """Session fixation defence: after a privilege change the old
    identifier must stop working, or an attacker who planted it still
    holds a valid session."""
    old = auth.create_session(user.id)
    new = auth.rotate_session(old, user.id)

    assert new != old
    assert _client(old).get("/v1/auth/me").status_code == 401
    assert _client(new).get("/v1/auth/me").status_code == 200


# --------------------------------------------------------------------------
# Session management endpoints
# --------------------------------------------------------------------------


def test_listing_sessions_marks_the_current_one_and_leaks_no_credential(user: User):
    token = auth.create_session(user.id, ip="198.51.100.4", user_agent="Chrome/1.0")
    auth.create_session(user.id)

    body = _client(token).get("/v1/auth/sessions").json()

    assert len(body) == 2
    assert sum(1 for s in body if s["current"]) == 1
    assert any(s["ip"] == "198.51.100.4" for s in body)
    # Nothing token-shaped may reach the client.
    serialized = str(body)
    assert token not in serialized
    assert auth.hash_token(token) not in serialized


def test_cannot_revoke_another_users_session(user: User):
    victim_token = auth.create_session(user.id)
    victim_session_id = _session_row(victim_token).id

    other_sub = f"test-auth-{uuid.uuid4()}"
    with get_session() as db:
        attacker = User(google_sub=other_sub, email=f"{other_sub}@example.com")
        db.add(attacker)
        db.commit()
        db.refresh(attacker)
        attacker_id = attacker.id
    attacker_token = auth.create_session(attacker_id)

    resp = _client(attacker_token).delete(f"/v1/auth/sessions/{victim_session_id}")

    # 404 not 403 -- a probe must not confirm the session id exists.
    assert resp.status_code == 404
    assert _client(victim_token).get("/v1/auth/me").status_code == 200


def test_unauthenticated_access_to_session_endpoints_is_rejected():
    anon = TestClient(app)
    assert anon.get("/v1/auth/sessions").status_code == 401
    assert anon.post("/v1/auth/sessions/revoke-all").status_code == 401


def test_garbage_token_is_rejected():
    assert _client("not-a-real-token").get("/v1/auth/me").status_code == 401
