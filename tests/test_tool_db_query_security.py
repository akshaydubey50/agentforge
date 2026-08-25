"""Security tests for db_query's table boundary.

These prove the CRITICAL finding in docs/ARCHITECTURE_AUDIT.md §7.1 is closed:
before the fix, every query in test_sensitive_tables_are_refused below was
valid, ungated and returned real data -- including plaintext Google OAuth
refresh tokens.

NOTHING HERE PRINTS A CREDENTIAL. The assertions check that the call was
REFUSED, never what it would have returned, and no test asserts on the content
of a token column. A security test that dumps the secret to prove it exists
has recreated the leak in the CI log.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from agentsys.config import settings
from agentsys.db.session import init_db
from agentsys.tools.db_query import DbQueryTool


def setup_module() -> None:
    init_db()


@pytest.fixture
def tool() -> DbQueryTool:
    return DbQueryTool()


# The tables the audit named explicitly, plus the audit log itself. Every one
# of these was readable before the fix.
SENSITIVE_TABLES = ["app_user", "user_session", "googleconnection", "audit_event"]


def test_the_sensitive_tables_actually_exist():
    """Guards the tests below against passing for the wrong reason. If a
    table were renamed, "SELECT * FROM it" would fail as UndefinedTable and
    every refusal assertion would still go green while the real table sat
    wide open under its new name."""
    from sqlalchemy import inspect

    from agentsys.db.session import get_engine

    present = set(inspect(get_engine()).get_table_names())
    missing = [t for t in SENSITIVE_TABLES if t not in present]

    assert not missing, f"these are not the real table names any more: {missing}"


@pytest.mark.parametrize("table", SENSITIVE_TABLES)
def test_sensitive_tables_are_refused(tool: DbQueryTool, table: str):
    result = tool.run(sql=f"SELECT * FROM {table}")

    assert result.success is False, f"{table} must not be readable by db_query"
    assert result.output == {}, "a refused query must return no rows at all"
    assert table in (result.error or "")
    # Specifically the ALLOWLIST refusing, not the planner failing for some
    # unrelated reason that happens to also produce an error.
    assert "not permitted to access" in (result.error or ""), (
        f"{table} was refused, but not by the allowlist: {result.error}"
    )


def test_the_exact_token_exfiltration_query_is_refused(tool: DbQueryTool):
    """The specific attack from the audit: plaintext OAuth tokens for every
    connected account, reachable through one prompt injection."""
    result = tool.run(
        sql="SELECT google_email, access_token, refresh_token FROM googleconnection"
    )

    assert result.success is False
    assert result.output == {}
    assert "not permitted to access" in (result.error or "")


def test_allowlisted_table_still_works(tool: DbQueryTool):
    """The boundary must not be a wall. If this fails the tool is useless,
    which is its own kind of broken."""
    result = tool.run(sql="SELECT company, quarter FROM sample_metric LIMIT 1")

    assert result.success is True, result.error
    assert "company" in result.output["columns"]


def test_join_onto_a_sensitive_table_is_refused(tool: DbQueryTool):
    """A join is where text-matching guards fall down: the statement still
    starts with SELECT and still names the allowlisted table."""
    result = tool.run(
        sql="SELECT m.company, u.email FROM sample_metric m CROSS JOIN app_user u"
    )

    assert result.success is False
    assert "app_user" in (result.error or "")


def test_subquery_reaching_a_sensitive_table_is_refused(tool: DbQueryTool):
    """The planner resolves relations inside scalar subqueries; a regex over
    the SQL text has no idea one is being read."""
    result = tool.run(
        sql="SELECT company, (SELECT count(*) FROM user_session) FROM sample_metric"
    )

    assert result.success is False


def test_cte_reaching_a_sensitive_table_is_refused(tool: DbQueryTool):
    """WITH is an allowed statement start, so this passes every layer-1 check
    and is caught only by resolving what the plan actually touches."""
    result = tool.run(
        sql="WITH leaked AS (SELECT email FROM app_user) SELECT * FROM leaked"
    )

    assert result.success is False
    assert "app_user" in (result.error or "")


def test_catalog_tables_are_refused(tool: DbQueryTool):
    """Not on the audit's list, but pg_catalog is the obvious next reach for
    anything trying to enumerate the schema."""
    result = tool.run(sql="SELECT tablename FROM pg_tables")

    assert result.success is False


def test_filesystem_functions_are_refused(tool: DbQueryTool):
    """Layer 1's job, and the one thing layer 2 structurally cannot catch: a
    function call touches no relation, so the plan has nothing to allowlist."""
    result = tool.run(sql="SELECT pg_read_file('/etc/passwd')")

    assert result.success is False
    assert result.output == {}


def test_write_statements_are_still_refused(tool: DbQueryTool):
    for sql in (
        "DELETE FROM sample_metric",
        "UPDATE sample_metric SET revenue_usd = 0",
        "DROP TABLE sample_metric",
        "SELECT 1; DROP TABLE sample_metric",
    ):
        result = tool.run(sql=sql)
        assert result.success is False, f"{sql!r} must be refused"


def test_unparseable_sql_fails_closed(tool: DbQueryTool):
    """A query the planner cannot plan must be REFUSED, not waved through.
    A guard that opens when you break it is not a guard."""
    result = tool.run(sql="SELECT * FROM sample_metric WHERE (")

    assert result.success is False
    assert result.output == {}


def test_allowlist_is_configurable_and_denylist_still_wins(tool, monkeypatch):
    """Even if someone adds a sensitive table to DB_QUERY_ALLOWED_TABLES, the
    explicit denylist refuses it. Fail closed on a careless config edit."""
    monkeypatch.setattr(
        settings, "db_query_allowed_tables", "sample_metric,app_user", raising=False
    )
    result = tool.run(sql="SELECT email FROM app_user")

    assert result.success is False, "denylist must override a bad allowlist entry"
