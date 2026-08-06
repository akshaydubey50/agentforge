from typing import TypedDict


class AgentState(TypedDict, total=False):
    """task_id is the only thing that must survive a process boundary (e.g. a
    human approval resuming a paused task from a different worker), so it's
    the only piece backed by Postgres as ground truth. `route` and
    `current_subtask_id` are pure in-memory control-flow scratch that
    LangGraph threads between nodes within a single graph.invoke() call —
    they don't need Redis because they never need to outlive one invocation."""

    task_id: str
    route: str
    current_subtask_id: str
