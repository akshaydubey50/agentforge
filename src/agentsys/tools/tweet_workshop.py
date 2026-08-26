"""Tweet generation as a self-contained evaluator-optimizer workflow.

This is the "evaluator-optimizer" pattern (one model generates, a second,
stronger model critiques against a fixed rubric, and the generator revises on
that feedback until it passes or a budget runs out) packaged as a single Tool
-- the same "new capability, existing shape" move as delegate_subagent, so it
inherits tracing, cost accounting, and the reviewer/retry machinery for free
and the main agent can reach for it whenever a task needs a tweet.

Three components, looped:
  1. Generate  -- first candidate from the brief + rubric.
  2. Evaluate  -- a stronger model (reviewer tier) scores it against the rubric
                  and classifies APPROVED vs NEEDS-IMPROVEMENT, with specifics.
  3. Optimize  -- regenerate using the evaluator's feedback. (Steps 1 and 3 are
                  the same call; the only difference is whether prior feedback
                  is present, which is exactly what makes it an *optimizer*.)

The rubric below is the single source of truth both the generator and the
evaluator read, so they're optimizing toward the same target rather than the
generator guessing what "good" means. Edit TWEET_RUBRIC to change the house
style in one place.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentsys import cost
from agentsys.config import settings
from agentsys.graph.tracing import span
from agentsys.llm import structured_complete
from agentsys.execution import ExecutionSafety
from agentsys.policy import ActionType, Risk
from agentsys.tools.base import Tool, ToolResult

# The shared rubric. Both the generator (aim for this) and the evaluator
# (enforce this) are handed this exact text, so "good" means one thing.
TWEET_RUBRIC = """HARD REJECTS -- a tweet with either of these can never be approved, no matter \
how polished it is otherwise (these are objective, format-level violations):
- A traditional / generic joke: setup-then-punchline "dad joke" structure, pun-for-pun's-sake, \
  or a joke format that's been done to death.
- Question-and-answer format: "Q: ... A: ...", "Ever wonder why...? Well...", or any \
  self-posed-question-then-answer structure. These read as engagement-bait, not real posts.

STRONGLY AVOID (weigh heavily against approval, but judge by degree -- don't auto-fail a genuinely \
sharp tweet over a single familiar word):
- Near-duplicate of an existing viral tweet, OR templated marketing clichés -- "meet your new best \
  friend", "say goodbye to X", "game-changer", "it's like having a time machine", "superhero \
  powers", "who has time for...". Sharing a general TOPIC or angle with other tweets is completely \
  normal and fine -- only penalize genuine near-copies or exhausted, templated phrasing, not merely \
  a familiar subject.

AIM FOR (what makes a tweet actually good and shareable):
- A scroll-stopping HOOK in the first line: the first ~7 words decide whether anyone reads on. \
  Lead with the sharpest, most surprising, or most specific part -- never a warm-up.
- Trend / meme awareness: ride a current conversation or a recognizable viral format WHEN it \
  genuinely fits -- tastefully, not forced or try-hard. A well-placed meme format beats a \
  from-scratch line; a shoehorned one is worse than none.
- A clear point of view or a genuinely useful / surprising insight -- not vague, not corporate, \
  not a press release. Say something only this author would say.
- Concrete and specific over abstract and generic.
- Sounds like a real person talking, not a brand account.
- Emotionally resonant or screenshot-worthy: would a real person actually share or quote this?
- Format hygiene: <= 280 characters, at most 1-2 hashtags (0 is often better), no engagement-bait \
  ("like if you agree", "retweet this"), no hashtag stuffing."""


GENERATE_PROMPT = """You are a sharp, extremely online copywriter who writes tweets people \
actually screenshot and share. Write ONE tweet for this brief.

Brief: {brief}

Follow this rubric -- it's what your work will be judged against, so write TO it:
{rubric}

{feedback}
Return the tweet text ready to post (no surrounding quotes, no "here's your tweet:" preamble), \
plus one line on why your first line is a genuine hook."""


EVALUATE_PROMPT = """You are a ruthless tweet editor. Judge this tweet against the rubric \
strictly -- your job is to protect quality, so when in doubt, send it back. Approving a \
mediocre tweet is a worse outcome than one more revision.

Tweet under review:
{tweet}

Rubric:
{rubric}

Set each flag honestly, then decide. A tweet is approved ONLY if it trips none of the hard \
rejects AND genuinely clears the "aim for" bar (a real hook, a real point of view). If you set \
approved=false, your feedback must be concrete and actionable -- name exactly what to change, \
not just "make it punchier"."""


class TweetCandidate(BaseModel):
    tweet: str = Field(description="The tweet text, ready to post, <= 280 characters.")
    hook_rationale: str = Field(description="One line: why the first line stops the scroll.")


class TweetEvaluation(BaseModel):
    approved: bool = Field(
        description="True if it trips neither hard reject (joke / Q&A format) AND clears the "
        "quality bar (a real hook, a real point of view, not drowning in clichés). Judge "
        "unoriginality by degree -- a fresh, sharp tweet isn't disqualified by one familiar word."
    )
    resembles_existing: bool = Field(description="True only if it's a near-copy of a known tweet or is drowning in templated clichés.")
    is_traditional_joke: bool = Field(description="True if it's a generic setup-punchline joke.")
    is_qa_format: bool = Field(description="True if it uses a question-then-answer structure.")
    has_strong_hook: bool = Field(description="True if the first line genuinely stops the scroll.")
    trend_or_meme_aware: bool = Field(description="True if it tastefully uses a trend or viral format.")
    issues: list[str] = Field(default_factory=list, description="Specific problems; empty if approved.")
    feedback: str = Field(description="Concrete, actionable guidance for the next revision.")

    def quality_score(self) -> int:
        """A rough score to pick the best attempt when none is formally
        approved -- rewards a hook and trend-awareness, penalizes clichés."""
        return (2 if self.has_strong_hook else 0) + (1 if self.trend_or_meme_aware else 0) - (1 if self.resembles_existing else 0)


class TweetWorkshopArgs(BaseModel):
    """The one first-party tool that ALLOWS extra arguments instead of
    forbidding them, deliberately.

    run() already folds stray string kwargs (angle=, tone=, topic=) into the
    brief rather than losing them -- that behaviour exists because the model
    reliably produces them and the alternative was a TypeError and an
    escalation. Forbidding extras here would re-break exactly that, and this
    tool is not action-capable: it writes text and returns it, so the
    fail-closed argument that applies to file_io or code_execution doesn't.
    Anything action-capable should forbid extras.
    """

    model_config = ConfigDict(extra="allow")

    brief: str | None = Field(default=None, description="What the tweet should be about, plus any angle/voice notes.")


class TweetWorkshopTool(Tool):
    name = "generate_tweet"
    args_model = TweetWorkshopArgs
    action_type = ActionType.READ
    risk = Risk.LOW
    execution_safety = ExecutionSafety.IDEMPOTENT
    """Text in, text out. Repeating spends real LLM budget, which
    max_task_cost_usd already caps, but has no effect on anything."""
    """Writes text and returns it -- it does not post anything, and there is
    no Twitter/X client in this repo. READ is the honest fit in policy's
    four-value taxonomy for "no external effect"; the day this tool gains a
    publish step it becomes EXTERNAL_WRITE and is gated by the existing rule
    with no new code."""
    description = (
        "Generates a high-quality, shareable tweet via a generate -> evaluate -> optimize loop: "
        "it drafts a tweet, a stricter editor scores it against a fixed quality rubric "
        "(rejects unoriginal / already-circulating lines, generic jokes, and question-answer "
        "formats; rewards a strong hook, tasteful trend/meme awareness, a clear point of view), "
        "and revises on that feedback until the tweet is approved or the iteration budget runs "
        "out. Use this whenever the task asks for a tweet, post, or short social copy. "
        "Arguments: brief (str, required) -- what the tweet should be about and any angle/voice "
        "notes, e.g. brief=\"our new one-click export feature, playful, aimed at busy PMs\". "
        "Returns {tweet, approved, iterations, history: [{tweet, evaluation}, ...]}."
    )

    def run(
        self,
        brief: str | None = None,
        task_id: str | None = None,
        subtask_id: str | None = None,
        **extra,
    ) -> ToolResult:
        # Be tolerant of the agent's argument variations. The specialist LLM
        # sometimes passes extra kwargs the schema doesn't name -- angle=,
        # tone=, voice=, topic=, audience= -- which used to TypeError, fail the
        # call, and escalate. Instead, fold any stray string values into the
        # brief so the intent is used rather than lost, and still work when the
        # topic arrived under a different key entirely (e.g. topic= not brief=).
        parts = [brief.strip()] if brief and brief.strip() else []
        for key, value in extra.items():
            if isinstance(value, str) and value.strip():
                parts.append(f"{key}: {value.strip()}")
        brief = " -- ".join(parts)
        if not brief:
            return ToolResult(success=False, error="brief must be a non-empty string describing the tweet to write")

        history: list[dict] = []
        feedback_block = "This is your first attempt -- no feedback yet."
        approved_tweet: str | None = None

        for iteration in range(1, settings.max_tweet_iterations + 1):
            phase = "tweet_generate" if iteration == 1 else "tweet_optimize"
            gen_prompt = GENERATE_PROMPT.format(
                brief=brief, rubric=TWEET_RUBRIC, feedback=feedback_block
            )
            candidate = self._run_llm(
                task_id, subtask_id, phase, {"iteration": iteration, "brief": brief},
                gen_prompt, TweetCandidate, settings.llm_model,
            )

            evaluation = self._run_llm(
                task_id, subtask_id, "tweet_evaluate", {"iteration": iteration, "tweet": candidate.tweet},
                EVALUATE_PROMPT.format(tweet=candidate.tweet, rubric=TWEET_RUBRIC),
                TweetEvaluation, settings.reviewer_llm_model,
            )

            # Enforce ONLY the objective, format-level hard rejects in code
            # (a joke, a Q&A structure) -- even if the evaluator set
            # approved=true, those override it. Unoriginality is deliberately
            # NOT a code-level auto-fail: it's too subjective a call to make
            # absolute (a strict editor flags almost anything as "familiar"),
            # so it's left to the evaluator's holistic `approved` judgment and
            # the quality_score tie-break instead. This is the fix for the tool
            # escalating on every tweet because one flag was near-impossible to
            # satisfy.
            hard_reject = evaluation.is_traditional_joke or evaluation.is_qa_format
            is_approved = evaluation.approved and not hard_reject

            history.append({
                "iteration": iteration,
                "phase": phase,
                "tweet": candidate.tweet,
                "hook_rationale": candidate.hook_rationale,
                "evaluation": evaluation.model_dump(),
                "approved": is_approved,
            })

            if is_approved:
                approved_tweet = candidate.tweet
                break

            # Feed the evaluator's verdict forward -- this is the optimize step's fuel.
            issues = "; ".join(evaluation.issues) if evaluation.issues else "(none listed)"
            feedback_block = (
                f"Your previous attempt was NOT approved.\nWhat was wrong: {issues}\n"
                f"Editor's guidance: {evaluation.feedback}\nRewrite it to fix these specifically -- "
                "don't just tweak wording, address the actual problems."
            )

        if approved_tweet is not None:
            return ToolResult(
                success=True,
                output={
                    "tweet": approved_tweet,
                    "approved": True,
                    "iterations": len(history),
                    "history": history,
                },
            )

        # Budget exhausted without a formal approval. Rather than escalate to a
        # human and hand back nothing (too heavy for a subjective call like
        # "is this tweet good enough"), return the BEST attempt -- always
        # produce a usable tweet -- but flag approved=false honestly so the
        # output makes clear it didn't fully clear the bar. The human can
        # refine it via a follow-up message (chat continuation) if they want.
        best = max(
            history,
            key=lambda h: TweetEvaluation(**h["evaluation"]).quality_score(),
            default={},
        )
        return ToolResult(
            success=True,
            output={
                "tweet": best.get("tweet"),
                "approved": False,
                "note": (
                    f"Best of {len(history)} attempts -- it didn't fully clear the quality bar, "
                    "so treat it as a strong draft rather than a final. Refine the brief or ask "
                    "for another pass to push it further."
                ),
                "iterations": len(history),
                "history": history,
            },
        )

    def _run_llm(self, task_id, subtask_id, span_type, span_input, prompt, schema, model):
        """One traced+costed structured LLM call. When task_id is present
        (normal path, called from a subtask) each step is a real TraceSpan and
        its cost is booked; standalone (task_id=None, e.g. a test) it just runs
        the call. Keeps the three workflow components visible in the trace."""
        if task_id is None:
            result, _ = structured_complete(prompt, schema, model=model)
            return result
        with span(task_id, span_type, span_type, subtask_id=subtask_id, input=span_input) as s:
            result, completion = structured_complete(prompt, schema, model=model)
            s["output"] = result.model_dump()
        cost.record_llm_call(task_id, subtask_id, span_type, completion)
        return result
