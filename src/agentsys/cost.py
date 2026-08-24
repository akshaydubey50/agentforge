"""Records USD cost for every LLM call. One function, called from every
LLM call site (graph/nodes.py's five, plus tools/delegate_subagent.py's
step loop) right after the call succeeds -- this is what /v1/analytics'
cost figures are aggregated from, the same on-the-fly-aggregation approach
already used there for tool_stats."""

import litellm

from agentsys.db.models import LlmCall
from agentsys.db.session import get_session


def record_llm_call(task_id: str, subtask_id: str | None, purpose: str, completion) -> None:
    model = completion.model
    usage = completion.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0

    # litellm.completion_cost() covers every provider/model litellm knows
    # pricing for -- unknown/unpriced models (e.g. a brand-new model swapped
    # in via .env before litellm's pricing table is updated) still get
    # recorded for token counts, just with $0 cost rather than a crash.
    try:
        cost_usd = litellm.completion_cost(completion_response=completion)
    except Exception:
        cost_usd = 0.0

    with get_session() as session:
        session.add(
            LlmCall(
                task_id=task_id,
                subtask_id=subtask_id,
                purpose=purpose,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
            )
        )
        session.commit()
