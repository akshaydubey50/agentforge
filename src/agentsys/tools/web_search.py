"""Web search tool backed by DuckDuckGo's HTML endpoint.

There's no budget for a paid search API (Tavily/Serper/Bing) on this portfolio
project, so this scrapes https://html.duckduckgo.com/html/ instead. That's a
deliberate tradeoff, not an oversight: DDG's HTML markup and bot-detection
behavior can change without notice, so a production system should swap this
implementation for a paid API to get reliable, rate-limit-free results.
"""

import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from agentsys.tools.base import Tool, ToolResult

_SEARCH_URL = "https://html.duckduckgo.com/html/"
_BOT_DETECTION_ERROR = (
    "DuckDuckGo returned a bot-detection challenge instead of results "
    "(likely rate-limited by repeated requests)."
)

_HEADERS = {
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


def _resolve_url(href: str) -> str:
    """DDG sometimes wraps result links in a /l/?uddg=<encoded target> redirect."""
    if href.startswith("//duckduckgo.com/l/") or href.startswith("https://duckduckgo.com/l/"):
        query = parse_qs(urlparse(href).query)
        target = query.get("uddg")
        if target:
            return unquote(target[0])
    return href


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Searches the web and returns a list of titles, URLs, and snippets. "
        "Arguments: query (str, required), max_results (int, optional, default 5)."
    )

    def run(self, query: str, max_results: int = 5, _retries: int = 1) -> ToolResult:
        if not query or not query.strip():
            return ToolResult(success=False, error="query must be a non-empty string")

        try:
            response = httpx.post(
                _SEARCH_URL,
                data={"q": query},
                headers=_HEADERS,
                timeout=10.0,
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"web search request failed: {e}")

        try:
            soup = BeautifulSoup(response.text, "html.parser")

            if not soup.select(".result") and (
                "anomaly.js" in response.text or "challenge-form" in response.text
            ):
                if _retries > 0:
                    time.sleep(3)
                    return self.run(query, max_results=max_results, _retries=_retries - 1)
                return ToolResult(
                    success=False,
                    error=f"{_BOT_DETECTION_ERROR} Retried once and still blocked — try again later.",
                )

            results = []
            for result in soup.select(".result"):
                link = result.select_one(".result__a")
                snippet = result.select_one(".result__snippet")
                if link is None or not link.get("href"):
                    continue
                title = link.get_text(strip=True)
                url = _resolve_url(link["href"])
                if not title or not url:
                    continue
                results.append(
                    {
                        "title": title,
                        "url": url,
                        "snippet": snippet.get_text(strip=True) if snippet else "",
                    }
                )
                if len(results) >= max_results:
                    break
        except Exception as e:
            return ToolResult(success=False, error=f"failed to parse search results: {e}")

        return ToolResult(success=True, output={"results": results})
