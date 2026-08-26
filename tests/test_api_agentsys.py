"""API-level tests for agentsys's control plane: auth gating, idempotency,
input bounds, rate limiting, and cancellation.

These are Layer-1 "harness correctness" tests -- fully deterministic, no LLM
calls. run_agent_task.delay is monkeypatched to a no-op throughout, so
creating a task exercises the API path without needing a live Celery worker
(and without spending money).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from agentsys import idempotency
from agentsys.auth import create_session
from agentsys.config import settings
from agentsys.db.models import Task, TaskStatus, User
from agentsys.db.session import get_session, init_db
from agentsys.main import app
from agentsys.memory.short_term import get_redis


@pytest.fixture(autouse=True)
def _no_celery(monkeypatch):
    """Every task-creating endpoint calls run_agent_task.delay -- stub it so
    these tests never enqueue real agent work."""
    from agentsys import worker

    monkeypatch.setattr(worker.run_agent_task, "delay", lambda *a, **kw: None)


@pytest.fixture
def user() -> User:
    """A fresh user per test, so rate-limit counters and idempotency keys
    from one test can never bleed into another (both are keyed by user id)."""
    init_db()
    sub = f"test-api-{uuid.uuid4()}"
    with get_session() as session:
        u = User(google_sub=sub, email=f"{sub}@example.com", name="API Test")
        session.add(u)
        session.commit()
        session.refresh(u)
        session.expunge(u)
        return u


@pytest.fixture
def client(user: User) -> TestClient:
    """An authenticated client: mints a real session the same way the OAuth
    callback does (auth.create_session) and sets the cookie the dependency
    reads, rather than mocking get_current_user -- so the auth path itself
    is covered by every test below."""
    token = create_session(user.id, ip="127.0.0.1", user_agent="pytest")
    c = TestClient(app)
    c.cookies.set(settings.session_cookie_name, token)
    return c


def test_unauthenticated_requests_are_rejected():
    anon = TestClient(app)
    assert anon.get("/v1/tasks").status_code == 401
    assert anon.post("/v1/tasks", json={"request_text": "hi"}).status_code == 401


def test_create_task_succeeds_and_is_owned_by_caller(client: TestClient, user: User):
    resp = client.post("/v1/tasks", json={"request_text": "summarize something"})
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["id"]

    with get_session() as session:
        assert session.get(Task, task_id).owner_id == user.id


def test_same_idempotency_key_returns_the_same_task(client: TestClient):
    key = f"key-{uuid.uuid4()}"
    headers = {"Idempotency-Key": key}

    first = client.post("/v1/tasks", json={"request_text": "do the thing"}, headers=headers)
    second = client.post("/v1/tasks", json={"request_text": "do the thing"}, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_idempotency_replay_wins_over_active_task_capacity(client: TestClient, monkeypatch):
    key = f"key-{uuid.uuid4()}"
    headers = {"Idempotency-Key": key}
    first = client.post("/v1/tasks", json={"request_text": "do the thing"}, headers=headers)
    assert first.status_code == 200

    monkeypatch.setattr(settings, "max_active_tasks", 0)
    replay = client.post("/v1/tasks", json={"request_text": "do the thing"}, headers=headers)

    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]


def test_different_idempotency_keys_create_different_tasks(client: TestClient):
    a = client.post(
        "/v1/tasks", json={"request_text": "x"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    b = client.post(
        "/v1/tasks", json={"request_text": "x"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    assert a.json()["id"] != b.json()["id"]


def test_idempotency_key_is_scoped_per_user(client: TestClient, user: User):
    """A key one user has used must never resolve to their task for someone
    else -- that would be a cross-tenant leak, not just a stale cache."""
    key = f"shared-{uuid.uuid4()}"
    mine = client.post("/v1/tasks", json={"request_text": "mine"}, headers={"Idempotency-Key": key})

    other_sub = f"test-api-{uuid.uuid4()}"
    with get_session() as session:
        other = User(google_sub=other_sub, email=f"{other_sub}@example.com")
        session.add(other)
        session.commit()
        session.refresh(other)
        other_id = other.id

    assert idempotency.lookup(other_id, key) is None
    assert idempotency.lookup(user.id, key) == mine.json()["id"]


def test_oversized_request_text_is_rejected(client: TestClient):
    too_long = "x" * (settings.max_request_text_length + 1)
    resp = client.post("/v1/tasks", json={"request_text": too_long})
    assert resp.status_code == 422


def test_empty_request_text_is_rejected(client: TestClient):
    assert client.post("/v1/tasks", json={"request_text": ""}).status_code == 422


def test_unknown_field_is_rejected(client: TestClient):
    """extra="forbid" -- a typo'd field name should 422, not be silently
    dropped (which would let a client think it set something it didn't)."""
    resp = client.post("/v1/tasks", json={"request_text": "hi", "requst_txt": "typo"})
    assert resp.status_code == 422


def test_rate_limit_returns_429_with_retry_after(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "task_rate_limit_per_hour", 3)

    for _ in range(3):
        assert client.post("/v1/tasks", json={"request_text": "ok"}).status_code == 200

    blocked = client.post("/v1/tasks", json={"request_text": "one too many"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_cancel_sets_status_and_flag(client: TestClient):
    from agentsys import cancellation

    task_id = client.post("/v1/tasks", json={"request_text": "long job"}).json()["id"]

    resp = client.post(f"/v1/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    # The flag is what a running worker actually checks (see nodes.py's
    # _check_cancelled) -- status alone wouldn't stop anything.
    assert cancellation.is_cancel_requested(task_id) is True


def test_cancelling_a_finished_task_is_a_conflict(client: TestClient):
    task_id = client.post("/v1/tasks", json={"request_text": "done already"}).json()["id"]
    with get_session() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.COMPLETED
        session.add(task)
        session.commit()

    assert client.post(f"/v1/tasks/{task_id}/cancel").status_code == 409


def test_cannot_cancel_another_users_task(client: TestClient):
    """404 rather than 403 -- consistent with the other task endpoints, so a
    probe can't confirm the id exists."""
    other_sub = f"test-api-{uuid.uuid4()}"
    with get_session() as session:
        other = User(google_sub=other_sub, email=f"{other_sub}@example.com")
        session.add(other)
        session.commit()
        session.refresh(other)
        foreign = Task(request_text="not yours", owner_id=other.id)
        session.add(foreign)
        session.commit()
        session.refresh(foreign)
        foreign_id = foreign.id

    assert client.post(f"/v1/tasks/{foreign_id}/cancel").status_code == 404


def test_a_running_node_cannot_resurrect_a_cancelled_task(client: TestClient):
    """Regression for a real race found in live testing: a node that passed
    _check_cancelled BEFORE the cancel landed still finishes its own body
    and writes a status, which used to clobber CANCELLED back to RUNNING --
    the task then showed 'running' forever. _set_task_status must refuse to
    move a task out of CANCELLED."""
    from agentsys.graph.nodes import _set_task_status

    task_id = client.post("/v1/tasks", json={"request_text": "race"}).json()["id"]
    client.post(f"/v1/tasks/{task_id}/cancel")

    with get_session() as session:
        task = session.get(Task, task_id)
        _set_task_status(session, task, TaskStatus.RUNNING)
        session.commit()

    with get_session() as session:
        assert session.get(Task, task_id).status == TaskStatus.CANCELLED


def test_cancelled_task_stops_the_graph():
    """The cancellation checkpoint itself (nodes._check_cancelled), rather
    than the HTTP surface -- a cancelled task must raise TaskCancelled
    instead of proceeding into an expensive step."""
    from agentsys import cancellation
    from agentsys.graph.nodes import _check_cancelled

    task_id = str(uuid.uuid4())
    _check_cancelled(task_id)  # no flag set -- must be a no-op

    cancellation.request_cancel(task_id)
    with pytest.raises(cancellation.TaskCancelled):
        _check_cancelled(task_id)

    cancellation.clear_cancel(task_id)
    _check_cancelled(task_id)  # cleared -- a resumed run proceeds again
