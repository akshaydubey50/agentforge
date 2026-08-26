import asyncio

import rag.config as rag_config
import rag.main as rag_main
from rag.ingest.chunking import ChunkingStrategy


class _Upload:
    filename = "new_policy.md"

    async def read(self) -> bytes:
        return b"# New Policy\n\nBackfills should use short batches."


def test_upload_saves_document_and_rebuilds_semantic_index(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(rag_config, "PROJECT_ROOT", tmp_path)

    def _run_ingest(strategy):
        calls.append(strategy)
        return {
            "strategy": strategy.value,
            "documents": 1,
            "input_chunks": 2,
            "indexed_chunks": 2,
            "duplicates_dropped": 0,
        }

    monkeypatch.setattr(rag_main, "run_ingest", _run_ingest)

    result = asyncio.run(rag_main.upload_document(_Upload(), _user_id="user-1"))

    saved = tmp_path / "data" / "raw" / "new_policy.md"
    assert saved.read_text(encoding="utf-8").startswith("# New Policy")
    assert calls == [ChunkingStrategy.SEMANTIC]
    assert result.document.filename == "new_policy.md"
    assert result.document.title == "New Policy"
    assert result.index_result.strategy == "semantic"
    assert result.index_result.indexed_chunks == 2
