# Merge notes: two projects, one repository

This repo was two separate projects until they were merged:

- **`agentforge`** — multi-agent orchestration (now `src/agentsys/`)
- **`hybrid-rag-eval`** — hybrid-search RAG pipeline with an eval harness (now `src/rag/`)

Both git histories are preserved here; the RAG project was brought in with
`git subtree`, not copied, so `git log` still shows its original commits.

## Why merge at all

The agent needs a retrieval capability, and the RAG pipeline is one. Keeping
them apart meant either duplicating retrieval inside `agentsys` or running two
stacks that had to be kept in step by hand.

## What had to be reconciled first

**Dependency versions were the blocker.** `agentsys` had been forced forward to
`fastapi 0.141` / `pydantic 2.13.4`, because `mcp 2.0.0` requires
`starlette>=1.x` and `fastapi 0.115` called a starlette Router API that
starlette 1.x removed — MCP support and the old pins could not coexist. The RAG
project was still on `fastapi 0.115` / `pydantic 2.9.2`.

The upgrade was verified rather than assumed, in this order:

1. Baseline the RAG suite on its old pins: **9 passed, 2 failed**.
2. Upgrade, re-run: **9 passed, 2 failed** — identical, so nothing regressed.
3. The 2 failures were pre-existing and unrelated to versions: both tests are
   named `..._covers_all_formats` and already assert format coverage, but also
   asserted an exact corpus size of 12 while `data/raw/` had grown to 16. The
   count became a floor, and the suite went green: **11 passed**.
4. Confirm no package needed a different version in each project: **none did**.
5. Confirm the combined 27-package set resolves: **it does**.

## Layout

Nothing about either package's internals changed — only where the files live.

```
src/agentsys/   agent orchestration (plan -> execute -> review -> escalate)
src/rag/        hybrid retrieval, generation, citation verification, eval
tests/          both suites, no filename collisions
scripts/        both projects' scripts, no collisions
data/
  raw|processed|eval/   rag corpus, chunk metadata, eval results
  workspace/            agentsys per-task sandboxed file_io
docs/RAG_PIPELINE.md    the RAG project's original README
```

The two packages do **not** import each other. `agentsys` reaches the RAG
pipeline the same way any external caller would, which keeps the boundary
honest and means either can still be run on its own.

## Known inconsistency: two Chroma modes

`agentsys` talks to Chroma as an **HTTP service** (the `chroma` container,
`CHROMA_HOST`/`CHROMA_PORT`). `rag` uses Chroma **embedded**, against a local
directory (`CHROMA_PERSIST_DIR`, `./data/chroma`).

Same library, same pinned version, two different modes in one repo. They do not
conflict — different processes, different stores, and `rag` has no dependency on
the `chroma` service at all — but it is inherited inconsistency rather than a
design decision, and unifying on the service mode would be the tidier end state.

## Running it

Both stacks come up from one compose file:

```bash
docker compose up -d --build
```

| Service | Port | What |
|---|---|---|
| `api` | 8100 | agentsys REST API |
| `dashboard` | 8601 | agentsys Streamlit UI |
| `rag-api` | 8000 | rag REST API |
| `rag-dashboard` | 8501 | rag upload/query UI |
| `postgres` / `redis` / `chroma` | 5432 / 6379 / 8001 | agentsys backing services |
| `worker` | — | agentsys Celery worker |
| `rag-seed` | — | one-shot; builds rag's indexes, `rag-api` waits for it |

For tests, only the backing services are needed — starting everything triggers
`rag-seed`, which spends real embedding calls:

```bash
docker compose up -d postgres redis chroma
PYTHONPATH=src pytest tests/ -v
```
