"""Web search tool. Three backends, tried in order, each degrading to the
next when its prerequisite isn't configured:

  1. Tavily -- a real search API purpose-built for LLM/agent use, no
     scraping, no bot-detection risk. Used when TAVILY_API_KEY is set.
  2. OpenAI's own hosted web search (via litellm, using the OpenAI key
     this app already requires for everything else) -- also server-side,
     no scraping. Used when there's no Tavily key but OPENAI_API_KEY is set.
  3. Scraping DuckDuckGo's HTML endpoint -- the original implementation,
     kept verbatim as the last resort so the tool still works out of the
     box with zero external accounts. Its own known rate-limiting tradeoff
     is documented on _duckduckgo_search below.
"""

import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from agentsys.config import settings
from agentsys.llm import complete
from agentsys.execution import ExecutionSafety
from agentsys.policy import ActionType, Risk
from agentsys.sanitize import wrap_untrusted
from agentsys.tools.base import Tool, ToolResult

_TAVILY_URL = "https://api.tavily.com/search"
_OPENAI_SEARCH_MODEL = "openai/gpt-5-search-api"
"""gpt-4o-search-preview and gpt-4o-mini-search-preview (OpenAI's original
web_search_options-capable models) were both deprecated with a 2026-07-23
shutdown date -- every call started failing with a 404 model_not_found once
that date passed. gpt-5-search-api is OpenAI's current replacement for the
same web_search_options-based Chat Completions flow used in
_openai_hosted_search below (see litellm's web search docs)."""

_DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
_DDG_BOT_DETECTION_ERROR = (
    "DuckDuckGo returned a bot-detection challenge instead of results "
    "(likely rate-limited by repeated requests)."
)
_DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://html.duckduckgo.com/html/",
    "Origin": "https://html.duckduckgo.com",
    "Content-Type": "application/x-www-form-urlencoded",
}


def _resolve_ddg_url(href: str) -> str:
    """DDG sometimes wraps result links in a /l/?uddg=<encoded target> redirect."""
    if href.startswith("//duckduckgo.com/l/") or href.startswith("https://duckduckgo.com/l/"):
        query = parse_qs(urlparse(href).query)
        target = query.get("uddg")
        if target:
            return unquote(target[0])
    return href


def _tavily_search(query: str, max_results: int) -> ToolResult:
    try:
        response = httpx.post(
            _TAVILY_URL,
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            json={"query": query, "max_results": max_results, "search_depth": "basic"},
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return ToolResult(success=False, error=f"Tavily search request failed: {e.response.status_code} {e.response.text[:200]}")
    except httpx.HTTPError as e:
        return ToolResult(success=False, error=f"Tavily search request failed: {e}")

    try:
        data = response.json()
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                # Same field name as the DuckDuckGo path ("snippet") so
                # every downstream consumer (prompts, the web UI's
                # StepOutput renderer, existing tests) sees one stable
                # shape regardless of which backend actually answered.
                "snippet": item.get("content", ""),
            }
            for item in data.get("results", [])[:max_results]
        ]
    except Exception as e:
        return ToolResult(success=False, error=f"failed to parse Tavily response: {e}")

    return ToolResult(success=True, output={"results": results})


_MARKDOWN_CITATION_RE = re.compile(r"\(\[([^\]]+)\]\((https?://[^\s)]+)\)\)")


def _extract_markdown_citations(text: str) -> list[dict]:
    """gpt-5-search-api (unlike the older *-search-preview models, which
    returned a structured `annotations` list) embeds its citations as inline
    markdown links in the message content instead, e.g.
    '([python.org](https://www.python.org/doc/...))'. Parses those out so
    results still come back as distinct {title, url} pairs like every other
    backend, instead of one undifferentiated text blob. De-duplicates by
    url -- the model sometimes cites the same source more than once across
    a longer answer."""
    seen_urls: set[str] = set()
    citations = []
    for title, url in _MARKDOWN_CITATION_RE.findall(text):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        citations.append({"title": title, "url": url})
    return citations


def _openai_hosted_search(query: str, max_results: int) -> ToolResult:
    """Uses OpenAI's server-side search (via litellm's web_search_options,
    on a search-dedicated model) rather than scraping anything. Unlike
    Tavily's discrete results array, this comes back as one synthesized
    answer -- citations are parsed out of it in one of two ways depending on
    the model (see _extract_markdown_citations above for why there are two):
    the older *-search-preview models attached them to the message as
    structured `annotations` ({type: "url_citation", url_citation: {url,
    title, ...}} entries); gpt-5-search-api, the current model (see
    _OPENAI_SEARCH_MODEL), instead embeds them as inline markdown links in
    the content text. Both are handled so a future model swap back to an
    annotations-based one doesn't silently regress. max_results isn't
    strictly honored here, since the model decides how many sources to
    cite, not this function."""
    try:
        # Through llm.complete(), not litellm directly -- that module is the
        # single provider seam (api-key routing, NUL scrubbing, and anything
        # added later like retries or caching all live there).
        text, response = complete(
            query,
            model=_OPENAI_SEARCH_MODEL,
            web_search_options={"search_context_size": "medium"},
        )
    except Exception as e:
        return ToolResult(success=False, error=f"OpenAI hosted search request failed: {e}")

    try:
        message = response.choices[0].message
        annotations = getattr(message, "annotations", None) or []
        results = [
            {
                "title": a.get("url_citation", {}).get("title", "") if isinstance(a, dict) else "",
                "url": a.get("url_citation", {}).get("url", "") if isinstance(a, dict) else "",
                "snippet": text,
            }
            for a in annotations[:max_results]
            if (isinstance(a, dict) and a.get("type") == "url_citation")
        ]
        if not results:
            results = [
                {"title": c["title"], "url": c["url"], "snippet": text}
                for c in _extract_markdown_citations(text)[:max_results]
            ]
        if not results:
            # No structured citations came back either way -- still real
            # search-grounded text, just without individually attributable
            # sources. Surface it as a single result rather than treating
            # it as a failure.
            results = [{"title": "OpenAI web search", "url": "", "snippet": text}]
    except Exception as e:
        return ToolResult(success=False, error=f"failed to parse OpenAI hosted search response: {e}")

    return ToolResult(success=True, output={"results": results})


def _duckduckgo_search(query: str, max_results: int, _retries: int = 1) -> ToolResult:
    """Fallback used only when no TAVILY_API_KEY is configured. Scrapes
    https://html.duckduckgo.com/html/ -- DDG's HTML markup and bot-detection
    behavior can change without notice, and repeated use gets rate-limited,
    which is exactly why Tavily is the preferred path above."""
    try:
        response = httpx.post(
            _DDG_SEARCH_URL,
            data={"q": query},
            headers=_DDG_HEADERS,
            timeout=10.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        return ToolResult(success=False, error=f"web search request failed: {e}")

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        if not soup.select(".result") and ("anomaly.js" in response.text or "challenge-form" in response.text):
            if _retries > 0:
                time.sleep(3)
                return _duckduckgo_search(query, max_results=max_results, _retries=_retries - 1)
            return ToolResult(
                success=False,
                error=f"{_DDG_BOT_DETECTION_ERROR} Retried once and still blocked — try again later.",
            )

        results = []
        for result in soup.select(".result"):
            link = result.select_one(".result__a")
            snippet = result.select_one(".result__snippet")
            if link is None or not link.get("href"):
                continue
            title = link.get_text(strip=True)
            url = _resolve_ddg_url(link["href"])
            if not title or not url:
                continue
            results.append(
                {"title": title, "url": url, "snippet": snippet.get_text(strip=True) if snippet else ""}
            )
            if len(results) >= max_results:
                break
    except Exception as e:
        return ToolResult(success=False, error=f"failed to parse search results: {e}")

    return ToolResult(success=True, output={"results": results})


class WebSearchArgs(BaseModel):
    """extra="forbid" here and on every other first-party args model below:
    an argument this tool never reads is a misunderstanding on the model's
    part, and silently dropping it hides that -- the classic version of this
    is a max_results typed as max_result and quietly ignored, which reads as
    "the tool ignored my limit" rather than "I named it wrong"."""

    model_config = ConfigDict(extra="forbid")

    query: str
    max_results: int = Field(default=5, ge=1, le=25)


class WebSearchTool(Tool):
    name = "web_search"
    args_model = WebSearchArgs
    action_type = ActionType.READ
    risk = Risk.LOW
    """Fetches public pages via a search provider. No effect on anything, but
    the SNIPPETS ARE UNTRUSTED INPUT -- they are wrapped as such (see
    sanitize.wrap_untrusted) precisely because a search result can carry a
    prompt injection. Reading attacker-influenceable text is a LOW-risk read;
    what the agent then proposes on the strength of it gets its own decision."""
    execution_safety = ExecutionSafety.IDEMPOTENT
    """A stateless query against a search API or an HTML endpoint. Nothing
    changes at either end, so a retry after any outcome is safe."""
    description = (
        "Searches the web and returns a list of titles, URLs, and snippets: "
        "{results: [{title, url, snippet}, ...]}. "
        "If the request could plausibly be about an internal document, policy, guide, other "
        "uploaded material, or a specific named person/candidate/project that might have a "
        "resume or profile on file, try knowledge_search first -- it's the authoritative "
        "source for that content and cheaper to check. Use this tool for anything genuinely "
        "public (current events, general facts, third-party information) or once "
        "knowledge_search has come back with no sources / low confidence for the same "
        "question. Arguments: query (str, required) -- short keyword-style queries work "
        "better than full sentences, e.g. \"Acme Robotics Q2 2026 earnings\" rather than "
        "\"what were Acme Robotics's earnings in the second quarter\"; max_results (int, "
        "optional, default 5)."
    )

    def run(self, query: str, max_results: int = 5) -> ToolResult:
        if not query or not query.strip():
            return ToolResult(success=False, error="query must be a non-empty string")

        if settings.tavily_api_key:
            result = _tavily_search(query, max_results)
        elif settings.openai_api_key:
            result = _openai_hosted_search(query, max_results)
        else:
            result = _duckduckgo_search(query, max_results)

        # Wrap only the fetched snippet text, not title/url -- those are
        # short, low-risk, and useful as-is for the model to read at a
        # glance. See sanitize.wrap_untrusted for why this exists at all.
        if result.success:
            for item in result.output.get("results", []):
                item["snippet"] = wrap_untrusted(item.get("snippet", ""), "web_search")
        return result
