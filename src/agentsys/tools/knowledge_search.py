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
        "that isn't in the metrics database and isn't public web content. This INCLUDES "
        "questions about a specific named person, candidate, or project ('who is X', 'what "
        "is X's experience/background') -- a resume, CV, or profile document may already be "
        "uploaded for exactly that name, so don't assume a person question means web_search. "
        "Try this BEFORE web_search whenever a request could plausibly be answered by an "
        "internal document -- it's the authoritative source for that material and cheaper "
        "to check than the open web. Returns {answer, sources (titles actually cited), "
        "confidence: {overall (0-1)}}; if sources is empty or confidence is low, that "
        "means Knowledge genuinely doesn't have this, not that the query needs rephrasing -- "
        "move on to web_search or another tool for the next step rather than repeating this call. "
        "Arguments: question (str, required) -- ask a full natural-language question, e.g. "
        "\"What is Jane Doe's work experience?\", not just a bare name or keyword."
    )

    def run(self, question: str) -> ToolResult:
        if not question or not question.strip():
            return ToolResult(success=False, error="question must be a non-empty string")

        if not settings.service_token:
            # Named plainly rather than surfacing as a bare 401 from the
            # other service: a misconfiguration should say what to set, not
            # make someone read two codebases to find out.
            return ToolResult(
                success=False,
                error=(
                    "knowledge search is not configured: SERVICE_TOKEN is unset, so this "
                    "service cannot authenticate to rag-api. Set the same SERVICE_TOKEN "
                    "for both services."
                ),
            )

        try:
            response = httpx.post(
                f"{settings.rag_api_url}/v1/ask",
                json={"question": question},
                # Bearer, not a cookie: this is a worker process with no
                # browser session. Never logged -- httpx does not log headers,
                # and the error paths below deliberately report only status.
                headers={"Authorization": f"Bearer {settings.service_token}"},
                timeout=60.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return ToolResult(
                    success=False,
                    error=(
                        "knowledge search was refused (401): this service's SERVICE_TOKEN "
                        "does not match rag-api's. Check both are set to the same value."
                    ),
                )
            return ToolResult(
                success=False,
                error=f"knowledge search failed with status {e.response.status_code}",
            )
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
