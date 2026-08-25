FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY src/ src/
COPY scripts/ scripts/
COPY alembic.ini .
COPY alembic/ alembic/
# The release gate runs pytest from inside this image (see
# src/agentsys/eval/gate.py and the Makefile's `gate` target), so the tests
# have to be in it. Without this, `make gate` fails at tier 1 on a clean
# build -- which is exactly how it was found.
COPY tests/ tests/

RUN pip install --no-cache-dir -e .

EXPOSE 8000
# Shell form (not exec form) so $PORT expands -- Railway injects a dynamic
# PORT the app must bind to; the ${PORT:-8000} fallback keeps this identical
# to before for local docker-compose, where `api` relies on this default CMD
# and no PORT env var is set (worker/rag-api/etc already override `command:`
# explicitly, so they're unaffected either way).
#
# `alembic upgrade head` runs before uvicorn starts -- init_db()'s
# create_all() (called from main.py's lifespan) only creates tables that
# don't exist yet, it does NOT add new columns to existing tables, so a
# schema change shipped without also running migrations would start
# successfully and then fail at the first query touching the new column.
#
# This is the ONLY migration step for Railway's single-service deploy (no
# separate migrate container there). In docker-compose.yml, the dedicated
# `migrate` one-shot service already runs this before api starts -- see its
# comment for why migrations must run from exactly one place (two
# containers racing `alembic upgrade head` against a fresh database is a
# real CREATE TABLE alembic_version collision, not a benign no-op). This
# run is then just a fast, already-at-head no-op in that case.
CMD alembic upgrade head && uvicorn agentsys.main:app --host 0.0.0.0 --port ${PORT:-8000}
