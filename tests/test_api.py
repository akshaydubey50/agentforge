from fastapi.testclient import TestClient

from rag.main import app

client = TestClient(app)


def test_list_strategies():
    response = client.get("/v1/strategies")
    assert response.status_code == 200
    assert set(response.json()["strategies"]) == {"fixed_overlap", "structure_aware", "semantic"}


def test_list_documents_covers_all_formats():
    response = client.get("/v1/documents")
    assert response.status_code == 200
    docs = response.json()
    # Floor rather than exact count -- see the matching note in test_ingest.py:
    # the corpus grows as documents are ingested, and this test is about the
    # endpoint reporting every format, not about a fixed corpus size.
    assert len(docs) >= 12
    assert {d["format"] for d in docs} == {"markdown", "text", "html", "pdf"}


def test_ask_rejects_unknown_strategy():
    response = client.post("/v1/ask", json={"question": "test", "strategy": "not_a_real_strategy"})
    assert response.status_code == 400
