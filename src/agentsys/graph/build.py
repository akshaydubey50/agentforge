from langgraph.graph import END, StateGraph

from agentsys.graph.nodes import (
    escalate_node,
    plan_node,
    route_after,
    route_after_select_subtask,
    route_entry,
    run_subtask_node,
    select_subtask_node,
    synthesize_node,
)
from agentsys.graph.state import AgentState


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("select_subtask", select_subtask_node)
    graph.add_node("run_subtask", run_subtask_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("synthesize", synthesize_node)

    graph.set_conditional_entry_point(
        route_entry, {"plan": "plan", "select_subtask": "select_subtask"}
    )

    graph.add_conditional_edges(
        "plan", route_after, {"escalate": "escalate", "select_subtask": "select_subtask"}
    )
    # route_after_select_subtask fans out to one Send("run_subtask", ...) per
    # ready subtask in the wave when route == "execute"; the "run_subtask"
    # entry below is a fallback destination declaration for graph
    # compilation/visualization (Send bypasses it for actual dispatch), and
    # synthesize/escalate stay plain string-routed exactly as before.
    graph.add_conditional_edges(
        "select_subtask",
        route_after_select_subtask,
        {"synthesize": "synthesize", "escalate": "escalate", "execute": "run_subtask"},
    )
    # Unconditional: every parallel run_subtask branch converges back here.
    # LangGraph's superstep model invokes select_subtask_node exactly once
    # per wave regardless of how many run_subtask branches fed into it --
    # that convergence is the barrier, not anything explicit in this file.
    graph.add_edge("run_subtask", "select_subtask")

    graph.add_edge("escalate", END)
    graph.add_edge("synthesize", END)

    return graph.compile()
