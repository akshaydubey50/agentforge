"""/v1/ask must not be reachable without a credential (audit §7.3).

rag-api publishes a host port and this endpoint had no authentication at all,
so anyone who could reach it could query the whole corpus and spend LLM
budget, unmetered and unaudited.

No token value is ever asserted on or printed -- only the status code and
whether the pipeline was reached.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from fastapi.testclient import TestClient

import rag.main as rag_main
from rag.config import settings

client = TestClient(rag_main.app)

_SECRET = "phase0-test-token-not-a-real-secret"
_BODY = {"question": "what is in the corpus?"}


@pytest.fixture(autouse=True)
def _never_actually_retrieve(monkeypatch):
    """answer_query would hit Chroma and a real model. Replacing it means an
    authorised call is observable without any of that -- and an UNauthORISED
    call must never reach it, which is the whole assertion."""
    calls = {"n": 0}

    def _fake(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("pipeline reached -- test should assert on auth before this")

    monkeypatch.setattr(rag_main, "answer_query", _fake)
    return calls


@pytest.fixture
def _service_token(monkeypatch):
    monkeypatch.setattr(settings, "service_token", _SECRET, raising=False)


def test_no_credential_is_rejected(_service_token):
    """The exact hole: an anonymous POST from anyone who can reach the port."""
    response = client.post("/v1/ask", json=_BODY)

    assert response.status_code == 401


def test_wrong_service_token_is_rejected(_service_token):
    response = client.post(
        "/v1/ask", json=_BODY, headers={"Authorization": "Bearer wrong-token"}
    )

    assert response.status_code == 401
    # The presented credential must never be echoed back -- an error message
    # that repeats it is how a secret reaches a log aggregator.
    assert "wrong-token" not in response.text


def test_unset_service_token_fails_closed(monkeypatch):
    """An unset secret must NOT reopen the hole. With no token configured,
    service authentication is impossible rather than skipped."""
    monkeypatch.setattr(settings, "service_token", "", raising=False)

    response = client.post(
        "/v1/ask", json=_BODY, headers={"Authorization": "Bearer anything"}
    )

    assert response.status_code == 401


def test_empty_bearer_value_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "service_token", "", raising=False)

    response = client.post("/v1/ask", json=_BODY, headers={"Authorization": "Bearer "})

    assert response.status_code == 401


def test_valid_service_token_is_accepted(_service_token, _never_actually_retrieve):
    """Proves the gate opens for the legitimate caller -- agentsys's worker.
    Reaching the (stubbed) pipeline IS passing auth."""
    with pytest.raises(AssertionError, match="pipeline reached"):
        client.post("/v1/ask", json=_BODY, headers={"Authorization": f"Bearer {_SECRET}"})

    assert _never_actually_retrieve["n"] == 1


def test_health_stays_open():
    """Liveness must not require a credential, or an orchestrator cannot
    tell a locked-down service from a dead one."""
    assert client.get("/health").status_code == 200


def test_service_token_comparison_is_constant_time():
    """A bearer secret with no expiry compared with `==` leaks its prefix
    through timing."""
    source = (Path(__file__).resolve().parents[1] / "src/rag/auth.py").read_text(encoding="utf-8")

    assert "hmac.compare_digest" in source
    assert "== settings.service_token" not in source
