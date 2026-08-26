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


def test_ask_rejects_an_unknown_strategy():
    """What happens PAST the gate: a 400 means the request got through
    authentication and was then rejected on its merits, before any retrieval.

    /v1/ask used to be ungated entirely, and this test presented no
    credential at all. Phase 0 closed that (audit §7.3) -- it now takes
    either a service token or a browser session (rag/auth.py's
    require_service_or_session). Whether that gate holds is
    tests/test_rag_ask_auth.py's subject and is not restated here; a signed-in
    client is used because it is the branch this file already has a helper
    for, and the one test_rag_ask_auth.py does not exercise."""
    response = _signed_in_client().post(
        "/v1/ask", json={"question": "test", "strategy": "not_a_real_strategy"}
    )
    assert response.status_code == 400
