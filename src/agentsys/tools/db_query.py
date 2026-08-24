import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agentsys.db.session import get_engine
from agentsys.tools.base import Tool, ToolResult

MAX_ROWS = 200

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
_MULTIPLE_STATEMENTS = re.compile(r";\s*\S")
_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


class DbQueryTool(Tool):
    name = "db_query"
    description = (
        "Runs a read-only SQL SELECT query against the seeded sample business "
        "database (table: sample_metric, columns: id, company, quarter, revenue_usd, "
        "headcount). quarter is formatted like '2026-Q1' (NOT 'Q1 2026'). "
        "Rejects any data-modifying or multi-statement SQL. Results are capped at "
        f"{MAX_ROWS} rows -- if you need an aggregate over more rows than that, "
        "compute the aggregate in SQL (SUM/AVG/COUNT) rather than pulling raw rows. "
        "Arguments: sql (str, required) — the SELECT/WITH statement to run. "
        "Example: sql=\"SELECT quarter, revenue_usd FROM sample_metric WHERE "
        "company = 'Acme Robotics' ORDER BY quarter\". Returns "
        "{columns: [...], rows: [[...], ...]}. "
        "If a query returns zero rows, don't assume the data doesn't exist — try "
        "a broader query (e.g. SELECT DISTINCT quarter FROM sample_metric) to check "
        "the actual formatting before concluding there's no data."
    )

    def run(self, sql: str) -> ToolResult:
        blocked = self._reject_reason(sql)
        if blocked:
            return ToolResult(success=False, error=blocked)

        try:
            engine = get_engine()
            with engine.connect() as conn:
                conn = conn.execution_options(postgresql_readonly=True)
                result = conn.execute(text(sql))
                columns = list(result.keys())
                rows = [list(row) for row in result.fetchmany(MAX_ROWS)]
                # Explicitly never commit: even if a mutating statement slipped
                # past the guardrails above, letting the connection close
                # without commit() rolls the transaction back.
        except SQLAlchemyError as exc:
            return ToolResult(success=False, error=f"Query failed: {exc}")

        return ToolResult(success=True, output={"columns": columns, "rows": rows})

    @staticmethod
    def _reject_reason(sql: str) -> str | None:
        if not sql or not sql.strip():
            return "Empty query is not allowed."
        if not _ALLOWED_START.match(sql):
            return "Only SELECT (or WITH ... SELECT) queries are allowed."
        if _FORBIDDEN_KEYWORDS.search(sql):
            return "Query contains a forbidden data-modifying or schema keyword."
        if _MULTIPLE_STATEMENTS.search(sql):
            return "Multiple SQL statements are not allowed."
        return None
