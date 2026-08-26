# AgentForge — where we are and what's left

Companion to [WAKU_COMPARISON.md](WAKU_COMPARISON.md), which is the analysis
this plan came out of. That document ranks 19 gaps (G1–G19); this one tracks
what's been closed, what's in flight, and what order the rest should happen in.

Last updated: 2026-08-25 (after G11, G2, G3, G6 and G4 landed).

---

## The goal, in one line

Make AgentForge **legible** — the system explains itself, proves what it did,
and can be measured — without giving up the production properties (auth,
durability, HITL, recovery) that waku doesn't have and can't easily get.

---

## Status at a glance

| | Item | State |
|---|---|---|
| G10 | Observer / event seam | **Done** |
| G9 | Live streaming (SSE) instead of polling | **Done** |
| — | System view: diagram-as-navigation | **Done** |
| — | Nav regrouped, live counts | **Done** |
| — | Always-present Ask dock | **Done** |
| — | Artifact links (open the files a task wrote) | **Done** |
| — | **Visual verification of all of the above** | **BLOCKED** |
| G11 | OpenTelemetry export | **Done** |
| G2 | Deterministic eval battery + release gate | **Done** (paid tiers unrun) |
| G3 | Four-outcome scoring (PASS/STALE/INVENTED/MISS) | **Done** |
| G4 | Triage fast path | **Done** |
| G5 | Retrieval gate + mid-loop memory | Next |
| G8 | Compare / Arena page | Not started |
| G1 | Skills (procedural memory) | Not started |
| G6 | Derive cost at read time | **Done** |
| G7 | Provider registry + model picker | Not started |
| G13 | Persona / system-prompt file | Not started |
| G14 | Agent manages its own memory | Not started |
| G15 | Cross-task consolidation | Not started |
| G16 | Memory backend contract + conformance suite | Not started |
| G12 | Parallel step execution | Not started |
| G17 | Gateways beyond HTTP | Not started |
| G18 | Zero-setup local mode | Not started |

---

## Phase 0 — shipped (PR #4)

Branch `feature/system-view`, based on `feature/agentforge-dashboard-ui`.

| Commit | What |
|---|---|
| `0df8976` | System view driven by the compiled graph; nav regrouped with live counts |
| `a07e164` | Event seam + SSE streaming + always-present Ask dock |
| `2fe83b0` | Stopped linking config cards to the unwired settings mockup |
| `d3ca540` | Task artifacts — open the files a task actually produced |
| `85d9ffd` | OpenTelemetry export (G11) |
| `ace5396` | Judge-free eval tier + release gate (G2) |
| `cb5e531` | Four-outcome scoring + grounding check (G3) |
| `295f917` | Read-time pricing, incl. cached tokens (G6) |
| `b9ea457` | Triage fast path (G4) |

**New API surface**

```
GET /v1/system/topology            shape of the system, off the compiled graph
GET /v1/system/summary             live counts + 24h per-node activity
GET /v1/tasks/{id}/events          SSE: span_start / span_end / task_status
GET /v1/tasks/{id}/artifacts       files the task produced
GET /v1/tasks/{id}/artifacts/content
```

**The two rules everything above follows**

1. **Existence comes from the system; only the gloss is authored.** Nodes and
   edges are read off `build_graph().get_graph()`, tools off the live
   registry, guardrails off `settings`. A hand-kept list would drift; these
   can't.
2. **Telemetry must never break a run.** Every publish is fail-open. Redis
   down costs freshness, never correctness — the durable `TraceSpan` row is
   written either way.

**Verified:** every endpoint against the running Docker stack — topology
(6 nodes, 9 edges, 14 tools, MCP attribution, approval flags), real
`span()` → Redis → subscriber with truncation and timing, artifact
traversal (4 attacks → 404) and cross-user isolation (404). `tsc` clean,
`next build` passes 18 routes.

**Not verified:** anything visual. See below.

---

## BLOCKED — visual verification

Four commits of UI currently rest on types compiling and a build passing.
Nothing has been looked at.

**What unblocks it:** sign in at `localhost:3000` (Google OAuth). Signing in
is the owner's action, not something the assistant should do on their behalf.

**What gets checked the moment it's unblocked**

- the `agent_step` self-loop arc — the continuous-reasoning story, and the
  one shape that can't be sanity-checked from types
- `color-mix` opacity modifiers on CSS-variable colours (`bg-role-human/15`)
- the 6-tile KPI row wrapping
- the Files panel at 300px in the task context column
- the Ask dock's live step list against a real run

**Do not add more UI until this clears.** The stack of unchecked frontend is
already larger than it should be.

---

## Phase 1 — prove it works (do this next)

The theme: right now nothing can tell you whether a change made the agent
better or worse.

### G11 · OpenTelemetry export — *small, do it first*

The event seam already exists, so this is a second subscriber, not new
instrumentation. Emit spans with `openinference.span.kind` so Phoenix /
Langfuse / Datadog render LLM/TOOL/CHAIN/AGENT correctly.

*Why now:* it's the cheapest item on the list and it's only cheap because
G10 landed. Do it while that's fresh.

### G2 · Deterministic eval battery + release gate — *the keystone*

`src/agentsys/eval/` is 100% LLM-judged today. The pytest suite is
integration testing, not a battery you can point at a different model.

- `data/eval/agent_battery.jsonl` — cases with `expect_tool`,
  `expect_in_args`, `expect_min_tool_calls`, scored by a **pure function
  with no LLM in the loop**
- `make gate` — deterministic must pass 100%, judge must clear a threshold;
  writes `eval_report.json` and appends `eval_runs.jsonl`
- every live bug fixed from here ships with a regression case

*Why it's the keystone:* it's what lets you prove any later change didn't
make things worse. Nothing after this is safe without it.

### G3 · Four-outcome scoring

`eval/metrics.py` returns one `correctness` float, so a confidently
fabricated figure and an honest "I don't know" both score near zero — and
they are opposite failures. Add PASS / STALE / **INVENTED** / MISS, and add
refusal cases to the golden set. The existing fabrication regression test
generalises into this.

---

## Phase 2 — make it cheaper and smarter

### G4 · Triage fast path

Every request pays `sketch` (full LLM) + `agent_step` + `synthesize`. A
follow-up like "thanks" or "what did step 2 say?" costs three model calls
minimum. Classify at the entry point (`route_entry` is already the seam) and
route trivial turns to one small-model reply. **Fails open to the full
path**, so it can only ever add speed.

### G5 · Retrieval gate + mid-loop memory

Backwards on both ends today: `sketch_node` unconditionally pulls `k=3`
memories; `agent_step_node` pulls none — so the place the agent actually
decides what to do has no access to durable memory.

- gate the sketch retrieval with a cheap model ("does this need memory?")
- add gated retrieval **inside** `agent_step_node`, keyed on the step being
  considered rather than the original request

*Cheap, low-risk, immediate latency and cost win. Both fail open.*

### G6 · Derive cost at read time

`cost.record_llm_call` stores `cost_usd` from `litellm.completion_cost()` at
write time, so a wrong or missing rate is permanently baked into analytics.
Store tokens (already done) and derive dollars at read time from a pricing
table — then fixing a rate retroactively corrects every historical figure.

---

## Phase 3 — choice and comparison

### G7 · Provider registry + model picker

LiteLLM makes multi-provider possible; nothing exercises it. No registry, no
catalog, no picker, no test that a non-OpenAI model still tool-calls. Note
the bug waku hit and documented: a global `MODEL` env var silently poisoning
a different provider's calls, where the retrieval gate then *failed open* and
reported the failure as a healthy decision. AgentForge will hit that the day
it adds a second provider.

### G8 · Compare / Arena page

No way to answer "would a cheaper model do this as well?" except by hand.
Race N models through the **real** harness in isolated sandboxes; score
Completion (deterministic, from G2) + Quality (independent judge) + cost +
latency.

Two disciplines to copy verbatim:
- the referee must be a model that **isn't racing**
- results land in **their own store**, never in the agent's real state, or a
  benchmark pollutes memory and analytics

*Depends on G2 — without a judge-free scorer the arena is just vibes.*

---

## Phase 4 — adaptability

### G1 · Skills (procedural memory) — *biggest capability gap*

No way to teach AgentForge a repeatable workflow without writing Python.
`SKILL.md` in the Anthropic Agent Skills format: frontmatter always scanned
(cheap), body loaded only on match, referenced files read only on request.
A `Skill` table or per-user directory + a matcher in `AGENT_STEP_PROMPT` +
a Skills page. **Multi-tenancy makes this more valuable, not less** — skills
become per-user customisation of a shared agent.

### G13 · Persona file · G14 · Self-managed memory · G15 · Consolidation

- **G13** — no user-editable identity or standing-preferences file. For a
  multi-tenant product that's per-user system-prompt customisation.
- **G14** — memory is written only by post-task reflection. A user who says
  "no, Cedar's fiscal year starts in April" has no way to correct a stored
  fact through conversation. Ship a `manage_memory` tool; keep anything
  safety-relevant append-only.
- **G15** — reflection is per-task; there's no cross-task distillation, no
  fact-vs-episode split, and no keyword index beside the vector store.
  (Keep the `0.7·sim + 0.3·importance` blend — waku has no equivalent.)

### G16 · Memory contract before a second backend

If Chroma is ever swapped for pgvector, **write the contract and its
conformance suite first**. waku's exists because a partial second
implementation shipped and silently returned empty lists the agent then
reported as "no memories".

---

## Phase 5 — bigger bets, not scheduled

- **G12 · Parallel steps.** Documented honestly as a limitation. If a decide
  call ever names multiple independent next steps, copy the disjoint-key
  write rule with a **collision exception** rather than silent last-write-wins.
- **G17 · Gateways beyond HTTP.** CLI, Slack, Telegram. The principle is
  worth adopting even if none ship: *a gateway only moves text*.
- **G18 · Zero-setup local mode.** Needs Postgres + Redis + Chroma + Docker
  before it does anything. A SQLite + synchronous `run_task` mode would let
  someone try it in 60 seconds — `eval/runner.py` already calls `run_task`
  in-process, so half the work exists.

---

## Loose ends

- **Stale security copy.** [settings/page.tsx](../web/app/(shell)/settings/page.tsx)
  still says *"No multi-tenancy yet — the API is open to anyone who can
  reach it."* That is false since `84231c0` shipped auth and per-user
  isolation. A security claim wrong in the reassuring direction. One-line fix,
  left alone because it's product copy outside this PR's scope.
- **`/settings` and `/automations` are unwired previews** with every control
  disabled. Fine as placeholders, but they're linked from the nav.
- **`bash.exe.stackdump`** is untracked junk in the repo root.

---

## Suggested order

```
NOW      unblock visual verification          ← everything else waits on this

then     G11 OTel  (cheap, seam is fresh)
         G2  eval battery + gate  ← keystone, unblocks G3 and G8
         G3  four-outcome scoring

then     G4  triage      ┐ independent, cheap,
         G5  gate+memory ┘ immediate cost win

then     G6 pricing → G7 providers → G8 arena

then     G1 skills → G13 persona → G14 self-managed memory
```

Start Phase 1 with **G4 + G5** if you want a visible win first — both fail
open, both are small, and they cut cost and latency on every single request.
But **G2 is what makes everything after it safe**, so it shouldn't slip far.
