import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from sqlmodel import select

from agentsys.db.models import SampleMetric
from agentsys.db.session import get_session, init_db
from agentsys.tools.db_query import DbQueryTool

pytestmark = pytest.mark.usefixtures("_seeded_db")


@pytest.fixture(scope="module", autouse=True)
def _seeded_db():
    init_db()
    with get_session() as session:
        if not session.exec(select(SampleMetric)).first():
            session.add(
                SampleMetric(
                    company="Acme Robotics",
                    quarter="2026-Q1",
                    revenue_usd=4_200_000,
                    headcount=85,
                )
            )
            session.commit()
    yield


def _row_count() -> int:
    with get_session() as session:
        return len(session.exec(select(SampleMetric)).all())


def test_select_star_returns_seeded_rows():
    tool = DbQueryTool()
    result = tool.run(sql="SELECT * FROM sample_metric")

    assert result.success is True
    assert result.error is None
    assert "company" in result.output["columns"]
    companies = [row[result.output["columns"].index("company")] for row in result.output["rows"]]
    assert "Acme Robotics" in companies


def test_filtered_aggregate_query():
    tool = DbQueryTool()
    result = tool.run(
        sql=(
            "SELECT company, SUM(revenue_usd) AS total_revenue "
            "FROM sample_metric WHERE company = 'Acme Robotics' GROUP BY company"
        )
    )

    assert result.success is True
    assert result.output["columns"] == ["company", "total_revenue"]
    assert len(result.output["rows"]) == 1
    assert result.output["rows"][0][0] == "Acme Robotics"
    assert result.output["rows"][0][1] > 0


def test_with_cte_select_is_allowed():
    tool = DbQueryTool()
    result = tool.run(
        sql=(
            "WITH totals AS (SELECT company, revenue_usd FROM sample_metric) "
            "SELECT * FROM totals"
        )
    )

    assert result.success is True
    assert result.output["rows"]


def test_drop_table_is_blocked_and_does_not_touch_data():
    before = _row_count()
    assert before > 0

    tool = DbQueryTool()
    result = tool.run(sql="DROP TABLE sample_metric")

    assert result.success is False
    assert result.error is not None

    after = _row_count()
    assert after == before


def test_delete_is_blocked_and_does_not_touch_data():
    before = _row_count()

    tool = DbQueryTool()
    result = tool.run(sql="DELETE FROM sample_metric")

    assert result.success is False

    after = _row_count()
    assert after == before


def test_multi_statement_injection_is_blocked():
    before = _row_count()

    tool = DbQueryTool()
    result = tool.run(sql="SELECT 1; DROP TABLE sample_metric;")

    assert result.success is False
    assert result.error is not None

    after = _row_count()
    assert after == before


def test_update_disguised_inside_cte_is_blocked():
    before = _row_count()

    tool = DbQueryTool()
    result = tool.run(
        sql=(
            "WITH x AS (UPDATE sample_metric SET headcount = 0 RETURNING id) "
            "SELECT * FROM x"
        )
    )

    assert result.success is False

    after = _row_count()
    assert after == before


def test_non_select_statement_is_rejected_without_executing():
    tool = DbQueryTool()
    result = tool.run(sql="INSERT INTO sample_metric (company, quarter, revenue_usd, headcount) VALUES ('Evil', '2099-Q1', 1, 1)")

    assert result.success is False
    assert result.error is not None

    with get_session() as session:
        rows = session.exec(select(SampleMetric).where(SampleMetric.company == "Evil")).all()
    assert rows == []


def test_word_boundary_does_not_false_positive_on_created_column_name():
    tool = DbQueryTool()
    result = tool.run(sql="SELECT company AS created_at_alias FROM sample_metric LIMIT 1")

    assert result.success is True


def test_syntactically_invalid_select_fails_gracefully():
    tool = DbQueryTool()
    result = tool.run(sql="SELECT FROM WHERE this is not valid sql")

    assert result.success is False
    assert result.error is not None


def test_empty_query_is_rejected():
    tool = DbQueryTool()
    result = tool.run(sql="   ")

    assert result.success is False
    assert result.error is not None


def test_row_limit_is_enforced():
    tool = DbQueryTool()
    result = tool.run(sql="SELECT * FROM generate_series(1, 500) AS s")

    assert result.success is True
    assert len(result.output["rows"]) <= 200
