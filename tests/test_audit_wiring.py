"""That the audit log is actually WIRED, not merely implemented.

test_audit.py proves the chain is sound. These prove the events reach it
from the real code paths -- which is the failure mode that matters in
practice: an audit module nobody calls looks entirely healthy right up
until the moment someone needs the record and it isn't there.

Fully deterministic -- no LLM calls, no network.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from agentsys import audit, auth
from agentsys.config import settings
from agentsys.db.models import (
    AuditEvent,
    Escalation,
    Task,
    TaskStatus,
    User,
    UserSession,
)
from agentsys.db.session import get_session, init_db
from agentsys.escalations import apply_escalation_decision
from agentsys.main import app
from tests.conftest import get_test_owner_id


@pytest.fixture
def user() -> User:
    init_db()
    sub = f"test-audit-wiring-{uuid.uuid4()}"
    with get_session() as db:
        u = User(google_sub=sub, email=f"{sub}@example.com", name="Audit Wiring")
        db.add(u)
        db.commit()
        db.refresh(u)
        db.expunge(u)
        return u


def _events(actor_id: str, action: str | None = None) -> list[AuditEvent]:
    with get_session() as db:
        query = select(AuditEvent).where(AuditEvent.actor_id == actor_id)
        if action:
            query = query.where(AuditEvent.action == action)
        rows = db.exec(query.order_by(AuditEvent.seq)).all()
        for r in rows:
            db.expunge(r)
        return rows


def test_creating_a_session_is_recorded(user: User):
    auth.create_session(user.id, ip="203.0.113.7", user_agent="pytest-agent")
    rows = _events(user.id, audit.Action.SESSION_CREATED)
    assert len(rows) == 1
    assert rows[0].ip == "203.0.113.7"
    assert rows[0].meta["reason"] == "login"


def test_the_session_token_never_reaches_the_audit_log(user: User):
    """The audit log is read by more people than the session table is. It
    must not become a second place credentials live."""
    token = auth.create_session(user.id)
    rows = _events(user.id)
    blob = str([(r.target_id, r.meta) for r in rows])
    assert token not in blob
    assert auth.hash_token(token) not in blob


def test_revoking_a_session_is_recorded_once(user: User):
    token = auth.create_session(user.id)
    auth.revoke_session(token, reason="logout")
    auth.revoke_session(token, reason="logout")  # already revoked -- a no-op

    rows = _events(user.id, audit.Action.SESSION_REVOKED)
    assert len(rows) == 1, "a no-op revoke should not add noise to the log"
    assert rows[0].meta["reason"] == "logout"


def test_sign_out_everywhere_is_one_event_carrying_its_scope(user: User):
    keep = auth.create_session(user.id)
    auth.create_session(user.id)
    auth.create_session(user.id)

    revoked = auth.revoke_all_sessions(user.id, except_token_hash=auth.hash_token(keep))
    assert revoked == 2

    rows = _events(user.id, audit.Action.SESSION_REVOKED_ALL)
    assert len(rows) == 1
    assert rows[0].meta["revoked_count"] == 2
    assert rows[0].meta["kept_current_session"] is True
    assert len(rows[0].meta["session_ids"]) == 2


def test_a_session_crossing_its_idle_deadline_is_recorded_once(user: User):
    """The interesting security event is a cookie presented after its window
    closed. Recorded at the transition, so retrying a dead cookie a thousand
    times still produces exactly one row."""
    token = auth.create_session(user.id)
    with get_session() as db:
        row = db.exec(
            select(UserSession).where(UserSession.token_hash == auth.hash_token(token))
        ).one()
        row.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=settings.session_idle_timeout_seconds + 60
        )
        db.add(row)
        db.commit()

    client = TestClient(app)
    client.cookies.set(settings.session_cookie_name, token)
    for _ in range(3):
        assert client.get("/v1/auth/me").status_code == 401

    rows = _events(user.id, audit.Action.SESSION_EXPIRED)
    assert len(rows) == 1
    assert rows[0].meta["expiry"] == "idle"
    assert rows[0].outcome == "denied"


def test_audit_endpoint_returns_only_the_callers_own_events(user: User):
    """Same isolation boundary as every other endpoint. A user's security
    activity is exactly as sensitive as their task data."""
    other = f"test-audit-other-{uuid.uuid4()}"
    with get_session() as db:
        stranger = User(google_sub=other, email=f"{other}@example.com")
        db.add(stranger)
        db.commit()
        db.refresh(stranger)
        stranger_id = stranger.id

    auth.create_session(stranger_id)
    token = auth.create_session(user.id)

    client = TestClient(app)
    client.cookies.set(settings.session_cookie_name, token)
    body = client.get("/v1/audit").json()

    assert body["items"], "the caller's own events should be visible"
    assert all(e["target_id"] != stranger_id for e in body["items"])
    for event in body["items"]:
        assert stranger_id not in str(event)


def test_audit_endpoint_filter_narrows_items_and_total_together(user: User):
    """A filtered page reporting the unfiltered total makes a client paginate
    off the end of a result set that was never that long."""
    auth.create_session(user.id)
    token = auth.create_session(user.id)
    # Keeps the calling session alive -- and emits a second action type, so
    # the filter has something to actually narrow.
    auth.revoke_all_sessions(user.id, except_token_hash=auth.hash_token(token))

    client = TestClient(app)
    client.cookies.set(settings.session_cookie_name, token)

    unfiltered = client.get("/v1/audit").json()
    filtered = client.get(
        "/v1/audit", params={"action": audit.Action.SESSION_CREATED}
    ).json()

    assert filtered["total"] < unfiltered["total"]
    assert filtered["total"] == len(filtered["items"])
    assert all(e["action"] == audit.Action.SESSION_CREATED for e in filtered["items"])


def test_audit_endpoint_requires_authentication():
    assert TestClient(app).get("/v1/audit").status_code == 401


def test_audit_endpoint_exposes_the_chain_links(user: User):
    """The chain is only useful if it can be checked from outside the
    database, which means the client has to be able to see the linkage."""
    token = auth.create_session(user.id)
    client = TestClient(app)
    client.cookies.set(settings.session_cookie_name, token)
    items = client.get("/v1/audit").json()["items"]

    assert items
    for event in items:
        assert len(event["hash"]) == 64
        assert len(event["prev_hash"]) == 64
        assert isinstance(event["seq"], int)


# --------------------------------------------------------------------------
# Escalation decisions -- the approvals that actually let irreversible things
# happen, and therefore the rows this log exists for
# --------------------------------------------------------------------------


def _pending_escalation(kind: str = "plan") -> str:
    with get_session() as db:
        task = Task(
            owner_id=get_test_owner_id(),
            request_text="audit wiring fixture",
            status=TaskStatus.AWAITING_APPROVAL,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        escalation = Escalation(
            task_id=task.id, subtask_id=None, kind=kind, reason="forced for audit test"
        )
        db.add(escalation)
        db.commit()
        db.refresh(escalation)
        return escalation.id


def _by_target(target_id: str) -> list[AuditEvent]:
    with get_session() as db:
        rows = db.exec(
            select(AuditEvent).where(AuditEvent.target_id == target_id).order_by(AuditEvent.seq)
        ).all()
        for r in rows:
            db.expunge(r)
        return rows


def test_an_escalation_decision_is_recorded_with_who_decided_what():
    escalation_id = _pending_escalation()
    apply_escalation_decision(
        escalation_id,
        decision="approve",
        note="looks fine",
        decided_by="approver@example.com",
        actor_id="actor-123",
        ip="198.51.100.4",
    )

    rows = _by_target(escalation_id)
    assert len(rows) == 1
    event = rows[0]
    assert event.action == audit.Action.ESCALATION_DECIDED
    assert event.actor_label == "approver@example.com"
    assert event.actor_id == "actor-123"
    assert event.ip == "198.51.100.4"
    assert event.meta["decision"] == "approve"
    assert event.meta["note"] == "looks fine"


def test_a_rejection_is_recorded_as_denied_not_as_a_failure():
    """outcome distinguishes "the human said no" from "something broke". A
    reject is a successful decision with a negative answer, and conflating
    the two would make the log unqueryable for either."""
    escalation_id = _pending_escalation()
    apply_escalation_decision(
        escalation_id, decision="reject", note="not viable", decided_by="pytest"
    )
    event = _by_target(escalation_id)[0]
    assert event.outcome == "denied"
    assert event.meta["decision"] == "reject"


def test_a_decision_made_outside_http_is_still_recorded():
    """The record lives in apply_escalation_decision, not the route, so that
    no entry point -- a CLI, a Slack action, a test -- can approve something
    irreversible without leaving one. This call goes nowhere near FastAPI."""
    escalation_id = _pending_escalation()
    apply_escalation_decision(escalation_id, decision="approve", decided_by="cli")

    rows = _by_target(escalation_id)
    assert len(rows) == 1
    assert rows[0].actor_label == "cli"
    assert rows[0].actor_id is None  # no HTTP layer, so no authenticated actor


def test_a_failed_decision_leaves_no_record():
    """A rejected call changed nothing, so the log must not claim it did --
    the same reason the record is written after the transaction commits."""
    escalation_id = _pending_escalation()
    apply_escalation_decision(escalation_id, decision="approve", decided_by="first")

    with pytest.raises(Exception):
        # Already decided -- raises EscalationError(409) before doing anything.
        apply_escalation_decision(escalation_id, decision="reject", decided_by="second")

    rows = _by_target(escalation_id)
    assert len(rows) == 1, "a rejected decision must not produce an audit row"
