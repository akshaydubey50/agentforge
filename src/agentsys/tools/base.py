import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ValidationError

from agentsys.policy import ActionType, Risk


class ToolResult(BaseModel):
    success: bool
    output: dict[str, Any] = {}
    error: str | None = None


class ToolValidationError(ValueError):
    """A proposed tool call that deterministic validation refused.

    Deliberately NOT a failed ToolResult: a ToolResult(success=False) means the
    tool ran and something went wrong out there, while this means nothing ran
    and nothing will until the arguments change. The agent loop needs to tell
    those apart -- a runtime failure is worth a different tool or a different
    approach, a validation failure is worth the SAME call with corrected
    arguments. Rendered field-addressed ("tool=x field=y reason=z") for the
    same reason every tool description carries a concrete example: the model
    fixes what it can see, and "invalid arguments" is not something it can see.
    """

    def __init__(self, tool_name: str, reason: str, field: str | None = None) -> None:
        self.tool_name = tool_name
        self.field = field
        self.reason = reason
        located = f" field={field}" if field else ""
        super().__init__(f"ToolValidationError: tool={tool_name}{located} reason={reason}")

    @classmethod
    def from_pydantic(cls, tool_name: str, exc: ValidationError) -> "ToolValidationError":
        errors = exc.errors()
        first = errors[0]
        field = ".".join(str(part) for part in first["loc"]) or None
        reason = first["msg"]
        if len(errors) > 1:
            reason = f"{reason} (and {len(errors) - 1} more argument problem(s))"
        return cls(tool_name, reason, field)


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
         appear in tool_input_json. The generated JSON Schema (args_model
         below) now goes into the prompt beside this, so the prose half is
         for what a schema cannot say: which argument to reach for, what a
         good value looks like, what the format actually means.
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

    action_type: ActionType = ActionType.EXTERNAL_WRITE
    risk: Risk = Risk.HIGH
    """This tool's BASELINE classification, which is what policy.decide()
    starts from -- see policy.py for what the values mean and which rule each
    combination hits.

    The defaults are deliberately the fail-closed pair, not the harmless one.
    A tool that never declares its classification is gated behind human
    approval, because "the author forgot" and "this is safe" must not look the
    same to the policy engine. Every first-party tool in this package declares
    both explicitly; MCPTool, whose effects belong to a third party, keeps
    these defaults unless the operator declares otherwise in the server's
    config entry.

    Baseline, not verdict: a rule may raise a specific call above it based on
    that call's validated arguments (policy._effective), which is how file_io
    is a LOW-risk READ to list a directory and a MEDIUM-risk LOCAL_WRITE to
    write a file. Approval is NOT a static property here -- that was the old
    requires_approval bool this replaces (ARCHITECTURE_AUDIT 5.2)."""

    args_model: type[BaseModel] | None = None
    """The tool's argument contract: a pydantic model of exactly the arguments
    the MODEL is allowed to choose. The LLM proposes; this disposes.

    Runtime-owned values are deliberately NOT fields here -- task_id, user_id,
    subtask_id, depth, workspace paths and credentials are injected after
    validation (see validated_kwargs below and _injected_kwargs in
    graph/nodes.py), so a proposal naming one cannot influence it. Exposing
    them as fields merely to make schema generation uniform would hand the
    model the one set of arguments it must never control.

    The schema supplements `description`, it does not replace it: the prose
    still carries the when/why/gotchas and the worked example, and the schema
    carries the machine-checkable shape. Both go into the prompt (see
    ToolRegistry.describe), and the schema half is generated from this model
    rather than written out a second time in a prompt string, because a second
    copy is a copy that drifts.
    """

    @abstractmethod
    def run(self, **kwargs) -> ToolResult: ...

    def args_schema(self) -> dict | None:
        """JSON Schema for the arguments the model may choose, or None if this
        tool has no contract. Generated, never hand-maintained."""
        if self.args_model is None:
            return None
        schema = self.args_model.model_json_schema()
        # Drop what pydantic generates for humans rather than for the model:
        # the args model's class docstring, which is maintainer commentary
        # (mcp_servers/company_internal.py's note on docstrings becoming
        # model-facing description text applies to these too -- rationale in
        # the prompt competes with the actual capability), and the auto-titles
        # ("Max Results"), which only restate the property name. Field
        # descriptions are written FOR the model and stay.
        schema.pop("title", None)
        schema.pop("description", None)
        for prop in schema.get("properties", {}).values():
            if isinstance(prop, dict):
                prop.pop("title", None)
        return schema

    def validate_args(self, proposed: dict) -> dict:
        """Deterministic validation of one proposed argument dict. Returns the
        validated kwargs; raises ToolValidationError. Overridden by MCPTool,
        whose contract arrives as JSON Schema from a third party rather than as
        a local pydantic model."""
        if self.args_model is None:
            # Fail closed. A registered tool with no contract is a bug in the
            # tool, not a licence to call it with whatever the model produced.
            raise ToolValidationError(self.name, "tool declares no argument contract (args_model)")
        try:
            return self.args_model.model_validate(proposed).model_dump()
        except ValidationError as exc:
            raise ToolValidationError.from_pydantic(self.name, exc) from exc


def validated_kwargs(
    tool: Tool,
    tool_input_json: str | None,
    defaults: dict[str, Any] | None = None,
    injected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The one gate between an LLM's proposed call and a tool's run().

    Every caller that turns `tool_input_json` into kwargs goes through here
    (graph/nodes.py's _execute_subtask and the delegate_subagent loop), because
    both used to do `json.loads(...) except: kwargs = {}` -- which does not
    reject a malformed proposal, it silently calls the tool with NO arguments
    and lets defaults stand in for whatever the model meant.

    The two dicts either side of validation are the whole security model:
      defaults  -- values the runtime supplies but the model MAY override
                   (delegate_subagent's goal falls back to the subtask text)
      injected  -- values the runtime owns outright and the model may NOT set
                   (task_id, user_id, subtask_id, depth). Applied last, so a
                   proposal naming one is overwritten rather than honoured.
    """
    raw = tool_input_json if tool_input_json is not None else "{}"
    try:
        proposed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ToolValidationError(tool.name, f"tool_input_json is not valid JSON: {exc}") from exc
    if not isinstance(proposed, dict):
        raise ToolValidationError(
            tool.name,
            f"tool_input_json must be a JSON object, got {type(proposed).__name__}",
        )

    kwargs = tool.validate_args({**(defaults or {}), **proposed})
    kwargs.update(injected or {})
    return kwargs


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool '{name}'. Registered: {list(self._tools)}")
        return self._tools[name]

    def describe(self, exclude: frozenset[str] = frozenset()) -> str:
        """The tool catalogue exactly as the model reads it: each tool's prose
        description plus the machine-generated JSON Schema for its arguments.

        Two lines per tool, the second one labelled -- deliberately not one
        line, and deliberately not a bare schema dump. See MCPTool.__init__ for
        the observed failure when catalogue entries lose their structure.
        """
        lines: list[str] = []
        for tool in self._tools.values():
            if tool.name in exclude:
                continue
            lines.append(f"- {tool.name}: {tool.description}")
            schema = tool.args_schema()
            if schema:
                lines.append(f"  arguments (JSON Schema): {json.dumps(schema, sort_keys=True)}")
        return "\n".join(lines) or "(no tools available)"

    def list_tools(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description} for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)
