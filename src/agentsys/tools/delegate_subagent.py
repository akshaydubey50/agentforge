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

from pydantic import BaseModel, ConfigDict, Field

from agentsys import cost
from agentsys.config import settings
from agentsys.db.models import SubAgentRun, SubAgentRunStatus
from agentsys.db.session import get_session
from agentsys.execution import ExecutionSafety, run_with_retry
from agentsys.graph.prompts import SUBAGENT_STEP_PROMPT
from agentsys.graph.schemas import SubAgentStep
from agentsys.graph.tracing import span
from agentsys.llm import structured_complete
from agentsys.policy import ActionType, PolicyDecisionType, Risk, decide
from agentsys.tools.base import Tool, ToolResult, ToolValidationError, validated_kwargs


class DelegateSubagentArgs(BaseModel):
    """goal is the only argument the model owns. task_id/subtask_id/depth are
    injected -- depth especially: it is the recursion bound, so a proposal that
    could set it could delegate its way past settings.max_delegation_depth."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(description="What the sub-agent should accomplish, usually the subtask description restated.")


class DelegateSubagentTool(Tool):
    name = "delegate_subagent"
    args_model = DelegateSubagentArgs
    action_type = ActionType.READ
    risk = Risk.MEDIUM
    """Delegating has no effect of its own: the sub-agent's own tool calls are
    each evaluated by policy inside its loop (see run() below), so gating the
    delegation as well would ask for approval twice for one action and once for
    none. MEDIUM rather than LOW because it spends real budget and its inner
    calls are chosen from a wider set than any single step."""
    execution_safety = ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT
    """Never repeated automatically. A delegation is a whole nested loop with
    its own model spend and its own tool calls; replaying it after an ambiguous
    outcome re-runs everything it already did, and none of that is visible from
    out here."""
    description = (
        "Hands this subtask's goal to a sub-agent that can make several tool calls in "
        "sequence, inspecting each result before deciding what to do next -- unlike a normal "
        "tool choice, which is exactly one call. Use this ONLY when the subtask genuinely "
        "requires investigating one thing, seeing the result, and deciding what to do next "
        "based on it -- e.g. look up which vendor a company uses, THEN query that specific "
        "vendor's pricing (the second query can't be written until the first result is known). "
        "Do NOT use this when a single tool call already suffices -- "
        "that's slower and costs more for no benefit. Arguments: goal (str, required) -- what "
        "the sub-agent should accomplish, usually just the subtask description restated, e.g. "
        "goal=\"Find which cloud provider Acme Robotics uses, then look up that provider's "
        "current pricing for the relevant tier\". Do "
        "not pass task_id, subtask_id, or depth -- they're filled in automatically."
    )

    def run(self, goal: str, task_id: str, subtask_id: str, depth: int = 1) -> ToolResult:
        # Both imported inside run() for the same reason get_registry is: this
        # tool is registered BY the registry and its calls are dispatched BY
        # the graph, so either at module scope would close an import cycle.
        from agentsys.graph.nodes import _injected_kwargs, _task_owner_id
        from agentsys.tools.registry import get_registry

        # Looked up once for the whole loop rather than per step: it is the
        # same task throughout, and policy.decide is called on every step.
        owner_id = _task_owner_id(task_id)

        with get_session() as session:
            run_row = SubAgentRun(task_id=task_id, subtask_id=subtask_id, depth=depth, goal=goal)
            session.add(run_row)
            session.commit()
            session.refresh(run_row)
            run_id = run_row.id

        registry = get_registry()
        can_delegate_further = depth < settings.max_delegation_depth
        tool_descriptions = registry.describe(
            exclude=frozenset() if can_delegate_further else frozenset({self.name})
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
                decision, completion = structured_complete(prompt, SubAgentStep, model=settings.llm_model)
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

            # Same validation gate and the same plumbing table as the main
            # loop's _execute_subtask -- this is the second and only other
            # place an LLM-proposed call becomes real kwargs, and it had the
            # same `except: kwargs = {}` hole. Injected values go on after
            # validation, so a sub-agent cannot set its own depth and delegate
            # its way past settings.max_delegation_depth.
            try:
                kwargs = validated_kwargs(
                    registry.get(tool_name),
                    decision.tool_input_json,
                    defaults={"goal": goal} if tool_name == self.name else None,
                    injected=_injected_kwargs(tool_name, task_id, subtask_id, depth=depth + 1),
                )
            except ToolValidationError as exc:
                # Rejected, not run. The sub-agent sees why and can fix the
                # arguments on its next step.
                step_lines.append(f"{step_num}. {exc}")
                continue

            # THE SAME POLICY BOUNDARY AS THE MAIN LOOP, which this seam did
            # not have. Phase 1 closed the validation hole in both execution
            # seams and left the approval gate in only one: _execute_subtask
            # checked needs_approval, this loop checked nothing, so a
            # sub-agent that proposed code_execution simply ran it. Approval
            # was bypassable by delegating.
            #
            # A sub-agent cannot ask for a human. It runs synchronously inside
            # one tool call, so there is no point at which it can park a
            # tool_approval escalation and be resumed -- pausing it would mean
            # suspending a running tool mid-call, which is a materially bigger
            # change than Phase 2 (see docs/ARCHITECTURE_AUDIT.md). So its
            # enforcement is REFUSE, for REQUIRE_APPROVAL and DENY alike: the
            # step does not happen, the sub-agent is told why, and it can
            # finish with what it has or take a route that needs no approval.
            #
            # That is deliberately stricter than the main loop. A gated action
            # is still reachable -- the parent agent can propose it directly
            # and get a human -- it just cannot be reached from inside a
            # sub-agent, where nobody would be asked.
            # Named policy_decision, not decision: `decision` in this loop is
            # the model's own SubAgentStep, and shadowing it here would be a
            # trap for the next person to add a line after this block.
            policy_decision = decide(registry.get(tool_name), kwargs, user_id=owner_id)
            if policy_decision.decision is not PolicyDecisionType.ALLOW:
                step_lines.append(
                    f"{step_num}. refused '{tool_name}' -- {policy_decision.reason}. A sub-agent cannot "
                    f"obtain human approval; if this action is required, finish and report that "
                    f"it needs to be done as its own approved step."
                )
                continue

            # THE SAME EXECUTION SAFETY AS THE MAIN LOOP (Phase 3), which this
            # seam also did not have: it called run() bare, so a transient
            # failure ended the step and the TypeError guard was a second copy
            # of _run_tool_call's.
            #
            # run_with_retry, not execute_tool: the ledger and its dedupe are
            # for EFFECTS, and a sub-agent cannot produce one. Everything above
            # a READ is REQUIRE_APPROVAL, and a sub-agent refuses anything that
            # is not ALLOW (just above), so every call reaching this line is a
            # read -- the one class that is deliberately never deduped (see
            # execution.execute_tool). If that ever loosens, this must become
            # execute_tool.
            attempt = run_with_retry(registry.get(tool_name), kwargs)
            result = attempt.result

            summary = json.dumps(result.output) if result.success else f"FAILED: {result.error}"
            retried = f" (after {attempt.attempts} attempts)" if attempt.attempts > 1 else ""
            step_lines.append(f"{step_num}. called {tool_name}{retried} -> {summary[:300]}")

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
