"""rag's HTTP surface.

The browser-facing endpoints here require a session (see rag/auth.py), so
these tests mint a real one through agentsys's session layer rather than
faking a Redis key by hand. That is deliberate: the two packages don't
import each other, so the session format is mirrored -- and a mirror that
drifts is exactly how rag ended up looking sessions up by raw token after
agentsys moved to storing hashes, silently 401ing every signed-in user.
Writing the key by hand in the test would have reproduced the drift instead
of catching it.
"""

from fastapi.testclient import TestClient

from agentsys import auth as agentsys_auth
from agentsys.db.session import init_db
from rag.config import settings as rag_settings
from rag.main import app
from tests.conftest import get_test_owner_id

client = TestClient(app)


def _signed_in_client() -> TestClient:
    init_db()
    token = agentsys_auth.create_session(get_test_owner_id())
    c = TestClient(app)
    c.cookies.set(rag_settings.session_cookie, token)
    return c


def test_list_strategies():
    response = _signed_in_client().get("/v1/strategies")
    assert response.status_code == 200
    assert set(response.json()["strategies"]) == {"fixed_overlap", "structure_aware", "semantic"}


def test_list_documents_covers_all_formats():
    response = _signed_in_client().get("/v1/documents")
    assert response.status_code == 200
    docs = response.json()
    # Floor rather than exact count -- see the matching note in test_ingest.py:
    # the corpus grows as documents are ingested, and this test is about the
    # endpoint reporting every format, not about a fixed corpus size.
    assert len(docs) >= 12
    assert {d["format"] for d in docs} == {"markdown", "text", "html", "pdf"}


def test_browser_facing_endpoints_require_a_session():
    """The gate itself, asserted rather than assumed -- an auth dependency
    that quietly stops being applied looks identical to one that works."""
    assert client.get("/v1/strategies").status_code == 401
    assert client.get("/v1/documents").status_code == 401


def test_ask_stays_open_for_server_to_server_callers():
    """/v1/ask is deliberately NOT gated: agentsys's knowledge_search tool
    calls it from the worker, which has no browser session to present (see
    rag/auth.py's module docstring). A 400 here means it got past auth and
    rejected the strategy on its merits."""
    response = client.post("/v1/ask", json={"question": "test", "strategy": "not_a_real_strategy"})
    assert response.status_code == 400
