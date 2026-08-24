from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    success: bool
    output: dict[str, Any] = {}
    error: str | None = None


class Tool(ABC):
    name: str
    description: str
    """The ONLY thing the specialist LLM sees when deciding whether to use this
    tool and how to call it -- there is no separate docs page it can consult.
    Every first-party tool's description follows the same convention (see
    db_query.py/code_execution.py/etc. for real examples), and new tools
    should too:
      1. What it does, in one sentence.
      2. Arguments, with type/required-vs-optional -- exactly as they must
         appear in tool_input_json.
      3. A concrete example call (a real query/code snippet/JSON args
         object), not just the abstract shape. This is the single highest-
         leverage addition: several of this project's logged bugs
         (README's "Real bugs found") were the model constructing plausible
         but wrong arguments because the description described the shape
         without ever showing one filled in.
      4. The output shape, if a caller would need to know it to use the
         result in a later step.
      5. Any non-obvious gotcha or constraint (a required format, a row/size
         cap, what makes a call fail) -- state it here rather than letting
         the model discover it by trial and error, which costs a real retry
         cycle every time.
    """

    requires_approval: bool = False
    """Class-level default for needs_approval() below -- True for a tool
    that's ALWAYS side-effecting regardless of arguments (e.g.
    code_execution, which executes arbitrary code on every call). A tool
    where only some actions are side-effecting (e.g. file_io's write vs.
    read/list) should leave this False and override needs_approval instead."""

    @abstractmethod
    def run(self, **kwargs) -> ToolResult: ...

    def needs_approval(self, kwargs: dict) -> bool:
        """Whether THIS specific call (about to be made with these exact
        kwargs) must be gated behind human approval before running -- see
        graph/nodes.py's _execute_subtask, which checks this right before
        registry.get(tool_name).run(**kwargs). Defaults to the class-level
        requires_approval flag; override for a tool where only some actions
        are side-effecting."""
        return self.requires_approval


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool '{name}'. Registered: {list(self._tools)}")
        return self._tools[name]

    def list_tools(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description} for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)
