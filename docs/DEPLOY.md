# Deploying to Railway

This repo runs seven services locally via `docker compose up` (see the root `README.md` and
`docs/MERGE.md`). Railway doesn't run `docker-compose.yml` directly, so this is six separate
Railway services, three of them sharing this repo's own `Dockerfile`. Everything below is done
in Railway's dashboard — nothing here can be done from a coding session, since it requires your
Railway account and secrets.

## What's different from local, and why

- **`api` and `worker` become one combined service** ("agentsys"), not two. They share a
  filesystem locally (the `./data` bind mount) — that's how an uploaded file lands via
  `POST /v1/tasks/upload` and gets read back by `file_io` later in the same task. Separate
  Railway services don't share a filesystem, so one container running both processes
  (`scripts/start_agentsys.sh`) is what preserves that without reaching for object storage.
- **`code_execution` is disabled** (`ENABLE_CODE_EXECUTION=false`). That tool sandboxes Python
  by spawning a sibling Docker container via the host's Docker socket — Railway containers don't
  get one. The tool cleanly drops out of the registry instead of failing every call; the agent
  just won't offer it. A real fix needs a different sandbox approach (e.g. a hosted execution
  API), out of scope here.
- **Postgres and Redis are Railway's managed plugins**, not containers you deploy yourself.
- **Chroma and rag-api both need a Railway Volume attached**, or every redeploy wipes the vector
  index and forces a full re-embed of the corpus — real OpenAI cost, hit twice already just
  from local Docker rebuilds during development.
- **The two Streamlit dashboards (`dashboard.py`, `simple_upload.py`) are not deployed.** They're
  dev tools the new `web/` app's own modules (Runs/Approvals/Knowledge) now cover.
- **`rag-seed`'s one-shot corpus embedding isn't automated.** Run it once manually after
  `rag-api` exists (step 8 below), not on every deploy.

## Services to create, in order

### 1. Postgres
Railway dashboard → New → Database → **Postgres** (Railway's managed plugin, not a Docker
deploy). No config needed — Railway generates connection details automatically.

### 2. Redis
Railway dashboard → New → Database → **Redis**. Same — managed plugin.

### 3. Chroma
New → Empty Service → **Deploy from Docker Image** → `chromadb/chroma:0.5.11`.
- Settings → Volumes → add a volume mounted at `/chroma/chroma`.
- Networking → note its internal hostname (used as `CHROMA_HOST` below) and internal port
  (`8000` — Chroma's default, matches `CHROMA_PORT`).

### 4. agentsys (combined api + worker)
New → GitHub Repo → this repo, **root directory `/`** (the `Dockerfile` is at repo root).
- Settings → Deploy → Start Command: `sh scripts/start_agentsys.sh`
- Settings → Volumes → add a volume mounted at `/app/data`
- Settings → Networking → generate a public domain (this is `NEXT_PUBLIC_API_BASE_URL` for
  `web`, set in step 6)
- Variables:
  ```
  DATABASE_URL=postgresql+psycopg://<user>:<password>@<postgres-host>:<port>/<db>
  REDIS_URL=<paste Redis plugin's REDIS_URL reference variable>
  CHROMA_HOST=<chroma service's internal hostname from step 3>
  CHROMA_PORT=8000
  RAG_API_URL=http://<rag-api service's internal hostname, from step 5>
  WORKSPACE_DIR=/app/data/workspace
  ENABLE_CODE_EXECUTION=false
  CORS_ALLOWED_ORIGINS=<web service's public URL, from step 6 — comma-separate if more than one>
  OPENAI_API_KEY=<your key, entered directly here>
  ```
  `DATABASE_URL` needs the `postgresql+psycopg://` prefix (this app's SQLAlchemy dialect) —
  Railway's own Postgres reference variable is plain `postgresql://`, so build this one by hand
  from the Postgres plugin's `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` reference
  variables rather than pasting its `DATABASE_URL` directly.

### 5. rag-api
New → GitHub Repo → this repo, root directory `/`.
- Settings → Deploy → Start Command: `uvicorn rag.main:app --host 0.0.0.0 --port $PORT`
- Settings → Volumes → add a volume mounted at `/app/data` (covers both the embedded Chroma
  index at `data/chroma` and the uploaded corpus at `data/raw`)
- Settings → Networking → generate a public domain and note the internal hostname (used as
  `RAG_API_URL` in step 4 above, and `NEXT_PUBLIC_RAG_API_BASE_URL` in step 6)
- Variables:
  ```
  OPENAI_API_KEY=<your key>
  CORS_ALLOWED_ORIGINS=<web service's public URL, from step 6>
  ```

### 6. web
New → GitHub Repo → this repo, **root directory `web/`**. Railway auto-detects Next.js.
- Settings → Networking → generate a public domain
- Variables:
  ```
  NEXT_PUBLIC_API_BASE_URL=https://<agentsys public URL from step 4>
  NEXT_PUBLIC_RAG_API_BASE_URL=https://<rag-api public URL from step 5>
  ```

Once `web`'s public URL exists, go back and set `CORS_ALLOWED_ORIGINS` on both the `agentsys`
and `rag-api` services to that URL (steps 4 and 5 reference this circularly — deploy once with
placeholders if needed, then fill in real values and redeploy).

### 7. Seed the corpus (once, manually)
After `rag-api` is up with its volume attached:
```bash
railway run --service rag-api python scripts/run_ingest.py --strategy all
```
This is the same script `rag-seed` runs locally — real OpenAI embedding cost, run it once, not
on every deploy.

## Verifying it worked

- `https://<agentsys-url>/health` → `{"status": "ok"}`
- `https://<agentsys-url>/v1/tools` → confirm `code_execution` is **absent** from the list
- `https://<rag-api-url>/v1/documents` → your corpus, after step 7
- `https://<web-url>/ask` → submit a real request, confirm it completes
