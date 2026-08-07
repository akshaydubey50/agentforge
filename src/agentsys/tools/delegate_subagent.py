"""Recursive sub-agent delegation. A subtask normally gets exactly one tool
call (see execute_node) -- this tool is the escape hatch: it hands the
subtask's goal to a bounded, isolated tool-use loop that can investigate,
see a result, and decide what to do next, instead of guessing everything up
front in one shot.

Modeled as a 5th Tool, not a new graph structure. execute_node's existing
dispatch (TypeError guard, ToolCall audit row, span() tracing) applies to
this exactly like every other tool -- the only new code is the loop below.
Recursion is bounded by settings.max_delegation_depth (default 1): a
sub-agent at the depth limit never sees "delegate_subagent" in its own
choices, so it cannot spawn a further sub-agent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from agentsys import cost
from agentsys.config import settings
from agentsys.db.models import SubAgentRun, SubAgentRunStatus
from agentsys.db.session import get_session
from agentsys.graph.prompts import SUBAGENT_STEP_PROMPT
from agentsys.graph.schemas import SubAgentStep
from agentsys.graph.tracing import span
from agentsys.llm import get_client
from agentsys.tools.base import Tool, ToolResult


class DelegateSubagentTool(Tool):
    name = "delegate_subagent"
    description = (
        "Hands this subtask's goal to a sub-agent that can make several tool calls in "
        "sequence, inspecting each result before deciding what to do next -- unlike a normal "
        "tool choice, which is exactly one call. Use this ONLY when the subtask genuinely "
        "requires investigating one thing, seeing the result, and deciding what to do next "
        "based on it (e.g. look something up, then decide what follow-up query or computation "
        "the result implies). Do NOT use this when a single tool call already suffices -- "
        "that's slower and costs more for no benefit. Arguments: goal (str, required) -- what "
        "the sub-agent should accomplish, usually just the subtask description restated. Do "
        "not pass task_id, subtask_id, or depth -- they're filled in automatically."
    )

    def run(self, goal: str, task_id: str, subtask_id: str, depth: int = 1) -> ToolResult:
        from agentsys.tools.registry import get_registry

        with get_session() as session:
            run_row = SubAgentRun(task_id=task_id, subtask_id=subtask_id, depth=depth, goal=goal)
            session.add(run_row)
            session.commit()
            session.refresh(run_row)
            run_id = run_row.id

        registry = get_registry()
        can_delegate_further = depth < settings.max_delegation_depth
        available_tools = [
            t for t in registry.list_tools() if can_delegate_further or t["name"] != self.name
        ]
        tool_descriptions = (
            "\n".join(f"- {t['name']}: {t['description']}" for t in available_tools)
            or "(no tools available)"
        )

        step_lines: list[str] = []
        final_answer: str | None = None

        for step_num in range(1, settings.max_subagent_steps + 1):
            prompt = SUBAGENT_STEP_PROMPT.format(
                goal=goal,
                tool_descriptions=tool_descriptions,
                step_history="\n".join(step_lines) if step_lines else "(nothing yet)",
                steps_remaining=settings.max_subagent_steps - step_num + 1,
            )

            with span(
                task_id, "subagent_tool_call", f"subagent_step_{step_num}",
                subtask_id=subtask_id, input={"goal": goal, "step": step_num},
            ) as s:
                completion = get_client().beta.chat.completions.parse(
                    model=settings.llm_model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format=SubAgentStep,
                )
                decision = completion.choices[0].message.parsed
                s["output"] = decision.model_dump()
            cost.record_llm_call(task_id, subtask_id, "subagent_step", completion)

            if decision.next_action == "finish":
                final_answer = decision.final_answer or ""
                break

            tool_name = decision.tool_name
            not_delegatable = tool_name == self.name and not can_delegate_further
            if not tool_name or tool_name not in registry.names() or not_delegatable:
                step_lines.append(f"{step_num}. tried '{tool_name}' -- not available, skipped")
                continue

            try:
                kwargs = json.loads(decision.tool_input_json or "{}")
            except json.JSONDecodeError:
                kwargs = {}
            if tool_name == "file_io":
                kwargs.setdefault("task_id", task_id)
            elif tool_name == self.name:
                kwargs.setdefault("task_id", task_id)
                kwargs.setdefault("subtask_id", subtask_id)
                kwargs.setdefault("depth", depth + 1)

            try:
                result = registry.get(tool_name).run(**kwargs)
            except TypeError as exc:
                result = ToolResult(success=False, error=f"invalid arguments for {tool_name}: {exc}")

            outcome = json.dumps(result.output) if result.success else f"FAILED: {result.error}"
            step_lines.append(f"{step_num}. called {tool_name} -> {outcome[:300]}")

        with get_session() as session:
            run_row = session.get(SubAgentRun, run_id)
            run_row.status = SubAgentRunStatus.DONE if final_answer is not None else SubAgentRunStatus.FAILED
            run_row.output = final_answer
            run_row.completed_at = datetime.now(timezone.utc)
            session.add(run_row)
            session.commit()

        if final_answer is None:
            return ToolResult(
                success=False,
                error=f"sub-agent exhausted {settings.max_subagent_steps} steps without finishing",
                output={"steps": step_lines},
            )
        return ToolResult(success=True, output={"answer": final_answer, "steps": step_lines})
