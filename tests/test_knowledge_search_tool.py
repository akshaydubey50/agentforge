from agentsys.config import settings
from agentsys.tools.knowledge_search import KnowledgeSearchTool


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "answer": "Use short batches [1].",
            "sources": [
                {"title": "Database Migration Guide", "cited": True},
                {"title": "Uncited Nearby Chunk", "cited": False},
            ],
            "confidence": {"overall": 0.82},
            "unsupported_claims": [],
        }


def test_knowledge_search_queries_same_strategy_as_knowledge_page(monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, "service_token", "test-service-token", raising=False)
    monkeypatch.setattr(settings, "rag_api_url", "http://rag-api.test", raising=False)

    def _post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr("agentsys.tools.knowledge_search.httpx.post", _post)

    result = KnowledgeSearchTool().run("What batch size should database backfills use?")

    assert result.success is True
    assert captured["url"] == "http://rag-api.test/v1/ask"
    assert captured["headers"] == {"Authorization": "Bearer test-service-token"}
    assert captured["timeout"] == 60.0
    assert captured["json"] == {
        "question": "What batch size should database backfills use?",
        "strategy": "semantic",
        "top_k": 5,
        "use_reranker": True,
        "sparse_weight": 1.0,
    }
    assert result.output["sources"] == ["Database Migration Guide"]
    assert result.output["confidence"] == 0.82
