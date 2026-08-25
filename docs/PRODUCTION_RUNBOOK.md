# Production Runbook

Concise operator guide for the current AgentForge topology.

## Start Services

Local Docker:

```bash
docker compose up -d --build
```

Expected core services: Postgres, Redis, Chroma, `migrate`, API, worker, RAG API,
and web/dashboard services as needed.

## Run Migrations

Docker Compose runs the `migrate` one-shot service before API/worker start:

```bash
docker compose run --rm migrate
```

Single-service deploys run `alembic upgrade head` from the Dockerfile command
before Uvicorn starts.

## Check Liveness

```bash
curl http://localhost:8100/health
```

Expected: `{"status":"ok"}`. This is process liveness only.

## Check Readiness

```bash
curl -i http://localhost:8100/ready
```

Expected HTTP 200 when database, Chroma, and Celery worker ping are healthy.
Expected HTTP 503 with a JSON body when a dependency is degraded or shutdown is
in progress. This endpoint is read-only and must not create business rows.

## Inspect a Task

```bash
curl http://localhost:8100/v1/tasks/<task_id>
curl http://localhost:8100/v1/tasks/<task_id>/trace
```

Use the task status plus trace spans as the durable record. Redis SSE is live UX
only and has no replay buffer.

## Inspect Stuck Tasks

Use `agentsys.recovery.find_stuck_tasks()` from a Python shell inside the API or
worker container:

```bash
python -c "from agentsys.db.session import init_db; from agentsys.recovery import find_stuck_tasks; init_db(); print(find_stuck_tasks())"
```

Stuck means state-aware evidence such as stale `running`, stale `pending`, or
long-expired approval wait. A long task with recent progress is not stuck.

## Inspect Ambiguous ToolCall

Query `toolcall.success IS NULL`, then inspect the owning subtask/task:

```bash
python -c "from agentsys.db.session import init_db, get_session; from agentsys.db.models import ToolCall; from sqlmodel import select; init_db(); s=get_session(); print(s.exec(select(ToolCall).where(ToolCall.success == None)).all())"
```

`success=NULL` means unknown outcome. Do not manually replay non-idempotent
external effects. Check the external system first or report manual action.

## Restart/Recover Worker

```bash
docker compose restart worker
docker compose logs -f worker
```

Worker boot runs bounded startup recovery. Recovery is idempotent and duplicate
task execution is guarded by a Postgres advisory task claim.

## Provider Outage Response

LLM transient errors retry in `agentsys.llm` with bounded backoff and jitter.
Permanent errors such as invalid keys fail immediately. Do not add retries around
graph nodes; that multiplies attempts.

## Redis Outage Response

Impact: Celery broker/backend, SSE, idempotency cache, rate limits, cancellation
flags, and short-term memory. Durable task truth remains in Postgres, but worker
execution cannot make progress without the broker. Restore Redis, then restart
workers to trigger recovery.

## Postgres Outage Response

Postgres is durable truth. Effectful execution fails closed because ToolCall
claims cannot be persisted. Restore Postgres before restarting API/worker. Do
not attempt external-write replay from logs.

## Graceful Deployment

1. Stop routing new traffic to the old API.
2. Let in-flight worker tasks finish within Celery soft/hard limits.
3. Restart API/worker.
4. Check `/ready`.
5. Watch worker logs for recovery sweep output.

## Trace Debugging

Use `/v1/tasks/<task_id>/trace` for durable spans. If OTLP export is configured,
use the collector UI for latency/provider analysis. Export failures are
observability-only and must not change task state.

## Local Synthetic Reliability Benchmark

No real OpenAI or external providers:

```bash
PYTHONPATH=src python scripts/local_reliability_benchmark.py --tasks 100 --workers 10
```

It reports throughput, p50/p95 latency, errors, and duplicate execution count
using fake tools through the real execution ledger.
