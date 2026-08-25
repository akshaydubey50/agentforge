"""Unit tests for the grounding check. No key, no stack -- pure functions.

The thing under test is a fabrication detector, so the tests care as much
about it NOT crying wolf as about it catching the real case. A detector that
flags every correct answer gets ignored, and an ignored detector is worse
than none.
"""

from agentsys.eval.grounding import (
    check_grounding,
    figures_in,
    is_significant,
    normalize,
)


class TestNormalize:
    def test_strips_thousands_separators(self):
        assert normalize("9,600,000") == "9600000"

    def test_trailing_zeros_after_decimal_point_are_dropped(self):
        assert normalize("2100000.00") == "2100000"

    def test_keeps_a_real_decimal_part(self):
        assert normalize("8.85") == "8.85"

    def test_currency_formatting_matches_the_bare_number(self):
        assert normalize("$9,600,000.00") == normalize("9600000")


class TestSignificance:
    def test_small_integers_are_noise(self):
        assert not is_significant("3")
        assert not is_significant("12")

    def test_years_are_excluded(self):
        assert not is_significant("2026")
        assert not is_significant("1991")

    def test_large_values_count(self):
        assert is_significant("9600000")

    def test_decimals_count_even_when_small(self):
        """A growth rate is exactly the kind of figure worth checking."""
        assert is_significant("8.85")


class TestFiguresIn:
    def test_extracts_and_normalizes(self):
        assert figures_in("Revenue was $9,600,000 and growth 8.85%") == {"9600000", "8.85"}

    def test_ignores_prose_counts_and_years(self):
        assert figures_in("In 2026 the agent took 3 steps") == set()

    def test_empty_and_none_are_safe(self):
        assert figures_in("") == set()
        assert figures_in(None) == set()


class TestCheckGrounding:
    def test_quoted_figure_backed_by_a_tool_result_is_grounded(self):
        report = check_grounding(
            "Cedar's 2026-Q1 revenue was $9,600,000.",
            [{"rows": [["Cedar Analytics", "2026-Q1", 9600000]]}],
            ["db_query"],
        )
        assert not report.looks_fabricated
        assert report.grounded == ["9600000"]

    def test_the_readme_fabrication_bug(self):
        """Q1 was queried, Q2 never was, and the answer quotes both. This is
        the exact failure the regression test was written for."""
        report = check_grounding(
            "Q1 was $9,600,000 and Q2 was $10,450,000.",
            [{"rows": [["Cedar", "2026-Q1", 9600000]]}],
            ["db_query"],
        )
        assert report.looks_fabricated
        assert report.ungrounded == ["10450000"]

    def test_formatting_difference_alone_is_not_fabrication(self):
        """The answer writes 9,600,000; the tool returned 9600000. Reporting
        that as invented would be the detector's most damaging false
        positive."""
        report = check_grounding(
            "Revenue was $9,600,000.00.", [{"rows": [[9600000]]}], ["db_query"]
        )
        assert not report.looks_fabricated

    def test_derived_value_is_flagged_uncertain_not_fabricated(self):
        """8.85% appears in no tool output because it was computed. The check
        must say it cannot tell rather than accuse."""
        report = check_grounding(
            "Growth was 8.85%.",
            [{"rows": [[9600000]]}, {"stdout": ""}],
            ["db_query", "code_execution"],
        )
        assert report.ungrounded == ["8.85"]
        assert not report.certain
        assert not report.looks_fabricated

    def test_figures_with_no_tool_run_at_all_are_certain(self):
        """Nothing could have produced these, so the heuristic can settle it."""
        report = check_grounding("Revenue was $9,600,000.", [], [])
        assert report.looks_fabricated and report.certain

    def test_answer_with_no_significant_figures_is_clean(self):
        report = check_grounding("I could not determine that.", [], [])
        assert not report.looks_fabricated
        assert report.ungrounded == []

    def test_nested_tool_output_is_searched(self):
        report = check_grounding(
            "The total is 1234567.",
            [{"result": {"summary": {"total": 1234567}}}],
            ["db_query"],
        )
        assert not report.looks_fabricated

    def test_honest_refusal_is_never_flagged(self):
        """The distinction the whole four-outcome axis exists for: a miss
        must never be reported as an invention."""
        report = check_grounding(
            "I don't have data for 2026-Q2, so I can't give you the growth.", [], []
        )
        assert not report.looks_fabricated
