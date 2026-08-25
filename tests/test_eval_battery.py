"""Unit tests for the battery scorer.

No API key, no database, no Docker, no network -- `check_case` is a pure
function and this file proves it stays one. That is the point of the tier:
the thing that decides whether you ship is itself cheap to verify.
"""

from agentsys.eval.battery import BatteryCase, check_case, load_battery


def case(**kwargs) -> BatteryCase:
    base = {"id": "c", "category": "test", "request_text": "irrelevant"}
    return BatteryCase(**{**base, **kwargs})


def call(tool: str, **args) -> dict:
    return {"tool": tool, "args": args}


class TestExpectTool:
    def test_passes_when_expected_tool_fired(self):
        verdict = check_case(case(expect_tool="db_query"), [call("db_query", sql="SELECT 1")])
        assert verdict.passed and verdict.reason == "ok"

    def test_fails_when_tool_missing(self):
        verdict = check_case(case(expect_tool="db_query"), [call("web_search", query="x")])
        assert not verdict.passed
        assert "expected db_query" in verdict.reason

    def test_fails_readably_when_nothing_was_called(self):
        verdict = check_case(case(expect_tool="db_query"), [])
        assert not verdict.passed and "called nothing" in verdict.reason

    def test_extra_tools_are_allowed(self):
        """A correct run may take an exploratory step or a retry; the battery
        asserts the expected call happened, not that nothing else did."""
        verdict = check_case(
            case(expect_tool="db_query"), [call("web_search", query="x"), call("db_query", sql="SELECT 1")]
        )
        assert verdict.passed


class TestExpectInArgs:
    def test_substring_match_is_case_insensitive(self):
        verdict = check_case(
            case(expect_tool="db_query", expect_in_args={"sql": "2026-q1"}),
            [call("db_query", sql="SELECT revenue_usd WHERE quarter = '2026-Q1'")],
        )
        assert verdict.passed

    def test_the_readme_quarter_format_regression(self):
        """The logged bug: the model wrote 'Q1 2026' when the column holds
        '2026-Q1'. This is the case that catches it coming back."""
        verdict = check_case(
            case(expect_tool="db_query", expect_in_args={"sql": "2026-Q1"}),
            [call("db_query", sql="SELECT revenue_usd WHERE quarter = 'Q1 2026'")],
        )
        assert not verdict.passed and "'2026-Q1' not in args[sql]" in verdict.reason

    def test_checks_args_of_the_expected_tool_not_another(self):
        verdict = check_case(
            case(expect_tool="db_query", expect_in_args={"sql": "2026-Q1"}),
            [call("web_search", sql="2026-Q1"), call("db_query", sql="SELECT 1")],
        )
        assert not verdict.passed

    def test_missing_arg_key_fails_rather_than_raising(self):
        verdict = check_case(
            case(expect_tool="file_io", expect_in_args={"path": "notes.txt"}),
            [call("file_io", action="list")],
        )
        assert not verdict.passed


class TestExpectNoTool:
    def test_passes_when_answered_by_reasoning(self):
        assert check_case(case(expect_no_tool=True), []).passed

    def test_fails_when_a_tool_was_reached_for(self):
        verdict = check_case(case(expect_no_tool=True), [call("web_search", query="15% of 240")])
        assert not verdict.passed and "expected no tool call" in verdict.reason


class TestMinToolCalls:
    def test_multi_step_case_needs_enough_calls(self):
        verdict = check_case(case(expect_min_tool_calls=3), [call("db_query", sql="a"), call("db_query", sql="b")])
        assert not verdict.passed and "expected at least 3" in verdict.reason

    def test_satisfied_at_the_boundary(self):
        assert check_case(case(expect_min_tool_calls=2), [call("a"), call("b")]).passed


class TestOutputContains:
    def test_case_insensitive_substring(self):
        assert check_case(case(expect_output_contains=["36"]), [], final_output="The answer is 36.").passed

    def test_missing_substring_fails(self):
        verdict = check_case(case(expect_output_contains=["1991"]), [], final_output="Sometime in the 90s.")
        assert not verdict.passed and "missing '1991'" in verdict.reason

    def test_absent_output_is_not_a_crash(self):
        assert not check_case(case(expect_output_contains=["36"]), [], final_output=None).passed


class TestEscalation:
    def test_expected_escalation_that_happened_passes(self):
        assert check_case(case(should_escalate=True), [], did_escalate=True).passed

    def test_expected_escalation_that_did_not_happen_fails(self):
        verdict = check_case(case(should_escalate=True), [], did_escalate=False)
        assert not verdict.passed and "expected an escalation" in verdict.reason

    def test_unexpected_escalation_fails(self):
        verdict = check_case(case(expect_tool="db_query"), [call("db_query", sql="x")], did_escalate=True)
        assert not verdict.passed and "should have been handled" in verdict.reason

    def test_escalated_case_is_not_graded_on_tools_or_output(self):
        """A run that correctly paused for a human has no final answer and may
        have no tool call -- grading it against either would fail a case that
        did exactly the right thing."""
        verdict = check_case(
            case(should_escalate=True, expect_tool="file_io", expect_output_contains=["done"]),
            [],
            final_output=None,
            did_escalate=True,
        )
        assert verdict.passed


class TestShippedBattery:
    def test_battery_file_parses_and_is_not_empty(self):
        cases = load_battery()
        assert cases, "data/eval/agent_battery.jsonl should ship with cases"

    def test_case_ids_are_unique(self):
        ids = [c.id for c in load_battery()]
        assert len(ids) == len(set(ids)), "duplicate case ids make a report ambiguous"

    def test_escalation_cases_do_not_also_assert_output(self):
        """Not enforced by the scorer (it short-circuits), but an escalation
        case carrying output expectations is a sign someone misunderstood what
        the case asserts -- catch it at authoring time."""
        for c in load_battery():
            if c.should_escalate:
                assert not c.expect_output_contains, f"{c.id}: escalation case asserts output"


class TestStepCeiling:
    """The property that the spill/read loop violated and nothing else could
    see: every call succeeded, nothing was wrong, the answer never arrived."""

    def test_within_budget_passes(self):
        assert check_case(case(expect_max_steps=3), [call("db_query", sql="x")], steps_taken=2).passed

    def test_over_budget_fails_with_the_count(self):
        verdict = check_case(
            case(expect_max_steps=3), [call("db_query", sql="x")], steps_taken=14
        )
        assert not verdict.passed
        assert "took 14 steps" in verdict.reason

    def test_thrashing_to_a_correct_escalation_still_fails(self):
        """The exact shape of the bug: the outcome was 'right' (it escalated),
        but it took 14 steps to get there."""
        verdict = check_case(
            case(should_escalate=True, expect_max_steps=3), [], did_escalate=True, steps_taken=14
        )
        assert not verdict.passed

    def test_no_ceiling_means_no_check(self):
        assert check_case(case(expect_tool="db_query"), [call("db_query")], steps_taken=99).passed

    def test_unknown_step_count_does_not_fail_the_case(self):
        """A caller that can't supply steps must not turn every case red."""
        assert check_case(case(expect_max_steps=1), [call("db_query")], steps_taken=None).passed
