PLAN_PROMPT = """You are the supervisor of a small team of specialist agents. Break the user's \
request into an ordered list of concrete subtasks. Each subtask should be small enough that one \
tool call (or a short burst of reasoning) can complete it.

This is a ONE-SHOT plan: you cannot add subtasks later once execution starts. If the request \
needs multiple pieces of information (e.g. two time periods, two entities, a lookup AND a \
computation AND a write-up), every one of those needs its own subtask in THIS plan — don't defer \
part of the work to "a second subtask" you'll create afterward, because that will never happen \
and the final answer will end up fabricating whatever wasn't actually planned for.

Available tools your specialists can use (a subtask doesn't have to use a tool — pure reasoning \
subtasks like "summarize the findings" are fine too):
{tool_descriptions}

If earlier subtasks discovered facts or preferences from similar past tasks, they're listed here \
— use them to inform the plan, but don't force a subtask just because a memory exists:
{memory_context}

Request: {request}

Rate your confidence 1-5 that this plan will successfully complete the request using only the \
tools available. Be honest — a plan that depends on a tool you don't have, or that's too vague \
to execute, should score low."""


TOOL_SELECTION_PROMPT = """You are a specialist agent executing one subtask. Choose the best tool \
for this subtask (or "none" if it's pure reasoning that needs no tool) and construct its \
arguments.

Subtask: {subtask_description}

Available tools:
{tool_descriptions}

Context from earlier subtasks in this task (may be empty):
{prior_context}
{revision_feedback}
If you choose a real tool, tool_input_json must be a valid JSON object string with exactly the \
keyword arguments that tool expects. If no tool is needed, set tool_name to "none" and \
tool_input_json to "{{}}"."""


REASONING_ONLY_PROMPT = """You are a specialist agent completing a subtask with no external tool \
— just reasoning over what's already known.

Subtask: {subtask_description}

Context from earlier subtasks in this task (may be empty):
{prior_context}
{revision_feedback}
Produce the subtask's output directly."""


REVIEW_PROMPT = """You are the reviewer validating a specialist's completed subtask. Judge \
whether the output actually satisfies the subtask, not just whether it looks plausible.

Subtask: {subtask_description}

Tool used: {tool_used}
Tool call succeeded: {tool_success}

Specialist's output:
{output}

Score 1-5 and give a verdict:
- pass: the output genuinely satisfies the subtask
- reject: the output is wrong, incomplete, or the tool call failed in a way a retry could fix \
  (e.g. a transient error, a malformed query) — explain what the retry should do differently
- escalate: this isn't something a retry can fix (e.g. the tool fundamentally can't do this, or \
  the subtask itself seems ambiguous/risky enough to need a human) — explain why"""


SYNTHESIS_PROMPT = """You are the supervisor. All subtasks for this request are done (or were \
skipped/failed with the task tolerating that). Write the final answer to the user's original \
request, using ONLY the subtask outputs below. Be direct — don't narrate the process, just answer.

Do not invent, estimate, or fill in any data point that isn't actually present in a subtask \
output below — not a number, not a fact, nothing. If the subtask outputs don't fully cover what \
the request asked for, say exactly what's missing instead of making it up. An incomplete but \
honest answer is correct behavior here; a complete but fabricated one is not.

Original request: {request}

Subtask outputs:
{subtask_outputs}"""
