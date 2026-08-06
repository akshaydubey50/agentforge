from langgraph.graph import END, StateGraph

from agentsys.graph.nodes import (
    escalate_node,
    execute_node,
    plan_node,
    review_node,
    route_after,
    route_entry,
    select_subtask_node,
    synthesize_node,
)
from agentsys.graph.state import AgentState


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("select_subtask", select_subtask_node)
    graph.add_node("execute", execute_node)
    graph.add_node("review", review_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("synthesize", synthesize_node)

    graph.set_conditional_entry_point(
        route_entry, {"plan": "plan", "select_subtask": "select_subtask"}
    )

    graph.add_conditional_edges(
        "plan", route_after, {"escalate": "escalate", "select_subtask": "select_subtask"}
    )
    graph.add_conditional_edges(
        "select_subtask",
        route_after,
        {"synthesize": "synthesize", "escalate": "escalate", "execute": "execute"},
    )
    graph.add_edge("execute", "review")
    graph.add_conditional_edges(
        "review",
        route_after,
        {"select_subtask": "select_subtask", "execute": "execute", "escalate": "escalate"},
    )

    graph.add_edge("escalate", END)
    graph.add_edge("synthesize", END)

    return graph.compile()
