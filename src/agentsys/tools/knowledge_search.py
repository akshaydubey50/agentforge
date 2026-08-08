"""Wires the agent to rag-api's hybrid retrieval + grounded generation
pipeline (src/rag) -- the one HTTP seam between the two otherwise-independent
packages (see docs/MERGE.md). Before this tool existed, documents uploaded
via the Knowledge screen were genuinely invisible to Ask: the UI could
upload/list/reindex them for real, but nothing in agentsys could ever read
one back. This is what closes that gap.
"""

import httpx

from agentsys.config import settings
from agentsys.tools.base import Tool, ToolResult


class KnowledgeSearchTool(Tool):
    name = "knowledge_search"
    description = (
        "Answers a question using documents uploaded to Knowledge -- runs real hybrid "
        "search (not a keyword match) plus grounded generation with citations. Use this "
        "for anything about internal docs, policies, guides, or other uploaded material "
        "that isn't in the metrics database and isn't public web content. "
        "Arguments: question (str, required)."
    )

    def run(self, question: str) -> ToolResult:
        if not question or not question.strip():
            return ToolResult(success=False, error="question must be a non-empty string")

        try:
            response = httpx.post(
                f"{settings.rag_api_url}/v1/ask",
                json={"question": question},
                timeout=60.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"knowledge search request failed: {e}")

        data = response.json()
        output = {
            "answer": data["answer"],
            "sources": [s["title"] for s in data.get("sources", [])],
            "confidence": data.get("confidence", {}).get("overall"),
        }
        # Report, don't pre-judge -- same division of labor as every other
        # tool here: the tool surfaces what happened, the Reviewer node
        # decides if that's good enough.
        if data.get("unsupported_claims"):
            output["warning"] = "Some claims in the answer weren't fully supported by the documents."
        return ToolResult(success=True, output=output)
