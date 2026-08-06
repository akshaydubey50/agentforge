import pytest

from agentsys.tools.web_search import WebSearchTool

_EXTERNAL_FLAKINESS_MARKERS = ("bot-detection challenge", "timed out", "timeout")


def _skip_if_rate_limited(result):
    """DuckDuckGo's scrape-target rate limiting and transient network timeouts
    are external-service constraints the tool already retries once (for bot
    detection) and reports clearly on — not something a test re-run can fix,
    so we skip rather than fail the suite on them. Any OTHER kind of failure
    still fails the test for real."""
    if not result.success and result.error and any(m in result.error.lower() for m in _EXTERNAL_FLAKINESS_MARKERS):
        pytest.skip(f"DuckDuckGo was unreachable/rate-limited this test run: {result.error}")


def test_search_returns_real_results():
    tool = WebSearchTool()
    result = tool.run(query="Python programming language", max_results=5)
    _skip_if_rate_limited(result)

    assert result.success, result.error
    results = result.output["results"]
    assert len(results) > 0
    assert len(results) <= 5

    for item in results:
        assert item["title"].strip()
        assert item["url"].strip()
        assert item["url"].startswith("http")
        assert isinstance(item["snippet"], str)


def test_search_respects_max_results():
    tool = WebSearchTool()
    result = tool.run(query="open source software", max_results=2)
    _skip_if_rate_limited(result)

    assert result.success, result.error
    assert len(result.output["results"]) <= 2


def test_search_nonsense_query_does_not_crash():
    tool = WebSearchTool()
    result = tool.run(query="asdkfjhaslkdjfhalskdjfhqwoieurpoiqwuerpoiasdf9878q3wr", max_results=5)

    assert isinstance(result.success, bool)
    if not result.success:
        assert result.error


def test_search_empty_query_is_handled_gracefully():
    tool = WebSearchTool()
    result = tool.run(query="", max_results=5)

    assert result.success is False
    assert result.error
