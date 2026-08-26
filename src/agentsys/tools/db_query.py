"""Read-only SQL against an explicitly allowlisted set of tables.

WHY THE OLD GUARDS WERE NOT A BOUNDARY

This tool used to accept any statement that (a) started with SELECT/WITH,
(b) contained no DML keyword and (c) was a single statement -- three regexes
over the SQL text. Nothing restricted WHICH TABLE was read, and the tool runs
on the application's own engine with the application's own credentials. So
every one of these was valid, ungated, and returned real data:

    SELECT email, google_sub FROM app_user;
    SELECT token_hash, user_id FROM user_session;
    SELECT google_email, access_token, refresh_token FROM googleconnection;

The last one is plaintext OAuth tokens for every connected account. The agent
reads attacker-influenceable content (email bodies, Drive files, web pages,
uploaded documents), so a single successful prompt injection could exfiltrate
them through a tool that is not approval-gated, and the answer is then shown
to the requesting user. See docs/ARCHITECTURE_AUDIT.md §7.1.

THREE LAYERS, EACH ABLE TO STAND ALONE

1. Cheap text prefilters (kept from before): shape only -- SELECT/WITH, one
   statement, no DML, no filesystem/network builtins. These are a fast
   rejection path, NOT the boundary, and are no longer trusted as one.

2. Relation allowlist resolved by the PLANNER, not by pattern-matching. The
   query is handed to EXPLAIN (which plans but does not execute it) and every
   relation the plan actually touches is read back out. That sees through
   subqueries, CTEs, joins, views and aliases -- all the places a regex over
   the raw text loses track of what is really being read.

3. A dedicated read-only role (settings.db_query_url, see
   scripts/create_readonly_role.sql). This is the only layer enforced by
   Postgres rather than by us, so it is the one that still holds if layers 1
   and 2 are wrong. Optional because creating a role needs rights the app
   doesn't have -- but it is the layer to deploy.

FAIL CLOSED. Anything unexpected while resolving relations -- a planner
error, an unreadable plan, a connection problem -- rejects the query. A guard
that can be made to fall open by breaking it is not a guard.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from agentsys.config import settings
from agentsys.db.session import get_engine
from agentsys.execution import ExecutionSafety
from agentsys.policy import ActionType, Risk
from agentsys.tools.base import Tool, ToolResult

MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 10_000

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
_MULTIPLE_STATEMENTS = re.compile(r";\s*\S")
_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

# Builtins that reach outside the database (filesystem, other servers) or
# park a connection. None of them touch a relation, so the allowlist below
# never sees them -- this is the one thing layer 2 structurally cannot cover.
_FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|dblink\w*|pg_sleep)\s*\(",
    re.IGNORECASE,
)

# Explicit denial, checked AFTER the allowlist so it can only ever subtract.
# Redundant today (none of these are allowlisted) and deliberately kept: it
# means a careless future edit to DB_QUERY_ALLOWED_TABLES that adds one of
# these still gets refused, rather than silently opening the hole this module
# exists to close.
_ALWAYS_DENIED = frozenset({"app_user", "user_session", "googleconnection", "audit_event"})
_DENIED_SUBSTRINGS = ("session", "token", "credential", "password", "secret", "oauth")

_query_engine: Engine | None = None


def _engine() -> Engine:
    """The restricted role when one is configured, otherwise the app's own
    engine. Cached like db.session.get_engine for the same reason -- an
    engine per call would leak a connection pool per call."""
    global _query_engine
    if not settings.db_query_url:
        return get_engine()
    if _query_engine is None:
        _query_engine = create_engine(settings.db_query_url, pool_pre_ping=True)
    return _query_engine


def _relations_in_plan(node) -> set[str]:
    """Every relation the planner says this query touches. Walks the whole
    plan tree iteratively -- 'Relation Name' appears at any depth, under
    'Plans' for joins/subqueries and under CTE nodes for WITH clauses."""
    found: set[str] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            relation = current.get("Relation Name")
            if relation:
                found.add(str(relation).lower())
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _denied(relation: str) -> bool:
    return relation in _ALWAYS_DENIED or any(s in relation for s in _DENIED_SUBSTRINGS)


class DbQueryArgs(BaseModel):
    """Shape only. WHAT the SQL is allowed to touch stays where it already is
    -- the three-layer guard in run() (statement-kind regexes, an EXPLAIN-based
    relation allowlist, and a restricted Postgres role). A type check cannot
    tell a SELECT on sample_metric from one on googleconnection, and a second,
    weaker copy of that decision here is exactly the drift Phase 0 closed."""

    model_config = ConfigDict(extra="forbid")

    sql: str


class DbQueryTool(Tool):
    name = "db_query"
    args_model = DbQueryArgs
    action_type = ActionType.READ
    risk = Risk.MEDIUM
    execution_safety = ExecutionSafety.IDEMPOTENT
    """SELECT-only, and not on trust: the statement shape, an
    EXPLAIN-resolved relation allowlist and a read-only Postgres role all
    have to agree before a query runs (Phase 0). Re-running one changes
    nothing."""
    """MEDIUM, not LOW: this is arbitrary SQL against the application's own
    engine, and the thing that keeps it to one sample table is the three-layer
    guard in run() (Phase 0), not the shape of the argument. Policy must not
    weaken that -- the READ/MEDIUM rule ALLOWS this tool, which means the
    allowlist, the statement-shape prefilters and the read-only role remain the
    entire boundary, exactly as before. Gating it instead would put a human in
    front of every ordinary metrics lookup, which is how humans learn to
    approve without reading."""
    description = (
        "Runs a read-only SQL SELECT query against the seeded sample business "
        "database (table: sample_metric, columns: id, company, quarter, revenue_usd, "
        "headcount). quarter is formatted like '2026-Q1' (NOT 'Q1 2026'). "
        "ONLY the sample_metric table is readable -- any query touching another "
        "table is refused, so don't try to inspect the application's own tables. "
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
            with _engine().connect() as conn:
                conn = conn.execution_options(postgresql_readonly=True)
                # LOCAL so it reverts with the transaction rather than
                # persisting onto a pooled connection for everyone after us.
                conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))

                denied = self._relation_reject_reason(conn, sql)
                if denied:
                    return ToolResult(success=False, error=denied)

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
    def _relation_reject_reason(conn, sql: str) -> str | None:
        """Layer 2. Returns None only when every relation the planner
        resolves is allowlisted and none is denied."""
        allowed = settings.db_query_allowed_tables_set
        try:
            plan_row = conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}")).scalar()
        except SQLAlchemyError as exc:
            # Includes "permission denied for table ..." when the restricted
            # role is in use -- layer 3 answering before layer 2 had to.
            return f"Query rejected: could not be planned for inspection ({exc})."

        try:
            plan = json.loads(plan_row) if isinstance(plan_row, str) else plan_row
            relations = _relations_in_plan(plan)
        except (TypeError, ValueError) as exc:
            return f"Query rejected: its plan could not be inspected ({exc})."

        forbidden = sorted(r for r in relations if r not in allowed or _denied(r))
        if forbidden:
            # Name what was refused, not what exists -- the message is read
            # by the model and ends up in the trace, so it should not double
            # as a directory of the schema.
            return (
                f"Query rejected: reads {', '.join(forbidden)}, which this tool is not "
                f"permitted to access. Only these tables are readable: {', '.join(sorted(allowed))}."
            )
        return None

    @staticmethod
    def _reject_reason(sql: str) -> str | None:
        """Layer 1 -- shape only. Cheap, runs before any database round trip,
        and explicitly NOT the boundary (see the module docstring)."""
        if not sql or not sql.strip():
            return "Empty query is not allowed."
        if not _ALLOWED_START.match(sql):
            return "Only SELECT (or WITH ... SELECT) queries are allowed."
        if _FORBIDDEN_KEYWORDS.search(sql):
            return "Query contains a forbidden data-modifying or schema keyword."
        if _MULTIPLE_STATEMENTS.search(sql):
            return "Multiple SQL statements are not allowed."
        if _FORBIDDEN_FUNCTIONS.search(sql):
            return "Query calls a function this tool does not permit."
        return None
