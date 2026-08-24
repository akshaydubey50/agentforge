from typing import TypedDict


class AgentState(TypedDict, total=False):
    """task_id is the only thing that must survive a process boundary (e.g. a
    human approval resuming a paused task from a different worker), so it's
    the only piece backed by Postgres as ground truth. `route` is pure
    in-memory control-flow scratch that LangGraph threads between nodes
    within a single graph.invoke() call.

    Execution is strictly sequential per task -- one agent_step_node
    invocation at a time, self-looping rather than fanning out via Send --
    so no field needs a reducer. Every per-step value that a parallel
    design would need Send-safety for (a current subtask id, an
    escalated-ids accumulator, a ready-ids list) is gone: agent_step_node
    creates and holds its subtask id as a local Python variable for the
    duration of one call, the same way its retry loop already treats
    route/tool_success."""

    task_id: str
    route: str
