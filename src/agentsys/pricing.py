"""What a call cost, worked out at read time.

cost.py stores tokens and a dollar figure per LlmCall. The tokens are ground
truth -- they are what the provider actually counted and they never change.
The dollar figure is not: rates get cut, a model gets repriced, and a model
litellm didn't know yet was recorded at $0.00 forever.

So dollars are DERIVED HERE, from tokens, at the moment someone asks. The
practical difference: correcting a wrong rate retroactively fixes every
historical chart, instead of leaving a permanently wrong number baked into
rows nobody will ever backfill.

THE LADDER, checked in this order:

  1. OVERRIDES     rates pinned in this file. For a model priced wrong or not
                   known upstream. Highest precedence precisely so a bad
                   number is fixable here, in one commit, without waiting on
                   a dependency release.
  2. litellm       3,176 models and maintained upstream, already a dependency.
                   The primary source; there is no reason to re-type a table
                   someone else keeps current.
  3. FAMILY        a rough per-family guess, flagged `estimated=True` so the
                   UI can mark it rather than presenting a guess as a fact.
  4. unknown       zero, `estimated=True`. Never an exception: a pricing gap
                   must not break an analytics page.

`LlmCall.cost_usd` is still written on every call, but it is now a CACHE of
what this module said at the time, not the source of truth. Nothing should
sum that column to answer "what did we spend" -- see cost.spend_for.
"""

from __future__ import annotations

from dataclasses import dataclass

# Per 1M tokens, USD. Only for models this project pins that litellm prices
# wrong or not at all -- keep it short, and delete an entry the moment
# upstream is correct, or this becomes the stale table it exists to fix.
OVERRIDES: dict[str, tuple[float, float]] = {}

# Rough per-family rates for anything unrecognized, so a new model shows an
# order of magnitude rather than $0.00. Deliberately coarse and always
# reported as an estimate.
FAMILY_RATES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-opus": (15.00, 75.00),
    "claude": (3.00, 15.00),
    "gemini": (0.30, 2.50),
}


@dataclass(frozen=True)
class Rate:
    input_per_1m: float
    output_per_1m: float
    source: str
    estimated: bool = False
    cached_input_per_1m: float | None = None
    """What a prompt token served from the provider's cache costs. None means
    unknown, and callers fall back to half the input rate -- the convention
    OpenAI and Anthropic both use today. Ignoring caching entirely is not an
    option: it overstates spend on precisely the long-prompt calls that cache
    best."""

    @property
    def cached_rate(self) -> float:
        return self.cached_input_per_1m if self.cached_input_per_1m is not None else self.input_per_1m / 2


def _strip_provider(model: str) -> str:
    """'openai/gpt-4o-mini' -> 'gpt-4o-mini'. litellm's table is keyed without
    the provider prefix for OpenAI models but WITH it for some others, so both
    forms get tried rather than assuming one convention holds everywhere."""
    return model.split("/", 1)[1] if "/" in model else model


def _from_litellm(model: str) -> Rate | None:
    try:
        import litellm

        table = litellm.model_cost
    except Exception:  # noqa: BLE001 -- pricing must never break a read path
        return None

    for key in (model, _strip_provider(model)):
        entry = table.get(key)
        if entry and entry.get("input_cost_per_token") is not None:
            cached = entry.get("cache_read_input_token_cost")
            return Rate(
                input_per_1m=float(entry["input_cost_per_token"]) * 1_000_000,
                output_per_1m=float(entry.get("output_cost_per_token") or 0.0) * 1_000_000,
                source="litellm",
                cached_input_per_1m=float(cached) * 1_000_000 if cached is not None else None,
            )
    return None


def _from_family(model: str) -> Rate | None:
    """Longest matching prefix wins, so 'gpt-4o-mini-2024-07-18' resolves to
    the mini rate rather than the far more expensive 'gpt-4o' one. Getting
    this backwards would overstate spend by ~17x on the default model."""
    name = _strip_provider(model).lower()
    matches = [family for family in FAMILY_RATES if name.startswith(family)]
    if not matches:
        return None
    best = max(matches, key=len)
    rate_in, rate_out = FAMILY_RATES[best]
    return Rate(rate_in, rate_out, source=f"family:{best}", estimated=True)


def rate_for(model: str) -> Rate:
    """The ladder. Always returns something -- see the module docstring for
    why a pricing gap is a $0.00-estimated answer rather than an exception."""
    if model in OVERRIDES:
        rate_in, rate_out = OVERRIDES[model]
        return Rate(rate_in, rate_out, source="override")
    return _from_litellm(model) or _from_family(model) or Rate(0.0, 0.0, "unknown", estimated=True)


def cost_for(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int | None = None,
) -> float:
    """USD for one call. Pure: same inputs, same answer, no clock, no network
    (litellm's table is an in-process dict).

    `cached_tokens` is a SUBSET of prompt_tokens billed at the cache-read
    rate. None (an old row that predates the column) is treated as zero, which
    is the only thing that can be done with missing data -- callers surface
    that as an estimate rather than a fact. See cost.spend_from_rows.
    """
    rate = rate_for(model)
    cached = min(max(cached_tokens or 0, 0), prompt_tokens)
    full = prompt_tokens - cached
    return (
        full * rate.input_per_1m
        + cached * rate.cached_rate
        + completion_tokens * rate.output_per_1m
    ) / 1_000_000


def is_estimated(model: str) -> bool:
    return rate_for(model).estimated
