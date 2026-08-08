import logging

from agentsys.tools.base import ToolRegistry

logger = logging.getLogger(__name__)

_registry: ToolRegistry | None = None


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()

    try:
        from agentsys.tools.web_search import WebSearchTool

        registry.register(WebSearchTool())
    except ImportError:
        logger.warning("web_search tool not available")

    try:
        from agentsys.tools.file_io import FileIOTool

        registry.register(FileIOTool())
    except ImportError:
        logger.warning("file_io tool not available")

    try:
        from agentsys.tools.db_query import DbQueryTool

        registry.register(DbQueryTool())
    except ImportError:
        logger.warning("db_query tool not available")

    from agentsys.config import settings

    if settings.enable_code_execution:
        try:
            from agentsys.tools.code_execution import CodeExecutionTool

            registry.register(CodeExecutionTool())
        except ImportError:
            logger.warning("code_execution tool not available")
    else:
        logger.info("code_execution tool disabled via ENABLE_CODE_EXECUTION=false (no Docker socket in this env)")

    try:
        from agentsys.tools.delegate_subagent import DelegateSubagentTool

        registry.register(DelegateSubagentTool())
    except ImportError:
        logger.warning("delegate_subagent tool not available")

    try:
        from agentsys.tools.knowledge_search import KnowledgeSearchTool

        registry.register(KnowledgeSearchTool())
    except ImportError:
        logger.warning("knowledge_search tool not available")

    _register_mcp_tools(registry)

    return registry


def _register_mcp_tools(registry: ToolRegistry) -> None:
    """Discovers and registers tools from every configured external MCP server.

    Each server is isolated in its own try/except on purpose: a third party's
    server being down, slow, or misconfigured must degrade to "those tools are
    unavailable this run" and never prevent AgentForge from starting. That is
    the difference between a plugin system and a hard dependency -- the first
    party tools above already degrade this way on ImportError, and an external
    process over a socket is strictly more likely to fail than a local import.
    """
    from agentsys.config import settings

    for server_config in settings.mcp_servers:
        server_name = server_config.get("name", "<unnamed>")
        try:
            from agentsys.tools.mcp_tool import discover_mcp_tools

            tools = discover_mcp_tools(server_config)
        except Exception as exc:
            logger.warning("MCP server '%s' unavailable, skipping its tools: %s", server_name, exc)
            continue

        for tool in tools:
            registry.register(tool)
        logger.info(
            "MCP server '%s' contributed %d tool(s): %s",
            server_name,
            len(tools),
            [t.name for t in tools],
        )


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry
