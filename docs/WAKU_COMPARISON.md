# waku-agent vs AgentForge — architecture teardown and gap analysis

Reference repo: `github.com/ShenSeanChen/waku-agent`, cloned to `../waku-agent`
(sibling of this repo). Analysed at commit `8328f56`.

Scope: what waku is, how it is built, what AgentForge lacks, and what is worth
copying — ordered by payoff, with the honest note that **the two systems are not
competing on the same axis**. Waku is a legible, local-first *agent harness*
optimised for "is this agent any good, and how do you know". AgentForge is
*production task infrastructure* optimised for "can this run unattended, for many
users, and survive a crash". Each is strong exactly where the other is thin.

---

## Part 1 — waku-agent, in full

### 1.1 Shape and size

```
25,594 LOC Python · 172 LOC TS (no frontend framework at all)
14,050 LOC in the waku/ package · the rest is evals/, scripts/, examples/
62 deterministic eval files · 3 judge eval files
```

No Postgres, no Redis, no Celery, no Docker required. One SQLite file
(`.waku/state.db`) plus a handful of append-only JSONL logs. `pip install` and one
API key gets you a working agent. Everything the agent writes is a file you can
open — that "local-first means you can always look" rule drives most design
decisions in the repo.

### 1.2 The four pillars

The whole repo is organised around a whiteboard diagram with four boxes. The
`CLAUDE.md` maps file → box explicitly, and code comments constantly reference it.

| Pillar | Where | What it is |
|---|---|---|
| **Harness** | `waku/gateway/`, `waku/runtime/session.py` | Gateways move text only; Session assembles working memory |
| **Loop** | `waku/loop/agent.py`, `waku/loop/models.py` | ~110-line while-loop; 11 providers behind 2 wire formats |
| **Memory** | `waku/memory/` | semantic + episodic + procedural, plus a gate and a consolidator |
| **Eval / LLM-Ops** | `waku/ops/`, `evals/` | traces, spend ledger, arenas, release gate |

### 1.3 The loop (`waku/loop/agent.py`)

The entire agent is this, and the file says so in its docstring:

```python
for iteration in range(1, max_iterations + 1):
    response = llm(messages, tools)                  # reason
    messages.append(assistant_turn)
    if no tool_use blocks: return reply              # guardrail 1: natural end
    for call in tool_uses:
        output = tools.execute(call.name, call.input, notify=notify)
    messages.append(tool_results)                    # observe
# guardrail 2: iteration limit -> honest "I hit my limit" reply
```

Three things make it more than a toy:

- **`messages` is mutated in place**, so after the call it *is* the full working
  memory of the turn — exactly what gets traced. No separate trace assembly.
- **The observer protocol.** `notify(kind, event)` is a single callable threaded
  through everything: the loop, the tool registry, the graph engine, and even
  sub-agent subprocesses. `ops.tracing.compose(...)` fans one event stream out to
  the gateway (live UI), the tracer (JSONL + OTel), and an ad-hoc capture closure
  (`app.py` uses one to persist gate/route decisions with the turn). This one seam
  is why waku gets streaming, live tool display, sub-agent visibility, and tracing
  without any of them being wired into the loop's logic.
- **Streaming is optional and falls back silently.** `stream=True` uses
  `client.messages.stream`, and *any* streaming hiccup falls back to one
  non-streaming call.

### 1.4 Provider layer (`waku/loop/models.py`, 469 LOC)

The loop speaks exactly one dialect — Anthropic's Messages shape. Providers plug
in two ways:

- **anthropic wire (native):** Anthropic, Kimi/Moonshot, GLM/Z.ai, MiniMax
- **openai wire (a ~60-line adapter):** OpenAI, Gemini, DeepSeek, OpenRouter, xAI,
  OpenCode zen/go

`OpenAICompatClient` translates content blocks ↔ `tool_calls`, exposes both
`.create` and `.stream`, and handles a pile of real-world grit that is worth
reading as a checklist of things that break in production:

- `max_tokens` vs `max_completion_tokens` retry — *only* when the error is about
  that param, so it can't mask unrelated failures
- Gemini's `thought_signature` must be echoed back with the tool call or the next
  call 400s
- OpenRouter returning HTTP 200 with an error body and no `choices`
- API keys with non-ASCII characters from a bad paste (latin-1 header encoding)
- **Cross-provider model leakage**: `WAKU_MODEL` is global, so switching provider
  carried Anthropic's gate model to xAI, which 400s — and because the retrieval
  gate *fails open*, it silently retrieved on every turn while reporting a healthy
  "retrieve" decision. "A silent permanent failure wearing the costume of a
  healthy decision." Fixed by dropping *inherited* env values that positively
  belong to another provider, while keeping explicitly-passed ones.

Each provider declares `model`, `small_model`, `flagship`, `fast`, a catalog URL,
and optional regional endpoints. `ops/catalog.py` fetches the live model list per
provider (5-min cache, failures cached ~1min *with the reason*), and
`.waku/models.json` holds a user-curated pinned shortlist that drives the chat
model switcher.

### 1.5 Memory (`waku/memory/`)

Three kinds behind one facade, plus two agents that manage them.

**Semantic** — `facts` table with SQLite FTS5. Swappable to Supabase/pgvector,
Mem0, Zep, or LangMem. Crucially, `semantic/base.py` defines the six-method
contract *and there is a conformance test suite* (`test_fact_store_conformance.py`)
that every backend must pass. The docstring explains why it exists: SQLite
implemented six methods, Supabase implemented two, and the call site guarded
`search_with_ids` with `hasattr(...) else []` — so the agent silently received an
empty list and told the user it had no memories.

**Episodic** — `episodes` table, or a Notion database.

**Procedural** — `SKILL.md` files in the official Anthropic Agent Skills format
(YAML frontmatter with `name` + `description`; the description *is* the trigger).
Progressive disclosure: frontmatter of every skill is always scanned (cheap), a
skill's **body** loads into the prompt only when it matches the message, and files
a skill references are only read if the model asks. Skills ship in the repo
(`skills/`), are force-included into the wheel, and users can add their own to
`.waku/skills/`.

**Retrieval gate** (`memory/retrieval_gate.py`) — before touching any store, a
cheap fast model answers one question: *does THIS message need the user's memory?*
Returns `{retrieve, query, reason}`. `"what's 2+2"` → no. `"when am I meeting
Alex?"` → yes, plus the search query. Cost: one small-model call. Payoff:
retrieval only when it helps, because default-on retrieval is both slow *and
worse* (irrelevant memories bias the answer). **Fails open** — a broken gate
retrieves, because a stale memory beats a lost one.

**Consolidation** (`memory/consolidation.py`) — after every N exchanges (default 6),
a cheap model reads the unconsolidated chat log and distils it into durable facts
+ one episode summary. Batching gives the summariser enough context; running it
every message is wasteful and noisy. On any failure the log stays unconsolidated
for next time — never lost.

**`MEMORY.md`** — after every turn, memory is mirrored to a human-readable markdown
file next to `state.db`. The DB stays the source of truth; the file makes "your
memory is something you can open" literally true.

**The agent manages its own memory** — three tools: `manage_memory` (CRUD over
facts/episodes), `update_soul` (append a standing preference to its own persona
file), `create_skill` (write a new SKILL.md after the user teaches it a workflow).
`update_soul` is append-only by design so the agent can't delete its own honesty
rules.

### 1.6 Working memory (`waku/runtime/session.py`)

Per turn, rebuilt and thrown away:

```
SOUL.md (editable persona)
+ "Right now it is <local time, tz>"        ← so it can resolve "in 30 minutes"
+ "Your model: you are running on <model> via <provider>"
+ gated memory retrieval (facts + episodes)
+ matching SKILL.md bodies
+ last N turns of history (sliding window, default 12)
+ the user's new message
```

Two details worth stealing:

- Past turns record a compact `[tools used: name(args) -> output]` line in the
  assistant's history entry. Without it the model forgets it already acted and
  re-runs the same tool — "the triple-booked-meeting bug from the first live test".
- History is a **bounded sliding window**. Older turns aren't lost; they're in
  `state.db`, distilled by consolidation, and pulled back by the gate when
  relevant. Context/cost/latency stay flat no matter how long the thread runs.

### 1.7 The graph layer (`waku/graph/`) — structure *around* the loop

Opt-in (`WAKU_GRAPH_WORKFLOWS=1`), and **every seam fails open to the plain loop**,
so it can never make waku worse — only faster/cheaper.

`graph/engine.py` (206 LOC) is a hand-rolled wave scheduler:

- **state** — one plain dict blackboard; nodes return keys to merge. Parallel nodes
  in the same wave must write **disjoint keys**; a collision raises
  `GraphStateCollision` instead of silently losing a write.
- **routers** — plain Python functions over state. Models write state; *code* reads
  it and picks the edge. **No LLM ever decides control flow directly.**
- **guards** — per-node `max_visits` (bounded cycles) + global `max_steps`. A node
  exception is recorded into `state["errors"]` and surfaced, never raised out of
  the run; the run drains cleanly to END.
- **waves** — ready nodes run concurrently in a ThreadPool; results merge in wave
  order, so execution order is deterministic and traces read the same way twice.
- `graph.describe()` returns the topology **as data**, which is what the dashboard
  draws — "rendering from this, never from a hand-copied picture, is what keeps
  the chart honest."

`graph/nodes.py` gives four node shapes: `tool_node` (pure function),
`llm_node` (one call, no tools — for classify/score/rewrite), **`agent_node` (a
full `run_loop` turn as one node)**, and `key_router`. The `agent_node` is the
whole thesis: *graphs don't replace the loop, they arrange calls around it and to
it.* `app.py` proves it — the graph's `full_agent` node calls the exact same
`_run_full_turn` method the flag-off default calls, so loop-as-node can never
drift from loop-as-default.

**The triage workflow** (`graph/workflows/triage.py`) is the shipped example:

```
START ─┬─ classify (small model)  ─┬─ gather ─ router ─┬─ quick_reply (small model) ─ END
       └─ check_calendar (file IO)─┘                    └─ full_agent (THE loop)     ─ END
```

Two things happen *at the same time* (a network call and a local file read), then a
code router picks the path. `"thanks!"` never wakes the big model. The user never
chooses a mode — the graph *is* the choice.

There is also `gather` — the same morning-briefing job done as a graph (4 sources
in parallel, then one digest) alongside `brief.py` which does it as a loop. Both
ship. Reading them side by side is the point.

### 1.8 Tools

`tools/registry.py` is 60 lines: name + description + JSON schema + function.
`execute()` catches every exception and returns it as text — *the model observes
errors instead of the loop crashing*. Tools can opt into `wants_notify=True` to
stream progress through the loop's observer.

Shipped: `create_event` / `list_events` (flagship), `save_note`, `send_message`
(drafts to a local outbox — nothing actually sends), `search_web` (DuckDuckGo
default, Tavily if keyed), `manage_memory` / `update_soul` / `create_skill`,
Apple ecosystem tools, Google Calendar, GitHub (read-only via the `gh` CLI's own
keychain auth — **argv is constructed, never filtered**), MCP client, and
`delegate_task` → the `pi` coding sub-agent with its native JSON event stream
relayed through the observer.

Governing rule — the **footprint ladder**, because every registered tool ships in
every prompt: *extend existing code → a skill (no Python) → a CLI + README → a
tool behind an extra → a gateway → a new core tool, last resort.*

### 1.9 LLM-Ops — the part AgentForge is thinnest against

**Tracing** (`ops/tracing.py`): JSONL always on (`.waku/traces/<date>.jsonl`), plus
OpenTelemetry spans when `OTEL_EXPORTER_OTLP_ENDPOINT` is set — so Phoenix
(`make trace`, localhost:6006) or Langfuse render the same events with zero
instrumentation changes. Spans carry `openinference.span.kind` so they render as
LLM/TOOL/CHAIN/AGENT. Flushed per turn so a killed process still has its trace.

**Spend ledger** (`.waku/usage.jsonl`): one line per LLM call with provider, model,
and token counts — **and deliberately no price**. "Prices change; tokens don't."
Cost is derived at *read* time from `ops/pricing.py`, which means fixing a wrong
rate silently corrects every past race and every historical chart. Three-tier
lookup: rates learned from a live catalog fetch → hand-maintained per-model table
→ a rough provider-level fallback labelled "est".

**Two eval tiers that are never mixed:**
- `evals/deterministic/` — 62 pytest files, 0/1, no model needed. Must pass 100%.
- `evals/judge/` — DeepEval, scored, runs only when a key is present.

**Release gate** (`make gate`): deterministic must pass or it exits 1 —
"GATE CLOSED". Judge runs if a key exists. Writes `eval_report.json` (latest
verdict) *and* appends to `eval_runs.jsonl` (history). One command answers
"changed the prompt / swapped the model / tuned top-k — can I ship?"

**The Model Arena** (`ops/arena.py`): one message → N models at once, each running
the **real harness** (gate, tools, memory) in **its own throwaway home**, streamed
to the dashboard over SSE. Two deliberately separate scores:
- *Completion* — deterministic. `ops/scoring.py` checks the battery case: did the
  expected tool fire, with the expected args, with enough calls? This is the same
  scorer the CLI `make shootout` uses, so the terminal number and the on-screen
  number can never drift.
- *Quality* — `ops/judge.py`, an LLM referee, 0–10 + one-line reason, run *after*
  the race in one gentle pass so concurrent calls can't 429 half of them. **The
  referee must be a model that isn't racing.** The judge is given the list of tools
  that *actually* fired as ground truth, so a truthful "I saved that" isn't scored
  as a hallucination.

**The Memory Arena** (`ops/memory_arena.py`, 896 LOC): same idea, other dial —
holds model *and* harness constant, varies **where facts live** (FTS5 / Mem0 / Zep /
LangMem / Supabase). Seed an 8-message conversation, then ask 7 probes. The scoring
is the best idea in the repo:

> **PASS** · **STALE** (returned a superseded answer) · **INVENTED** (a refusal was
> correct and it answered anyway) · **MISS** (honest failure)
>
> "Pass/fail hides the only interesting question. A system that says 'I don't know'
> is behaving correctly under uncertainty. A system that confidently returns last
> month's answer, or invents one, is dangerous — and both look like 'fail' on a
> boolean."

It also runs a **control contestant** that is told nothing and asked everything: any
probe the control passes measures *training data*, not the store. And because the
refusal detector is a keyword heuristic, every verdict resting on it is flagged
`certain=False` so only those probes get sent to a judge.

**Coding eval** (`ops/coding_eval.py`): hands a real programming job to `pi` pointed
at the contestant's model, then scores by **running the produced code** — the
`verify` command's exit code is the verdict, SWE-bench style, not an opinion.

### 1.10 Gateways

CLI, voice (wake word), Telegram, Discord, WhatsApp, and the dashboard — all under
the rule *"a gateway only moves text in and out."* The Discord gateway's docstring
is a model of honest security writing: running your agent in a shared server is
three problems at once (cost — every message is a billed turn; privacy — strangers
read your facts back out; integrity — their conversation gets consolidated into
*your* long-term memory), so the default posture is **deny**.

### 1.11 Engineering culture worth noting

- **Fail open, everywhere.** Retrieval gate, triage classifier, graph engine,
  tool execution, judge calls — every one degrades to the safe path rather than
  erroring.
- **Comments explain live bugs with dates and measurements.** ("Measured against a
  472-event calendar: `whose` filter ~25s vs ~6s." "Watched kimi-k3 return an empty
  reply at max_tokens=2048.")
- **`make gate` before push; a live bug becomes a regression case in
  `evals/deterministic/`.**
- **No new dependencies without discussion** — core is stdlib + anthropic/openai;
  everything else is an extra.

---

## Part 2 — AgentForge, as it stands

For a fair comparison, here is what this repo already does that waku does **not**,
and in several cases could not:

| Capability | AgentForge | waku |
|---|---|---|
| Multi-tenancy, auth, per-user isolation | Google OAuth, sessions with idle + absolute timeout, `owner_id` on every row | none — single local user |
| Tamper-evident audit log | hash-chained `audit.py` + alembic migration | none |
| Durable async execution | Celery + Redis, `acks_late`, soft/hard time limits | synchronous, in-process |
| Crash recovery | `recovery.py` — orphaned-subtask reconciliation, stranded-task sweep | none |
| Idempotency | client `Idempotency-Key` → 24h task mapping | none |
| Rate limiting | per-user hourly cap on enqueueing endpoints | none |
| Cancellation | cooperative `TaskCancelled` + a guard against status resurrection | none |
| Human-in-the-loop | escalations with approve / reject / **take_over**, plus tool-approval gating | none |
| Cost as a stop condition | `max_task_cost_usd` checked before deciding | ledger only, no ceiling |
| Progress as a stop condition | unproductive-streak counter + dead-call tracking | none |
| Context engineering | artifact spilling (>2k chars → workspace + preview pointer), recency truncation | sliding window only |
| Independent reviewer model | `reviewer_llm_model` is a different tier by design | judge only, not in-loop |
| Sandboxed execution | Docker code exec, path-sandboxed file IO, SQL guard + read-only txn | none (`delegate_task` shells out to pi) |
| Real RAG | hybrid dense+BM25 → RRF → LLM rerank → grounded gen → per-claim citation verification | FTS5 keyword search |
| Schema migrations | alembic | `CREATE TABLE IF NOT EXISTS` |
| Modern web frontend | Next.js, 16 pages, React Flow run graph, trace timeline | stdlib HTTP server + vanilla JS |

**AgentForge's loop** is a LangGraph state machine — `sketch` → `agent_step`
(self-loop: decide → execute → review → retry) → `synthesize` / `escalate` — with
Postgres, not the LangGraph checkpointer, as the source of truth. `AgentState`
carries `task_id` and an in-memory `route`, nothing else, which is what makes
"resume after human approval" literally *call `run_task(task_id)` again* from a
different worker process. That is a genuinely better resume story than waku has,
because waku has no resume story at all.

---

## Part 3 — the gaps, ranked

Grouped by what they'd buy. "Effort" is rough, assuming familiarity with this
codebase.

### Tier 1 — high payoff, self-contained

**G1. No procedural memory / skills.** *(effort: M)*
The single biggest capability gap. There is no way to teach AgentForge a
repeatable workflow without writing Python. Waku's `SKILL.md` — Anthropic Agent
Skills format, frontmatter always scanned, body loaded only on match — means a
non-programmer can add a capability, and the agent can write one for itself via
`create_skill`. For AgentForge this maps cleanly: a `Skill` table (or per-user
directory) + a matcher in `_gather_prior_context` / `AGENT_STEP_PROMPT`, plus a
Skills page in the web UI. Multi-tenant makes it *more* valuable, not less — skills
become per-user customisation of a shared agent.

**G2. No deterministic eval tier and no release gate.** *(effort: M)*
`src/agentsys/eval/` is entirely LLM-judged. The pytest suite is good but it is
integration testing, not a *battery you can point at a different model*. Waku's
split is the right one: `evals/deterministic/` is 0/1 with no model in the loop and
gates the release; the judge tier is scored and advisory. Concretely:
- add `data/eval/agent_battery.jsonl` cases with `expect_tool` / `expect_in_args` /
  `expect_min_tool_calls`, scored by a pure function with no LLM
- add `make gate` → deterministic must pass 100%, judge must clear a threshold,
  write `eval_report.json` + append `eval_runs.jsonl`
- when a live bug is found, the fix ships with a regression case

**G3. Four-outcome memory/answer scoring, not 0–1 correctness.** *(effort: S)*
`eval/metrics.py`'s judge returns a single `correctness` float. Waku's
PASS / STALE / **INVENTED** / MISS distinction is the one that matters for a
business agent: a confidently fabricated figure and an honest "I don't know" both
score near 0 today, and they are opposite failures. Add the axis to
`OutcomeJudgment`, and add refusal cases to the golden set. AgentForge already has
the fabrication regression test — this generalises it into a metric.

**G4. No fast path / triage.** *(effort: S–M)*
Every request pays `sketch_node` (full LLM) + at least one `agent_step` decision +
`synthesize`. A follow-up like "thanks" or "what did step 2 say?" costs three
model calls minimum. Add a triage classifier at the entry point (`route_entry`
already exists as the seam) that routes trivial or purely-conversational turns to
one small-model reply. Waku's rule applies verbatim: **fail open to the full
path**, so it can only ever add speed.

**G5. No retrieval gate, and no memory retrieval inside the loop.** *(effort: S)*
`sketch_node` unconditionally pulls `k=3` memories, and `agent_step_node` pulls
none. That is backwards on both ends: the sketch pays for retrieval it often
doesn't need, and the loop — where the agent is actually deciding what to do — has
no access to durable memory at all. Add (a) a cheap gate before retrieval, and
(b) gated retrieval inside `agent_step_node` keyed on the *step* being considered,
not just the original request.

**G6. Cost is stored, not derived.** *(effort: S)*
`cost.record_llm_call` stores `cost_usd` from `litellm.completion_cost()` at write
time. Waku stores tokens only and derives dollars at read time from a pricing
table with a documented fallback ladder. The practical difference: a wrong or
missing rate is permanently baked into your analytics today; under waku's approach
correcting the table retroactively fixes every historical figure. `LlmCall` already
stores `prompt_tokens`/`completion_tokens`, so this is a read-path change plus a
pricing module — the column can stay as a cached value.

### Tier 2 — meaningful, more work

**G7. Effectively single-provider.** *(effort: M)*
LiteLLM *makes* multi-provider possible, but nothing exercises it: one hardcoded
`llm_model` default, no provider registry, no live model catalog, no picker, no
per-provider defaults, no test that a non-OpenAI model still tool-calls correctly.
Waku's `PROVIDERS` dict + `catalog.py` + pinned shortlist is ~600 LOC total and
buys: switching models from the UI, regional endpoints, honest "no API key" errors
that name the variable *and the .env file actually read*, and — importantly — the
cross-provider leakage bug they hit (a global `MODEL` env var silently poisoning a
different provider's calls) is a bug AgentForge would hit the moment it added a
second provider.

**G8. No model arena / shootout.** *(effort: M–L)*
There is currently no way to answer "would gpt-5-mini do this task as well for a
third of the cost?" other than by hand. Waku races N models through the *real*
harness in isolated sandboxes and scores Completion (deterministic) + Quality
(independent judge) + cost + latency, live. AgentForge has most of the parts
already — golden dataset, eval runner, judge, cost records, per-task isolation via
`owner_id` + `is_eval` — what's missing is the fan-out, the sandbox story, and the
scoreboard. Build it on the eval runner, not on the task API. Note waku's
discipline: arena results land in **their own JSONL**, never in the agent's real
state, so a benchmark can't pollute memory or analytics.

**G9. No streaming.** *(effort: M)*
The web UI polls at 1.5s. Nothing streams — not tokens, not tool events, not step
transitions. This is the clearest UX gap versus any modern agent product. The
architectural blocker is that AgentForge has **no observer seam**: tracing happens
via the `span()` context manager writing DB rows, so there is no single event
stream to subscribe to. Fixing that is G10, and streaming falls out of it.

**G10. No observer / event-bus seam.** *(effort: M, high leverage)*
Waku's `notify(kind, event)` threaded through loop → registry → graph → sub-agent,
with `compose()` fanning out to N sinks, is what makes streaming, live tool
display, sub-agent visibility, and tracing all one mechanism. AgentForge should add
the same: nodes and tools emit events; sinks are (1) the existing `TraceSpan`
writer, (2) a Redis pub/sub channel per task → SSE endpoint → the web UI, (3) an
OTel exporter (G11). This is the single highest-leverage refactor on the list
because three other gaps close behind it.

**G11. No OpenTelemetry export.** *(effort: S, once G10 exists)*
`TraceSpan` rows power a good in-house trace explorer, but nothing speaks OTel, so
Phoenix / Langfuse / Datadog / Jaeger see nothing. Waku gets this for ~40 lines
because the event stream already exists. Use `openinference.span.kind` values so
LLM/TOOL/CHAIN/AGENT render correctly.

**G12. No parallel step execution.** *(effort: L)*
Documented honestly in the README as a known limitation. Waku's engine shows what
the safe version looks like: waves of ready nodes, **disjoint-key writes with a
collision exception** rather than silent last-write-wins, deterministic merge order
so traces read the same way twice, per-node `max_visits` plus global `max_steps`.
If AgentForge ever adds a "decide names multiple independent next steps" action,
that state-collision rule is the part not to skip.

### Tier 3 — worth knowing, lower priority

**G13. No persona file.** No `SOUL.md` equivalent — no user-editable identity, no
standing-preferences file the agent can append to. For a multi-tenant product this
is per-user system-prompt customisation, and it's a small table plus a prompt slot.

**G14. Agent cannot manage its own memory.** Memory is written only by
`_reflect_and_save_memory` after a task completes. A user who says "no, Cedar's
fiscal year starts in April" has no path to correct a stored fact through
conversation. Waku ships `manage_memory` as a tool. (Append-only for anything
safety-relevant, per `update_soul`'s design.)

**G15. No cross-task consolidation.** Reflection is per-task. There is no
"distil the last N tasks into durable facts", no fact-vs-episode distinction, and
no keyword index alongside the vector store — so a memory that is semantically
mid-match but exactly right by keyword is hard to surface. (The importance blend
`0.7·sim + 0.3·importance/5` is a good partial answer and waku has no equivalent —
worth keeping.)

**G16. No memory-backend contract or conformance suite.** `memory/long_term.py` is
one Chroma implementation. Waku's `semantic/base.py` protocol + conformance tests
exist *because* a second backend silently returned empty lists that the agent
reported as "no memories". If AgentForge ever swaps Chroma (for pgvector, say),
write the contract first.

**G17. No gateways beyond HTTP.** No CLI, no Telegram/Slack/Discord, no voice. The
architectural point is worth adopting even if you never ship a Telegram bot:
*a gateway only moves text*. Right now the FastAPI layer and the agent are close
enough that a second surface would mean re-plumbing.

**G18. No zero-setup local mode.** AgentForge needs Postgres + Redis + Chroma +
Docker before it does anything. Waku needs `pip install` and one API key. For a
portfolio/demo repo this is a real adoption gap — a SQLite + in-memory-queue
"local mode" that runs `run_task` synchronously would let someone try it in 60
seconds. `eval/runner.py` already calls `run_task` synchronously in-process, so
half the work is done.

**G19. Frontend gaps** (the web app is stronger than waku's overall — these are
specific holes): no live streaming (G9), no model switcher (G7), no arena/compare
view (G8), no memory *editing* UI (view only), and no connections tab that
actively probes credentials the way waku's `integrations.py` + `/api/connections/test`
does — AgentForge degrades tools out of the registry silently, so a user with a
broken Google connection sees fewer tools with no explanation.

---

## Part 4 — a suggested order of work

The dependency structure matters more than the ranking:

```
1. G10 observer/event seam ──┬─→ G9  streaming UI
                             ├─→ G11 OTel export
                             └─→ G8  arena (live scoreboard)

2. G2 deterministic battery ─┬─→ G3  four-outcome scoring
   + release gate            └─→ G8  arena (needs a judge-free scorer)

3. G1 skills ────────────────── G13 persona · G14 self-managed memory

4. G4 triage + G5 gated retrieval  (independent, cheap, immediate cost win)

5. G6 derived pricing ───────── G7 provider registry ── G8 arena
```

Start with **G4 + G5** for an immediate latency/cost win with almost no risk (both
fail open), and **G2** because it is what lets you prove any of the rest didn't
make things worse. **G10** is the refactor that unlocks the most.

---

## Part 5 — ideas worth stealing that aren't features

1. **Fail open, always.** Gate, classifier, graph, judge, tool execution — every
   optional layer degrades to the plain path. AgentForge already does this for tool
   registration; extend the habit to every new layer.
2. **A benchmark must not touch real state.** Arena runs get throwaway homes and
   their own JSONL. AgentForge's `is_eval` flag is the right instinct — make sure
   eval runs also stay out of long-term memory and analytics aggregates.
3. **Store the invariant, derive the variable.** Tokens are ground truth; dollars
   are derived. Same reasoning applies to anything with a changing rate.
4. **Render diagrams from data.** `graph.describe()` → the dashboard chart, never a
   hand-drawn picture. AgentForge's `agentGraph.ts` already builds from real run
   data — good; keep it that way and resist hardcoded topology.
5. **A model that judges must not be a model that competes.** Already honoured via
   `reviewer_llm_model`; make it explicit in the eval judge too (it is —
   `judge_outcome` uses `reviewer_llm_model`).
6. **Write the contract before the second implementation.** The `FactStore`
   conformance suite exists because a partial second implementation shipped and
   failed silently.
7. **Comment the live bug, with the measurement.** waku's most useful comments are
   dated observations ("measured 25s vs 6s", "watched kimi-k3 return empty at
   2048"). AgentForge's README already does this beautifully in "Real bugs found
   and fixed" — push that style down into the code.
8. **The footprint ladder.** Every registered tool ships in every prompt. Before
   adding a core tool: can it be a skill? a CLI? a tool behind a flag? AgentForge
   already conditionally registers Google/code-exec tools — formalise it.
