"""/health/deep must check dependencies without polluting business data.

Before this it enqueued a Celery task that INSERTED a `Task` row on every
call, then blocked up to 15s on the result -- so pointing a load balancer at
it meant unbounded growth in the application's own table plus a request that
could hang (audit §7.6).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlmodel import select

from agentsys.db.models import Task
from agentsys.db.session import get_session, init_db
from agentsys.main import app

client = TestClient(app)


def setup_module() -> None:
    init_db()


def _task_count() -> int:
    with get_session() as session:
        return session.exec(select(func.count()).select_from(Task)).one()


def test_deep_health_creates_no_task_rows():
    """The regression that matters. Several calls, zero new rows."""
    before = _task_count()

    for _ in range(3):
        client.get("/health/deep")

    assert _task_count() == before, "readiness must not write to the business tables"


def test_deep_health_reports_each_dependency():
    response = client.get("/health/deep")

    assert response.status_code == 200
    body = response.json()
    for dependency in ("database", "chroma", "celery"):
        assert dependency in body


def test_deep_health_never_5xxs_on_a_down_dependency(monkeypatch):
    """A degraded dependency must be reported in the body with 200, not
    raised. An exception flattens "Chroma is down" and "this endpoint is
    broken" into the same response."""
    import agentsys.main as main

    def _boom():
        raise RuntimeError("chroma is unreachable")

    monkeypatch.setattr(main, "get_chroma_client", _boom)
    response = client.get("/health/deep")

    assert response.status_code == 200
    body = response.json()
    assert body["chroma"].startswith("failed")
    assert body["status"] == "degraded"


def test_deep_health_is_bounded():
    """It used to block up to 15s on a Celery result. The ping timeout plus
    two local round trips should be far inside this; the assertion is
    deliberately loose so it fails on a hang, not on a slow machine."""
    started = time.monotonic()
    client.get("/health/deep")
    elapsed = time.monotonic() - started

    assert elapsed < 10.0, f"deep health took {elapsed:.1f}s -- it should be bounded"


def test_shallow_health_is_unchanged():
    assert client.get("/health").json() == {"status": "ok"}
