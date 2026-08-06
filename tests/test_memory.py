import uuid

from agentsys.db.session import init_db
from agentsys.memory import long_term, short_term


def setup_module() -> None:
    init_db()


def test_short_term_memory_roundtrip_and_clear():
    task_id = f"test-{uuid.uuid4()}"
    short_term.set_value(task_id, "step_1_result", {"found": "the answer"})
    short_term.set_value(task_id, "step_2_result", "plain string value")

    assert short_term.get_value(task_id, "step_1_result") == {"found": "the answer"}
    all_values = short_term.get_all(task_id)
    assert all_values["step_1_result"] == {"found": "the answer"}
    assert all_values["step_2_result"] == "plain string value"

    short_term.clear(task_id)
    assert short_term.get_all(task_id) == {}


def test_long_term_memory_retrieval_ranks_by_similarity_and_importance():
    marker = uuid.uuid4().hex[:8]
    low_importance_id = long_term.add_memory(
        f"The user {marker} prefers dark roast coffee in the morning.",
        kind="preference",
        importance=1,
    )
    high_importance_id = long_term.add_memory(
        f"The user {marker} prefers dark roast coffee and this is critical to remember.",
        kind="preference",
        importance=5,
    )
    unrelated_id = long_term.add_memory(
        f"Unrelated fact {marker}: the database migration guide requires a rollback plan.",
        kind="fact",
        importance=5,
    )

    results = long_term.retrieve_relevant(f"What coffee does the user {marker} like?", k=2)

    result_ids = {r.id for r in results}
    assert high_importance_id in result_ids
    assert unrelated_id not in result_ids

    for r in results:
        assert r.similarity > 0.3
