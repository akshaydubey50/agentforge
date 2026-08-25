# Phase 7C Evaluation Note

Status: Phase 7C design and implementation note.

Phase 7C adds a production evaluation layer for AI quality. It does not replace
deterministic AgentForge safety tests, does not add runtime DeepEval tracing,
and does not start Phase 7D guardrails or security work.

## 1. Existing Eval Stack

| Layer | Type | Offline/online | Network/model | Release role | Current metric | Data source | Output |
|---|---|---|---|---|---|---|---|
| Pytest Tier 1 | Deterministic | Offline | No network/key by design | Hard blocking | Pass/fail invariants | Curated tests in `agentsys.eval.gate.UNIT_SUITES` | Pytest summary |
| Battery Tier 2 | Deterministic scoring over real runs | Offline | Real model/tools may be needed to run cases | Hard blocking in `make gate` | 0/1 case verdicts | `data/eval/agent_battery.jsonl`, DB `Task`/`Subtask`/`ToolCall`/`Escalation` rows | Console verdicts and `data/eval/eval_report.json` |
| Judge Tier 3 | Probabilistic LLM judge | Offline | Requires judge model/key and running stack | Threshold-based in `make gate` | Outcome correctness, invented/stale/miss/pass, tool-call precision, escalation correctness, step efficiency | `data/eval/agent_golden_tasks.json`, DB run records, grounding signal | `data/eval/agent_results/latest.json`, gate report |
| Agent grounding heuristic | Deterministic signal | Offline | No | Evidence to judge and tests | Ungrounded significant figures | Final answer plus tool outputs | `GroundingReport` |
| RAG eval | Mixed probabilistic and deterministic | Offline | Requires model/embeddings/Chroma | Advisory/benchmark today | Correctness, citation precision, citation coverage, retrieval relevance, confidence | `data/eval/golden_qa.json`, RAG answer pipeline | `data/eval/results/*.json`, `comparison.md` |
| RAG citation verification | Probabilistic plus computed confidence | Runtime pipeline and offline eval | Requires model | Runtime quality signal | Supported claims, uncited claims, completeness, confidence | Retrieved chunks and generated answer | `CitationVerification`, `ConfidenceBreakdown` |
| Phase 6 context benchmarks | Deterministic | Offline | No for pure benchmarks | Pytest blocking | Token reduction, evidence preservation, route unchanged | Representative fixtures in `agentsys.context` | Pytest assertions |
| Phase 7B telemetry | Observability only | Runtime | OTel export optional | Not an eval gate | Safe span/LLM/tool metadata export | `TraceSpan`, `ToolCall`, `LlmCall` | OTel attributes, Redis/SSE, DB rows |

## 2. What Pytest Owns

Pytest owns deterministic invariants and release safety. Examples include tool
schema validation, policy decisions, approval binding, execution retry and
effect safety, verification routing, Gmail draft behavior, RAG auth, synthesis
redaction, LLM retry classification, memory/context pure helpers, and Phase 7B
telemetry fail-open behavior.

These checks remain hard blockers. An LLM judge may report quality signals, but
it must not override policy, approval, ownership, credential, or side-effect
safety assertions that code can decide.

## 3. What `eval/gate.py` Owns

`src/agentsys/eval/gate.py` owns the release gate orchestration:

- Tier 1: pure pytest suites, 100 percent pass required.
- Tier 2: real graph battery runs, deterministic 0/1 scoring, 100 percent pass required.
- Tier 3: stochastic judge tier, score threshold and trend history.

Phase 7C should extend this model without replacing it. Deterministic tiers
stay first and cheapest. Probabilistic quality metrics are thresholded and
reported as pass rate, average score, and critical-case minimums rather than
"every stochastic judgment must always be perfect."

## 4. Existing RAG Metrics

The existing RAG package already measures:

- Answer correctness with an LLM judge against expected facts or an expected refusal.
- Citation precision from citation verification checks.
- Citation coverage from supported cited claims over total factual claims.
- Retrieval relevance from expected source files actually cited.
- Confidence from retrieval confidence, citation coverage, and completeness.

This is enough to avoid adding RAGAS in Phase 7C. DeepEval can be used only as
an optional standard wrapper where it adds comparable faithfulness, answer
relevance, or contextual relevance reporting.

## 5. Existing LLM Judge

`agentsys.eval.metrics.judge_outcome()` grades final task outcomes against
`GoldenTask.expected_outcome`. It classifies outcomes as `pass`, `stale`,
`invented`, or `miss` and uses deterministic grounding evidence when available.

The current judge remains useful. DeepEval should complement it with standardized
metric names and optional task/trajectory/tool/RAG scoring, not replace the
existing run harness or grounding logic.

## 6. Missing Quality Metrics

Current gaps are:

- A standard adapter from AgentForge runtime evidence into eval cases.
- A production-representative golden dataset for task, trajectory, tool, RAG,
  failure/recovery, and long-horizon quality cases.
- Probabilistic answer relevance separate from exact expected outcome grading.
- Task completion scoring for nuanced task outcomes.
- Tool argument quality scoring where exact deterministic matching is not enough.
- Compact trajectory quality scoring from normalized safe events.
- Clear cost and skip behavior for judge-backed evals.
- A future online eval metadata path that does not run judges synchronously on
  production requests.

## 7. DeepEval Role

DeepEval belongs in eval/test tooling only. It should be optional for normal
AgentForge startup and imported only behind the eval adapter/runner.

Use it for:

- `AnswerRelevancyMetric` when final answer relevance cannot be decided by code.
- `ToolCorrectnessMetric` for standardized expected-tool scoring.
- `ArgumentCorrectnessMetric` for semantic argument quality on selected cases.
- `FaithfulnessMetric` and `ContextualRelevancyMetric` for optional RAG/evidence
  parity checks when retrieval context is available.

Defer or reject for now:

- DeepEval production tracing.
- Plan quality/adherence metrics unless current runtime records contain enough
  structured plan data.
- Step efficiency as an LLM trajectory metric until normalized traces are
  stable; keep deterministic step ceilings first.
- Conversation metrics except for future multi-turn tasks that explicitly need them.
- RAGAS, because no concrete RAG metric gap remains after current RAG eval plus
  selected DeepEval metrics.

## 8. Adapter Architecture

AgentForge records stay the source of truth:

```text
Task / TraceSpan / ToolCall / LlmCall / Review / verification output
  -> safe eval adapter
  -> normalized eval case
  -> optional DeepEval test case and metrics
  -> eval report file / gate tier summary
```

The adapter must not instrument runtime execution. It reads existing records or
synthetic fixtures, normalizes them, removes sensitive bodies, and constructs
only the fields needed by a metric.

Safe normalized trajectory events should look like:

```text
actor=agent operation=agent_step result=ok
actor=agent operation=tool_call tool=web_search result=ok
actor=system operation=verification decision=retry result=failed
actor=agent operation=agent_step result=replan
actor=system operation=verification decision=pass result=ok
```

## 9. Dataset Strategy

Create a small version-controlled dataset under `data/eval`. Cases must be
synthetic or redacted and production-representative, not huge.

Initial categories:

- Normal tasks: web research, RAG answer, memory continuation, tool call,
  Gmail draft, MCP read.
- Failure/recovery: tool failure, verification fail, replan, retryable provider
  error, optional memory unavailable, RAG unavailable.
- Action safety: reference deterministic assertions for approval required,
  deny destructive, no ambiguous retry, and no cross-user access.
- Long horizon: multi-step planning, multiple tools, memory retrieval, context
  compression, replanning.

Do not create Phase 7D adversarial/security datasets here.

## 10. Offline Eval Strategy

Phase 7C primarily implements offline evaluation:

- PR/local: small critical dataset, deterministic adapter checks, optional
  DeepEval quality metrics when installed and configured.
- Manual/nightly: larger golden subset with judge-backed metrics.
- Model or threshold changes: run the quality suite and compare against
  stored baselines.

Offline reports should live as files under `data/eval`, not a new database
table.

## 11. Online Eval Strategy

Online eval is designed but not fully implemented in Phase 7C:

- Sample production runs asynchronously.
- Use safe run metadata and redacted/normalized events only.
- Do not run an LLM judge synchronously on every request.
- Do not authorize actions based on online eval scores.
- Link future scores to task/span IDs and external observability traces.

## 12. Metric Thresholds

Thresholds start conservative and baseline-aware:

| Metric | Initial policy | Reason |
|---|---|---|
| Deterministic expected-tool check | Blocking, exact dataset expectation | Code can decide it cheaply |
| Deterministic expected-args check | Blocking when exact or substring args are declared | Code can decide it cheaply |
| Task completion | Threshold-based, baseline tracked | Judge can drift |
| Answer relevance | Threshold-based, baseline tracked | Useful but stochastic |
| Tool correctness | Blocking when deterministic-only, threshold when LLM optimality is enabled | Avoid paying for obvious checks |
| Argument correctness | Advisory or thresholded on selected cases | Semantic args can be judge-dependent |
| RAG faithfulness/context relevance | Advisory until parity with existing RAG eval is measured | Existing RAG eval is already mature |
| Step efficiency | Deterministic step ceiling first, optional advisory score later | Shortest path is not always best |

Avoid blanket 0.9 thresholds. Prefer current baseline, pass-rate regression, and
critical-case minimums once measured.

The current `0.7` quality floor is bootstrap behavior for the first optional
DeepEval tier. It is intentionally conservative and easy to inspect, not an
industry-standard magic number. Once real AgentForge DeepEval reports exist,
replace it with a measured baseline plus explicit regression tolerance rather
than inventing separate per-metric floors without data.

## 13. CI/Release Integration

Keep the existing gate ownership:

- Tier 1 remains deterministic and must pass without DeepEval installed.
- Tier 2 remains deterministic scoring over real runs.
- Tier 3 quality can include the existing judge and optional DeepEval summary.

CI should always run deterministic tests. DeepEval metrics should be opt-in or
skipped when the package or judge configuration is unavailable.

## 14. Cost Controls

Configuration should support:

- Judge model.
- Dataset subset.
- Case count/sample count.
- Metric selection.
- Skip mode when DeepEval or judge credentials are unavailable.

Do not evaluate every case with every metric. Do not commit secrets. Do not
print full prompts, Gmail bodies, private documents, raw memory bodies, OAuth
credentials, or raw tool outputs.

## 15. Failure Behavior

- DeepEval unavailable: deterministic suites still run and quality tier reports
  `skipped` or `unavailable`.
- Judge API unavailable: quality tier fails or reports unavailable according to
  gate policy, without affecting normal application startup.
- Normal AgentForge runtime: unaffected.
- Adapter parse errors: fail the eval command clearly before running judges.

## 16. Explicitly Excluded Work

Phase 7C does not implement:

- NeMo Guardrails.
- Guardrails AI.
- Prompt-injection runtime filters.
- PII runtime guardrails.
- Red-team security dataset.
- MCP trust changes.
- Langfuse SDK.
- Logfire.
- RAGAS.
- Frontend eval UI or Waku UI.
- Gmail send.
- Production synchronous online judge.
- New vector DB.
- New eval database tables.
- DeepEval runtime tracing.

## 17. Illustrative Example

Task:

```text
Research Agentic AI authentication and summarize findings.
```

Deterministic checks:

- approved retrieval path used when the task contract requires retrieval.
- Verification passed.
- No denied tool used.

Illustrative DeepEval scores, not measured:

| Metric | Score |
|---|---:|
| TaskCompletion | 0.92 |
| AnswerRelevance | 0.89 |
| ToolCorrectness | 1.00 |
| StepEfficiency | 0.84 |

Gate:

```text
PASS
```

These values are examples only. Measured reports must label the model, dataset,
latency, unavailable/skipped metrics, and thresholds used.

## 18. Ponytail Reductions

- No new DB table: offline eval reports are enough.
- No new trace system: `TraceSpan`, `ToolCall`, and `LlmCall` remain runtime evidence.
- No DeepEval tracing in runtime: adapter only.
- No RAGAS: current RAG eval plus selected DeepEval metrics is sufficient.
- No large dataset: start with a small representative set.
- No ten-metric suite: start with task completion, answer relevance, tool
  correctness, argument correctness where useful, and optional RAG faithfulness
  or contextual relevance.

## 19. Implemented Phase 7C Surface

Files:

- `requirements-eval.txt`: optional eval-only DeepEval pin.
- `data/eval/quality_golden_tasks.json`: small synthetic/redacted quality dataset.
- `src/agentsys/eval/quality_dataset.py`: dataset parser and duplicate-id validation.
- `src/agentsys/eval/quality_adapter.py`: safe adapter from `Task`,
  `TraceSpan`, `ToolCall`, and `LlmCall`-like records into normalized eval input.
- `src/agentsys/eval/deepeval_runner.py`: optional DeepEval metric bridge and
  report persistence under `data/eval/deepeval_quality/`.
- `src/agentsys/eval/quality_runner.py`: explicit offline harness that can run
  quality cases through the existing AgentForge graph.
- `scripts/run_deepeval_quality.py`: dry-run-by-default CLI.
- `tests/test_phase7c_evaluation.py`: deterministic infrastructure tests.

Configuration:

- `DEEPEVAL_ENABLED=false` by default.
- `DEEPEVAL_JUDGE_MODEL` overrides the quality judge model.
- `DEEPEVAL_SAMPLE_COUNT` caps the optional quality tier.
- `DEEPEVAL_DATASET_SUBSET` filters by category prefix.

Commands:

```text
python scripts/run_deepeval_quality.py
python scripts/run_deepeval_quality.py --run-agent --with-deepeval --max-cases 3
```

The first command validates and summarizes the dataset only. The second command
executes real AgentForge cases and optional DeepEval metrics, and therefore may
require running services, a judge key, and the optional eval dependencies.

## 20. DeepEval Metric Decision

Selected:

- `answer_relevance`: useful for end-to-end final response quality.
- `tool_correctness`: useful standardization for expected tool use; keep exact
  deterministic checks first.
- `argument_correctness`: useful only for selected semantic argument cases.
- `faithfulness`: useful optional RAG/evidence quality check when retrieval
  context exists.
- `contextual_relevance`: useful optional RAG retriever/context quality check.
- `task_completion`: implemented through DeepEval `GEval` because Phase 7C does
  not adopt DeepEval runtime tracing.

Deferred:

- Built-in trajectory `TaskCompletionMetric`, `StepEfficiencyMetric`,
  `PlanQualityMetric`, and `PlanAdherenceMetric`, because current DeepEval
  trajectory metrics are tracing-oriented and Phase 7C explicitly avoids
  DeepEval runtime tracing.
- Conversation metrics, until a multi-turn quality dataset exists.
- RAGAS, because no concrete gap remains after existing RAG eval plus selected
  DeepEval metrics.

`step_efficiency` remains deterministic-first through expected step ceilings and
trajectory summaries. A judge may be added later only after baseline stability
is measured.
