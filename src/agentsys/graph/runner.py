from agentsys.graph.build import build_graph
from agentsys.task_claims import task_claim

_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_task(task_id: str) -> bool:
    """Safe to call repeatedly for the same task_id: a fresh run reads
    current Postgres state (route_entry decides plan vs. resume), so this is
    exactly how a human-approved escalation resumes execution — by calling
    this again, not by restoring some in-memory checkpoint.

    First thing on every (re)invocation: reconcile any subtask a previously
    crashed run left stranded in an in-flight state, so a resume starts from a
    clean, consistent picture rather than stepping around a zombie RUNNING row
    (see recovery.reconcile_orphaned_subtasks)."""
    import logging

    from agentsys import otel
    from agentsys.recovery import reconcile_orphaned_subtasks

    logger = logging.getLogger(__name__)
    with task_claim(task_id) as claimed:
        if not claimed:
            logger.info("task %s is already claimed by another worker; skipping duplicate delivery", task_id)
            return False

        reconcile_orphaned_subtasks(task_id)
        graph = get_graph()
        # One root span per invocation, so every node span underneath it lands in
        # a single trace instead of arriving as orphans. A resume after a human
        # decision is a fresh invocation and therefore a fresh trace -- which is
        # honest: it really is a separate run, often on a different worker.
        with otel.span("agent_task", span_type="agent", task_id=task_id) as root:
            if root is not None:
                root.set_attribute("openinference.span.kind", "AGENT")
            graph.invoke({"task_id": task_id}, config={"recursion_limit": 100})
    return True
