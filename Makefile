# AgentForge — one command per thing you actually do.
#
# Make is a shortcut tool, not a build system here: each target below is the
# shell command you'd otherwise type. Everything real runs in Docker (see
# docker-compose.yml), so most targets exec into the running api container
# rather than needing a local venv with Postgres/Redis/Chroma reachable.

DC := docker compose
API := $(DC) exec -T api

.PHONY: up down logs seed test unit battery gate eval web help

help:           ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

up:             ## build and start the whole stack
	$(DC) up -d --build

down:           ## stop everything
	$(DC) down

logs:           ## follow api + worker
	$(DC) logs -f api worker

seed:           ## seed sample_metric with demo data (needed by some battery cases)
	$(API) python scripts/seed_sample_db.py

# --- evaluation ------------------------------------------------------------
# Three tiers, never mixed. See src/agentsys/eval/gate.py.

unit:           ## tier 1 only: pure functions, no API key, no network
	$(API) python -m pytest -q tests/test_eval_battery.py tests/test_eval_grounding.py tests/test_graph_routing.py tests/test_pricing.py

battery:        ## tier 2 only: real runs, scored 0/1 by a pure function (needs a key)
	$(API) python -c "import sys; sys.path.insert(0,'src'); \
	from agentsys.db.session import init_db; from agentsys.auth import get_or_create_system_user; \
	from agentsys.eval.gate import run_battery_tier; init_db(); \
	r = run_battery_tier(get_or_create_system_user('agent-eval-harness','agent-eval@agentforge.local','Agent Eval Harness')); \
	print(r.status, r.detail); sys.exit(0 if r.status != 'fail' else 1)"

eval:           ## tier 3 only: the LLM-judged golden set (needs a key)
	$(API) python scripts/run_agent_eval.py

gate:           ## the ship/no-ship check: all three tiers, cheapest first
	$(API) python scripts/run_gate.py

test:           ## the full pytest suite (real Postgres/Docker/LLM calls -- costs money)
	$(API) python -m pytest tests/ -v

web:            ## the Next.js dashboard in dev mode
	npm run dev --prefix web
