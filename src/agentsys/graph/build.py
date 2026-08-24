from langgraph.graph import END, StateGraph

from agentsys.graph.nodes import (
    agent_step_node,
    escalate_node,
    route_after,
    route_entry,
    sketch_node,
    synthesize_node,
)
from agentsys.graph.state import AgentState


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("sketch", sketch_node)
    graph.add_node("agent_step", agent_step_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("synthesize", synthesize_node)

    graph.set_conditional_entry_point(
        route_entry, {"sketch": "sketch", "agent_step": "agent_step"}
    )

    graph.add_conditional_edges(
        "sketch", route_after, {"escalate": "escalate", "agent_step": "agent_step"}
    )
    # agent_step_node runs one full "decide -> act -> review-and-retry" cycle
    # per invocation and reports where to go next via route: back to itself
    # for the next decision, to escalate (step budget exhausted, model tried
    # to finish before doing anything, or a subtask exhausted its own review
    # retries), or to synthesize (model declared the request done). This
    # self-loop is the entire control-flow story now -- there is no separate
    # scheduler node and no Send-based parallel fan-out (see nodes.py's
    # agent_step_node docstring and the plan doc for why that tradeoff is
    # deliberate: a continuous decide-next-step loop is inherently
    # sequential).
    graph.add_conditional_edges(
        "agent_step", route_after,
        {"agent_step": "agent_step", "escalate": "escalate", "synthesize": "synthesize"},
    )

    graph.add_edge("escalate", END)
    graph.add_edge("synthesize", END)

    return graph.compile()
