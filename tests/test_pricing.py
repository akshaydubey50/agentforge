"""Unit tests for read-time pricing. No key, no stack, no database.

The property that matters most here is the family-prefix one: getting
'gpt-4o-mini-2024-07-18' matched against 'gpt-4o' instead of 'gpt-4o-mini'
overstates spend by roughly 17x, and it would look entirely plausible on a
chart.
"""

from types import SimpleNamespace

from agentsys import pricing
from agentsys.cost import spend_from_rows


def row(model: str, tokens_in: int, tokens_out: int, purpose: str = "agent_step", cached: int | None = 0):
    return SimpleNamespace(
        model=model,
        prompt_tokens=tokens_in,
        completion_tokens=tokens_out,
        purpose=purpose,
        cached_tokens=cached,
    )


class TestRateLookup:
    def test_known_model_comes_from_litellm(self):
        rate = pricing.rate_for("gpt-4o-mini")
        assert rate.source == "litellm" and not rate.estimated
        assert rate.input_per_1m > 0

    def test_provider_prefixed_id_resolves(self):
        """settings.llm_model is 'openai/gpt-4o-mini'; litellm keys it without
        the prefix, so both forms have to work."""
        assert pricing.rate_for("openai/gpt-4o-mini").input_per_1m > 0

    def test_dated_snapshot_resolves(self):
        """What actually lands in LlmCall.model is the dated id the API
        returns, not the alias that was requested."""
        assert pricing.rate_for("gpt-4o-mini-2024-07-18").input_per_1m > 0

    def test_unknown_model_is_zero_and_flagged_never_raising(self):
        rate = pricing.rate_for("totally-made-up-model-v9")
        assert rate.input_per_1m == 0.0 and rate.estimated

    def test_override_wins_over_everything(self):
        pricing.OVERRIDES["gpt-4o-mini"] = (1.0, 2.0)
        try:
            rate = pricing.rate_for("gpt-4o-mini")
            assert rate.source == "override" and rate.input_per_1m == 1.0
        finally:
            del pricing.OVERRIDES["gpt-4o-mini"]


class TestFamilyFallback:
    def test_longest_prefix_wins(self):
        """'gpt-4o-mini-preview-unknown' must NOT match the far pricier
        'gpt-4o' family. This is the expensive mistake."""
        rate = pricing._from_family("gpt-4o-mini-preview-unknown")
        assert rate.source == "family:gpt-4o-mini"
        assert rate.input_per_1m == pricing.FAMILY_RATES["gpt-4o-mini"][0]

    def test_family_rates_are_marked_estimated(self):
        assert pricing._from_family("claude-3-5-sonnet-something").estimated

    def test_no_family_match_returns_none(self):
        assert pricing._from_family("mistral-large") is None


class TestCostFor:
    def test_is_linear_in_tokens(self):
        one = pricing.cost_for("gpt-4o-mini", 1000, 1000)
        two = pricing.cost_for("gpt-4o-mini", 2000, 2000)
        assert abs(two - one * 2) < 1e-12

    def test_zero_tokens_costs_nothing(self):
        assert pricing.cost_for("gpt-4o-mini", 0, 0) == 0.0

    def test_output_is_priced_above_input(self):
        """True for every model this project pins; a table where it isn't is
        almost certainly a units mistake."""
        rate = pricing.rate_for("gpt-4o-mini")
        assert rate.output_per_1m > rate.input_per_1m


class TestCachedTokens:
    """Prompt caching is half price. Ignoring it overstates spend on exactly
    the long-prompt agent_step calls that cache best -- measured ~3% high
    across this project's own history before cached_tokens was recorded."""

    def test_cached_tokens_cost_half(self):
        model = "gpt-4o-mini-2024-07-18"
        rate = pricing.rate_for(model)
        assert rate.cached_rate == rate.input_per_1m / 2

    def test_caching_lowers_the_bill(self):
        full = pricing.cost_for("gpt-4o-mini", 10_000, 100, 0)
        half = pricing.cost_for("gpt-4o-mini", 10_000, 100, 10_000)
        assert half < full

    def test_reproduces_a_real_billed_call(self):
        """A real row from this project: 11,920 prompt / 138 completion tokens,
        billed $0.001564. Only reachable with the cache discount applied."""
        assert round(pricing.cost_for("gpt-4o-mini-2024-07-18", 11920, 138, 4096), 6) == 0.001564

    def test_none_is_treated_as_zero_cached(self):
        assert pricing.cost_for("gpt-4o-mini", 1000, 10, None) == pricing.cost_for(
            "gpt-4o-mini", 1000, 10, 0
        )

    def test_cached_cannot_exceed_prompt_tokens(self):
        """A nonsense value must not produce a negative full-rate portion."""
        assert pricing.cost_for("gpt-4o-mini", 100, 10, 99_999) > 0

    def test_negative_cached_is_clamped(self):
        assert pricing.cost_for("gpt-4o-mini", 100, 10, -5) == pricing.cost_for(
            "gpt-4o-mini", 100, 10, 0
        )


class TestSpendFromRows:
    def test_empty_is_all_zeroes_not_an_error(self):
        spend = spend_from_rows([])
        assert spend["usd"] == 0.0 and spend["llm_calls"] == 0 and not spend["estimated"]

    def test_totals_and_breakdowns_agree(self):
        rows = [
            row("gpt-4o-mini", 1000, 100, "sketch"),
            row("gpt-4o-mini", 2000, 200, "agent_step"),
            row("gpt-4o", 500, 50, "review"),
        ]
        spend = spend_from_rows(rows)
        assert spend["llm_calls"] == 3
        assert spend["tokens_in"] == 3500 and spend["tokens_out"] == 350
        assert abs(sum(spend["by_purpose"].values()) - spend["usd"]) < 1e-6
        assert abs(sum(m["usd"] for m in spend["by_model"].values()) - spend["usd"]) < 1e-6

    def test_one_unknown_model_flags_the_whole_figure_estimated(self):
        spend = spend_from_rows([row("gpt-4o-mini", 100, 10), row("who-knows-v3", 100, 10)])
        assert spend["estimated"]
        assert spend["by_model"]["who-knows-v3"]["estimated"]
        assert not spend["by_model"]["gpt-4o-mini"]["estimated"]

    def test_row_without_cached_tokens_marks_the_total_estimated(self):
        """A row predating the column cannot say whether it was cached, so the
        figure may read slightly high. Saying so beats a confident wrong
        number."""
        spend = spend_from_rows([row("gpt-4o-mini", 1000, 10, cached=None)])
        assert spend["estimated"]

    def test_rows_with_cached_tokens_are_exact(self):
        spend = spend_from_rows([row("gpt-4o-mini", 1000, 10, cached=512)])
        assert not spend["estimated"]

    def test_rate_change_is_retroactive(self):
        """The entire point of deriving at read time: the same stored rows
        price differently once the table is corrected."""
        rows = [row("gpt-4o-mini", 1_000_000, 0)]
        before = spend_from_rows(rows)["usd"]
        pricing.OVERRIDES["gpt-4o-mini"] = (999.0, 999.0)
        try:
            after = spend_from_rows(rows)["usd"]
        finally:
            del pricing.OVERRIDES["gpt-4o-mini"]
        assert after == 999.0 and after != before
