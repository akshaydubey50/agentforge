from agentsys.graph.build import build_graph

_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_task(task_id: str) -> None:
    """Safe to call repeatedly for the same task_id: a fresh run reads
    current Postgres state (route_entry decides plan vs. resume), so this is
    exactly how a human-approved escalation resumes execution — by calling
    this again, not by restoring some in-memory checkpoint."""
    graph = get_graph()
    graph.invoke({"task_id": task_id}, config={"recursion_limit": 100})
