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

    try:
        from agentsys.tools.code_execution import CodeExecutionTool

        registry.register(CodeExecutionTool())
    except ImportError:
        logger.warning("code_execution tool not available")

    return registry


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry
