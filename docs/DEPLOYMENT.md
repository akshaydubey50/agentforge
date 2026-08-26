# AgentForge Deployment

This document describes a portfolio deployment profile for AgentForge. It does
not require creating cloud resources during release acceptance.

## Target Architecture

```text
Browser
  |
  v
Vercel: Next.js Execution Studio
  |        NEXT_PUBLIC_API_BASE_URL
  |        NEXT_PUBLIC_RAG_API_BASE_URL
  v
Render or Koyeb: agentsys FastAPI
  |-- Neon PostgreSQL: durable tasks, traces, approvals, ledger, memory metadata
  |-- Upstash Redis: Celery broker/result backend and session store
  |-- Worker service: Celery agent runtime
  |-- Chroma server: long-term memory vectors
  |
  v
Render or Koyeb: rag FastAPI
  |-- Upstash Redis: validates browser sessions created by agentsys
  |-- Persistent disk Chroma: knowledge indexes
  v
OpenAI API: LLM, embeddings, optional vision
```

The current repository has two backend services:

- `agentsys`: task runtime, approvals, tools, memory metadata, auth, SSE.
- `rag`: document ingestion, hybrid retrieval, citation verification.

They share secrets through environment variables but do not import each other.

## Local Docker Desktop Stack

Use Compose for the full local development stack:

```bash
cp .env.example .env
docker compose up -d --build
```

Compose starts the Next.js Execution Studio as `web` on `http://localhost:3000`.
The frontend container mounts `./web` for source edits, but keeps Docker-owned
`node_modules` and `.next` volumes. This prevents host-side `.next` output from
mixing with the container runtime and breaking CSS/static chunks.

Browser-facing frontend variables stay pointed at the published host ports:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8100
NEXT_PUBLIC_RAG_API_BASE_URL=http://localhost:8000
```

## Recommended Free Portfolio Profile

Use the existing feature flags rather than adding a new deployment mode.

Set:

```env
ENABLE_CODE_EXECUTION=false
SESSION_COOKIE_SECURE=true
CORS_ALLOWED_ORIGINS=https://<your-vercel-app>.vercel.app
WEB_APP_URL=https://<your-vercel-app>.vercel.app
GOOGLE_OAUTH_REDIRECT_URI=https://<your-api-host>/v1/auth/google/callback
```

This keeps the portfolio demo safe on hosts that do not expose a Docker socket.
The `code_execution` tool disappears from the registry cleanly. Do not replace
it with unsafe local subprocess execution.

## Frontend: Vercel

Project root:

```text
web/
```

Build command:

```bash
npm run build
```

Environment variables:

```env
NEXT_PUBLIC_API_BASE_URL=https://<agentsys-api-host>
NEXT_PUBLIC_RAG_API_BASE_URL=https://<rag-api-host>
```

The frontend uses credentialed fetches and EventSource, so the backend CORS
origin must include the exact Vercel origin.

## Backend: Render Or Koyeb

Use the existing `Dockerfile`.

Agentsys API start command is already the Dockerfile default:

```bash
alembic upgrade head && uvicorn agentsys.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Worker service command:

```bash
celery -A agentsys.worker.celery_app worker --loglevel=info --pool=solo --concurrency=1
```

For free-tier portfolio hosting, `solo` avoids prefork complications on small
containers. Use low concurrency to protect LLM spend.

RAG API command:

```bash
uvicorn rag.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

## Database: Neon PostgreSQL

Set:

```env
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>/<db>?sslmode=require
```

Run migrations once during release/startup:

```bash
alembic upgrade head
```

Do not run destructive reset commands in deployment. The Dockerfile runs
`alembic upgrade head` before API startup for single-service deployment.

## Redis: Upstash

Set:

```env
REDIS_URL=rediss://default:<password>@<host>:<port>
```

The same Redis must be used by:

- agentsys API
- agentsys worker
- rag API

Sessions are created by agentsys and validated by rag through Redis.

## Vector Store Strategy

Current behavior:

- Agentsys memory uses Chroma server via `CHROMA_HOST` and `CHROMA_PORT`.
- RAG uses embedded Chroma persisted at `CHROMA_PERSIST_DIR`.

Smallest release-safe portfolio option:

- Run Chroma as a hosted/container service with persistent disk for agentsys
  memory.
- Run RAG API with persistent disk for `CHROMA_PERSIST_DIR`.
- Keep corpus small and re-index from the UI or a one-off job.

Known debt:

- The repo currently uses two Chroma modes. A future migration to one hosted
  vector service or Postgres `pgvector` would simplify deployment, but that is
  too large for release acceptance.

## Google OAuth

Google Cloud Console OAuth client type:

```text
Web application
```

Authorized redirect URI:

```text
https://<agentsys-api-host>/v1/auth/google/callback
```

Required backend env:

```env
GOOGLE_CLIENT_ID=<server-side secret>
GOOGLE_CLIENT_SECRET=<server-side secret>
GOOGLE_OAUTH_REDIRECT_URI=https://<agentsys-api-host>/v1/auth/google/callback
WEB_APP_URL=https://<your-vercel-app>.vercel.app
SESSION_COOKIE_SECURE=true
```

Do not expose Google secrets to Vercel. Only the backend needs them.

Gmail capability is search/read and draft creation where scopes are granted.
There is no Gmail send UI.

## Required Environment Variables

Minimum agentsys API/worker:

```env
OPENAI_API_KEY=
LLM_MODEL=openai/gpt-4o-mini
REVIEWER_LLM_MODEL=openai/gpt-4o
DATABASE_URL=
REDIS_URL=
CORS_ALLOWED_ORIGINS=
WEB_APP_URL=
SERVICE_TOKEN=
SESSION_COOKIE_SECURE=true
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=
RAG_API_URL=
ENABLE_CODE_EXECUTION=false
WORKSPACE_DIR=/app/data/workspace
```

Optional:

```env
TAVILY_API_KEY=
ANTHROPIC_API_KEY=
OTEL_EXPORTER_OTLP_ENDPOINT=
DEEPEVAL_ENABLED=false
ENABLE_GOOGLE_PHOTOS=false
```

Minimum RAG API:

```env
OPENAI_API_KEY=
REDIS_URL=
SERVICE_TOKEN=
CORS_ALLOWED_ORIGINS=
SESSION_COOKIE_SECURE=true
CHROMA_PERSIST_DIR=/app/data/chroma
EMBEDDING_MODEL=openai/text-embedding-3-small
```

Minimum Vercel frontend:

```env
NEXT_PUBLIC_API_BASE_URL=
NEXT_PUBLIC_RAG_API_BASE_URL=
```

## Health Checks

Agentsys:

```text
GET /health
GET /health/deep
```

RAG:

```text
GET /health
```

Use `/health` for simple platform health checks. Use `/health/deep` manually
when diagnosing dependency readiness; it reports degraded dependencies in the
body.

## Deployment Blockers And Solutions

| Blocker | Current behavior | Portfolio solution | Code change required? |
| --- | --- | --- | --- |
| Docker sandbox unavailable | `code_execution` needs Docker socket. | Set `ENABLE_CODE_EXECUTION=false`; tool degrades out. | No |
| Background worker required | API enqueues Celery tasks. | Deploy separate worker service. | No |
| Redis required | Broker, result backend, sessions. | Use Upstash Redis and same URL in all services. | No |
| Postgres required | Durable run state and ledger. | Use Neon; run Alembic. | No |
| Chroma memory server | Agentsys memory expects Chroma HTTP. | Run Chroma service with persistent disk or keep memory disabled for minimal demo. | No for service; future migration debt |
| RAG embedded Chroma | RAG stores indexes on local disk. | Attach persistent disk to RAG service; keep small corpus. | No |
| OAuth redirect URL | Localhost default. | Set deployed callback URL in env and Google Console. | No |
| CORS credentials | Defaults to localhost frontend. | Set `CORS_ALLOWED_ORIGINS` to Vercel origin. | No |
| SSE on serverless | SSE requires long-lived backend connection. | Use Render/Koyeb backend, not Vercel functions. | No |
| Free-tier sleep | Backend/worker may sleep. | Accept cold starts for portfolio, show reconnecting UI. | No |
| File workspace persistence | Artifacts live under `WORKSPACE_DIR`. | Attach persistent disk or accept ephemeral artifacts in portfolio mode. | No |

## Known Free-Tier Limitations

- Cold starts can interrupt active runs.
- Long tasks may exceed free host runtime limits.
- `code_execution` should be disabled unless Docker socket is available.
- RAG and memory persistence require persistent disk or an external vector
  service.
- Web search reliability depends on available provider configuration.
- OAuth requires exact production redirect URLs before sign-in works.

## Troubleshooting

- Frontend redirects to login repeatedly: check cookies, `SESSION_COOKIE_SECURE`,
  backend origin, and CORS credentials.
- RAG playground 401s: ensure rag API uses the same Redis and session cookie
  settings as agentsys.
- Workspace never progresses: check worker process and Redis URL.
- Tools are missing: inspect `/v1/system/topology`; optional tools degrade out
  when dependencies or config are unavailable.
- Approvals do not resume: check worker logs after `POST /v1/escalations/{id}/decide`.
- Knowledge query fails: verify `SERVICE_TOKEN`, `RAG_API_URL`, and RAG health.
