import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    """task_id is the only thing that must survive a process boundary (e.g. a
    human approval resuming a paused task from a different worker), so it's
    the only piece backed by Postgres as ground truth. `route` and
    `current_subtask_id` are pure in-memory control-flow scratch that
    LangGraph threads between nodes within a single graph.invoke() call —
    they don't need Redis because they never need to outlive one invocation.

    `ready_subtask_ids` is written once by select_subtask_node, before any
    parallel branch exists, so it's a plain field -- no concurrent-write risk.

    `escalated_subtask_ids` is the one field multiple parallel run_subtask
    branches (dispatched via Send, see graph/build.py) can genuinely write to
    in the SAME superstep. It MUST use a reducer (operator.add, i.e. list
    concatenation): LangGraph's default channel for a plain field only
    accepts one write per step and raises InvalidUpdateError the instant two
    branches disagree -- which happens the moment one subtask in a wave
    passes review while a sibling escalates. Every other per-branch value
    (route, current_subtask_id, tool_success) is deliberately kept as a local
    Python variable inside run_subtask_node's own retry loop rather than
    written to graph state at all, which is what avoids needing a reducer for
    those too."""

    task_id: str
    route: str
    current_subtask_id: str
    ready_subtask_ids: list[str]
    escalated_subtask_ids: Annotated[list[str], operator.add]
