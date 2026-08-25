"""Model Context Protocol (MCP) client support -- the plugin backbone.

This is what turns AgentForge from "an agent with five hardcoded tools" into a
platform: any MCP server, written by anyone in any language, can be listed in
settings.mcp_servers and its tools become callable by the specialist agent with
no code change here. Discovery happens at registry-build time (list_tools over
the real protocol), so AgentForge does not need to know a server's tools in
advance -- it asks.

Design: an MCP tool is modelled as an ordinary Tool subclass, not as a parallel
execution path. That is deliberate. Because MCPTool implements the same
Tool.run() -> ToolResult contract as db_query et al., every existing mechanism
applies to it for free and unmodified: TraceSpan/ToolCall auditing, the
reviewer's reject-and-retry loop, escalation, cost accounting, and the
delegate_subagent tool loop. Nothing downstream needs to know a tool came from
MCP. (Same principle as delegate_subagent: new capability, existing shape.)

Connection lifecycle -- a real, deliberate tradeoff:
    Each run() call spawns the server subprocess, handshakes, calls the one
    tool, and tears the process down. That is slower than holding a persistent
    session (subprocess spawn + MCP handshake on every call, tens to hundreds
    of ms), and it is chosen anyway because it is thread-safe by construction:
    there is no shared mutable connection state at all. That matters directly
    here -- multiple Celery workers (or, if this codebase ever reintroduces
    controlled parallel step execution -- see graph/nodes.py's agent_step_node
    docstring) can call into the same MCPTool instance concurrently. A
    pooled/persistent session would need a per-thread or locked connection
    manager, and an MCP
    ClientSession additionally cannot be reused across separate asyncio.run()
    calls (each opens a fresh event loop; the session's streams are bound to
    the loop that created them).

    The right future version is a dedicated background event-loop thread owning
    long-lived sessions, dispatched to via asyncio.run_coroutine_threadsafe.
    That is a real optimization and is NOT built here -- stated rather than
    hidden, consistent with how the rest of this codebase documents its limits.

The SDK is async-only, so every call bridges sync -> async via asyncio.run().
This is safe from AgentForge's synchronous nodes and worker; it would raise if
ever called from a thread that already has a running event loop, which is
detected explicitly below rather than failing obscurely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentsys.tools.base import Tool, ToolResult, ToolValidationError

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 30.0


def _server_params(server_config: dict) -> StdioServerParameters:
    """Builds stdio launch params, merging any configured env over the parent
    process environment rather than replacing it -- a bare env dict would drop
    PATH/SystemRoot and break the interpreter launch on some platforms."""
    env = dict(os.environ)
    env.update(server_config.get("env") or {})
    return StdioServerParameters(
        command=server_config["command"],
        args=server_config.get("args") or [],
        env=env,
        cwd=server_config.get("cwd"),
    )


def _example_value(spec: dict) -> Any:
    """Best-effort placeholder for one argument in an auto-generated example
    call -- a real value if the schema names one (default/examples/enum),
    otherwise a type-appropriate stand-in so the example JSON at least has
    the right shape for the model to pattern-match against."""
    if spec.get("default") is not None:
        return spec["default"]
    if spec.get("examples"):
        return spec["examples"][0]
    if spec.get("enum"):
        return spec["enum"][0]
    return {"string": "...", "number": 0, "integer": 0, "boolean": True, "array": [], "object": {}}.get(
        spec.get("type"), "..."
    )


def _describe_schema(input_schema: dict | None) -> str:
    """Renders a JSON Schema into the plain 'Arguments: name (type, required)'
    prose the other tool descriptions use, plus an auto-generated example
    call built from the same schema.

    This is not cosmetic. The specialist LLM constructs tool arguments from the
    description text alone, and two of this project's real logged bugs came from
    a tool description that failed to state how to call it. An MCP server's
    schema is machine-readable but the model reads descriptions, so it gets
    translated rather than dumped as raw JSON Schema -- and every first-party
    tool's description now includes a concrete example call for the same
    reason, so this generates one automatically rather than leaving
    third-party MCP tools as the one category of tool without one.
    """
    if not input_schema:
        return "Takes no arguments."
    props = input_schema.get("properties") or {}
    if not props:
        return "Takes no arguments."
    required = set(input_schema.get("required") or [])
    parts = []
    example: dict[str, Any] = {}
    for arg_name, spec in props.items():
        arg_type = spec.get("type", "any")
        req = "required" if arg_name in required else "optional"
        default = spec.get("default")
        default_txt = f", default {default!r}" if default is not None else ""
        desc = spec.get("description")
        desc_txt = f" -- {desc}" if desc else ""
        parts.append(f"{arg_name} ({arg_type}, {req}{default_txt}){desc_txt}")
        example[arg_name] = _example_value(spec)
    example_txt = f" Example call: {json.dumps(example)}." if example else ""
    return "Arguments: " + "; ".join(parts) + "." + example_txt


def _result_to_output(result: Any) -> dict:
    """Normalizes an MCP CallToolResult into this project's flat output dict.

    Handles the case observed against a real server: a tool returning a dict
    with no declared output_schema comes back with structured_content=None and
    the payload JSON-encoded inside a TextContent block, so falling back to
    parsing content text is the common path, not an edge case.
    """
    structured = getattr(result, "structured_content", None)
    if structured:
        return structured if isinstance(structured, dict) else {"result": structured}

    texts = [getattr(block, "text", None) for block in (result.content or [])]
    texts = [t for t in texts if t]
    if not texts:
        return {}

    joined = "\n".join(texts)
    try:
        parsed = json.loads(joined)
    except (json.JSONDecodeError, ValueError):
        return {"text": joined}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _error_text(result: Any) -> str:
    texts = [getattr(block, "text", None) for block in (result.content or [])]
    texts = [t for t in texts if t]
    return "\n".join(texts) if texts else "tool reported an error with no message"


class MCPTool(Tool):
    """One AgentForge tool bound to one tool on one external MCP server."""

    def __init__(self, server_name: str, server_config: dict, remote_tool: Any) -> None:
        self.server_name = server_name
        self.server_config = server_config
        self.remote_tool_name = remote_tool.name
        # The argument contract, straight from the server. An MCP tool already
        # ships machine-readable JSON Schema, so there is nothing to translate:
        # wrapping every discovered tool in a hand-written pydantic model would
        # mean writing code for tools this repo has never seen, which is the
        # opposite of what discovery is for. args_model stays None and
        # validate_args below checks against this instead -- same invariant
        # (nothing reaches run() unvalidated), different source of truth.
        self.input_schema = getattr(remote_tool, "input_schema", None) or {}
        # Namespaced so two servers exposing a "search" tool can coexist, and so
        # provenance is obvious in the trace explorer and analytics tables.
        self.name = f"mcp_{server_name}_{remote_tool.name}"
        # Collapse all whitespace to single spaces. An MCP description is
        # usually a Python docstring, so it arrives with newlines and source
        # indentation; _tool_descriptions() renders the tool list as one
        # "- name: description" line per tool, and a multi-line description
        # silently shatters that structure -- continuation lines read as
        # separate, unlabelled content. Observed live: the specialist skipped a
        # tool that was the only possible source for the answer and reached for
        # web_search/db_query instead, because the list it was reading was
        # malformed. Third-party servers will all have multi-line docstrings,
        # so normalizing here is a correctness requirement, not tidiness.
        base_desc = " ".join((remote_tool.description or "").split())
        base_desc = base_desc or f"{remote_tool.name} (via MCP)"
        self.description = (
            f"{base_desc} {_describe_schema(self.input_schema)} "
            f"[provided by the '{server_name}' MCP server]"
        )

    def args_schema(self) -> dict | None:
        return self.input_schema or None

    def validate_args(self, proposed: dict) -> dict:
        """Validated against the server's own advertised schema.

        Two honest limits, stated rather than papered over. A server that
        advertises no schema (or an empty one) gets no local check -- there is
        nothing to check against, and inventing constraints for a third party's
        tool would reject calls the server would have accepted. And a schema
        that doesn't set additionalProperties:false permits extra arguments,
        because that is what the server said it permits; the fail-closed choice
        belongs to whoever wrote the tool. In both cases the server validates
        again on its side, so this is the first of two checks, not the only one.
        """
        if not self.input_schema:
            return dict(proposed)
        try:
            jsonschema.validate(proposed, self.input_schema)
        except jsonschema.ValidationError as exc:
            field = ".".join(str(part) for part in exc.absolute_path) or None
            raise ToolValidationError(self.name, exc.message, field) from exc
        except jsonschema.SchemaError as exc:
            # A malformed schema is the server's bug, but it must not be a
            # licence to call the tool unvalidated.
            raise ToolValidationError(self.name, f"server advertised an invalid schema: {exc.message}") from exc
        return dict(proposed)

    def run(self, **kwargs) -> ToolResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # no loop in this thread -- the expected, supported case
        else:
            return ToolResult(
                success=False,
                error=(
                    f"{self.name} cannot be called from a thread with a running event "
                    "loop; MCP calls bridge sync->async via asyncio.run()"
                ),
            )

        try:
            return asyncio.run(self._call(kwargs))
        except Exception as exc:
            # A dead/misbehaving external server must surface as a failed tool
            # call the reviewer can reject and retry, never as a crashed graph
            # run -- same reasoning as the TypeError guard in _execute_subtask.
            logger.warning("MCP call %s failed: %s", self.name, exc)
            return ToolResult(success=False, error=f"MCP call failed: {exc}")

    async def _call(self, arguments: dict) -> ToolResult:
        params = _server_params(self.server_config)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT_S)
                result = await session.call_tool(self.remote_tool_name, arguments)

        if getattr(result, "is_error", False):
            return ToolResult(success=False, error=_error_text(result))
        return ToolResult(success=True, output=_result_to_output(result))


async def _list_remote_tools(server_config: dict) -> list[Any]:
    params = _server_params(server_config)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT_S)
            listed = await session.list_tools()
            return list(listed.tools)


def discover_mcp_tools(server_config: dict) -> list[MCPTool]:
    """Connects to one configured MCP server and returns an MCPTool per tool it
    advertises. Raises on connection failure; the registry decides whether an
    unreachable server is fatal (it isn't -- see registry._build_registry)."""
    server_name = server_config["name"]
    remote_tools = asyncio.run(_list_remote_tools(server_config))
    return [MCPTool(server_name, server_config, t) for t in remote_tools]
