# Phase 9A Product UX Architecture

Status: Phase 9A closure review complete. No production frontend rewrite.

Phase 9A defines AgentForge as an AI Agent Execution Studio: a live operating
surface for setting goals, watching agent execution, approving side effects,
inspecting context, and revisiting durable run evidence.

## 1. Product Vision

AgentForge should make invisible agent behavior visible without turning the
product into a raw trace viewer.

The primary object is a run: a user gives a goal, AgentForge plans and executes
bounded steps, chooses context, retrieves memory and knowledge, calls tools,
applies deterministic policy, pauses for approval, verifies effects, recovers
from failures where safe, and writes durable evidence.

The product should answer five questions quickly:

- What is the agent trying to do?
- What is running right now?
- What did the LLM decide versus what did the runtime enforce?
- What evidence, memory, policy, and context shaped the action?
- What was the cost, latency, risk, result, and recovery path?

AgentForge is not a generic chatbot, a static workflow builder, or an admin
dashboard. It is a live execution studio for understanding and steering agentic
work.

## 2. Target User

Primary users:

- Engineers building, debugging, and operating agent workflows.
- Technical founders or operators who need to approve external actions and
  understand why an agent is blocked.
- Evaluators reviewing quality, safety, and regressions across runs.

Secondary users:

- Security reviewers inspecting guardrails, MCP trust, and policy decisions.
- Knowledge maintainers debugging RAG ingestion and retrieval.

The product should be dense enough for engineers, but the default view should
remain readable for a human approver who only needs to know exactly what effect
is being authorized.

## 3. Primary Workflows

Start work:

- Enter a goal from Mission Control or Workspace.
- Pick a representative task template only as a starter, never as a hardcoded
  workflow.
- Attach supported files when backend upload support is available.

Watch work:

- Read the user/agent conversation.
- Watch the graph grow as runtime events arrive.
- Follow the current node, or pause auto-follow and inspect history.
- Use the timeline to understand timing and execution order.

Approve work:

- Inspect exactly one proposed side effect.
- See recipient, subject, arguments, risk, policy decision, and fingerprint.
- Approve or reject.
- Understand that changed arguments require a fresh approval.

Debug work:

- Click a failed, retried, blocked, or recovered node.
- Inspect validated inputs, outputs, policy, trace, token/cost metrics, and
  verification evidence.
- Drill from observability metrics to a run, then to a node/span.

Improve work:

- Compare eval results across datasets.
- Inspect deterministic safety failures separately from probabilistic quality
  scores.
- Use Memory and Knowledge screens to debug what context the agent can retrieve.

Govern work:

- Review deterministic policies and current tool classifications.
- Inspect MCP server trust, schema fingerprints, and changed-schema warnings.
- Inspect security events without exposing raw sensitive payloads.

## 4. Complete Information Architecture

Final production navigation should use five plain-language groups. This keeps
all major surfaces reachable while avoiding a 20-item flat sidebar.

```text
WORK
  Mission Control
  Workspace
  Runs
  Approvals

CAPABILITIES
  Knowledge
  Memory
  Tools
  MCP
  Integrations

QUALITY
  Observability
  Evals

GOVERN
  Policies
  Security

SYSTEM
  Settings
```

Rationale:

- Workspace belongs near Mission Control because it is where live work happens.
- Runs and Approvals are operational, not configuration.
- Knowledge, Memory, Tools, MCP, and Integrations are build-time capability
  surfaces. MCP and Integrations share the capability-management mental model,
  but MCP remains its own child because schema trust drift is a specialized
  workflow.
- Observability and Evals are quality surfaces because one watches production behavior
  and the other measures quality or release readiness.
- Policy and Security are governance surfaces and should not be buried under
  Settings.
- Context Inspector is not a standalone top-level screen. It appears in
  Workspace and Run Detail, with a deep link from a selected LLM/context event.

Items intentionally combined:

- Run Detail and historical "Replay" use the same Workspace visualization. The
  label should be `Run Detail` or `Execution History` until deterministic
  replay semantics exist. A historical animation may be called a replay
  visualization, not task re-execution.
- Tool Detail lives inside Tools, not as a separate nav item.
- MCP Tool Discovery lives inside MCP, not as a separate nav item.
- Eval Datasets live inside Evals.
- Trace detail lives inside Observability and Run Detail.
- Context Inspector is a Workspace/Run Detail panel, not a standalone nav item.
- Agent Configuration starts inside Settings because the backend does not yet
  expose a full multi-agent builder.

## 5. Navigation

Use a scalable sidebar with grouped sections, not a flat 20-item list.

Default desktop sidebar:

```text
AgentForge

WORK
  Mission Control
  Workspace
  Runs
  Approvals

CAPABILITIES
  Knowledge
  Memory
  Tools
  MCP
  Integrations

QUALITY
  Observability
  Evals

GOVERN
  Policies
  Security

SYSTEM
  Settings
```

Navigation behaviors:

- Badges: active runs, pending approvals, failed runs, unhealthy tools/MCP.
- Collapsed rail: icon-only with tooltips and status dots.
- Current run shortcut: when a run is active, a persistent top bar chip opens
  the live Workspace.
- No dead links in the prototype or production app.

## 6. Screen Inventory

Mission Control:

- Start or resume work.
- Active runs, waiting approvals, recent failures, recent completions, tool/MCP
  health, recent memory changes, eval quality snapshot.

Workspace:

- Chat + live execution graph + inspector + run status + timeline + approvals.
- This is the flagship screen.

Runs:

- Execution history with filters for status, approvals, retries, tools, tokens,
  cost, owner, duration, and failures.

Run Detail:

- Historical Workspace view using durable task, trace, escalation, tool, LLM,
  memory, and artifact records.

Knowledge:

- Source and document inventory, ingestion/indexing status, retrieval
  playground.

Memory:

- Durable memory inventory with type, provenance, last used, importance,
  supersession, archive status where supported.

Tools:

- Native and MCP tool registry, health, action type, risk, approval posture,
  execution safety, schema, recent calls.

MCP:

- Configured servers, connection status, transport, discovered tools, schema
  fingerprint, trust status, changed-schema warnings.

Integrations:

- Connected external providers, scopes, consent state, errors. Gmail send is
  not implied.

Approvals:

- Human-in-the-loop inbox with exact effect authorization, policy, risk, args,
  approve/reject decisions.

Policies:

- Policy Explorer for current deterministic code-defined policy. Not a fake
  policy editor.

Observability:

- Agent-specific latency, token, cost, error, retry, recovery, dependency, and
  active-run views.

Evals:

- Datasets, deterministic safety checks, optional DeepEval quality results,
  trends, case detail.

Security:

- Guardrail stage, risk category, blocked/flagged counts, MCP trust changes,
  secret detections, safe metadata only.

Settings:

- Deployment and user settings that the backend actually supports. Agent config
  is read-only or narrowly editable until backend support exists.

Support classification:

| Screen / component | Classification | Notes |
| --- | --- | --- |
| Mission Control | Supported with small Phase 9B adapter | Current `/v1/system/summary`, tasks, escalations, analytics, tools, and memory can populate an operational landing page. |
| Workspace chat | Supported with small Phase 9B adapter | Current task create/upload/message APIs exist; follow-up messages are terminal-state only today. |
| Live execution graph | Supported with small Phase 9B adapter | `@xyflow/react`, task detail, trace, escalations, and SSE exist; richer node semantics need projection/enrichment. |
| Run timeline | Supported with small Phase 9B adapter | Can derive from traces, task messages, escalations, artifacts, and SSE. |
| Node inspector | Supported with small Phase 9B adapter | Basic trace/escalation/subtask data exists; exact per-family tabs need enriched safe schemas. |
| Context Inspector | Future / partial | Context engineering exists, but no stable UI-safe context composition endpoint is exposed. |
| Approval UX | Supported by backend now | Escalation decide API, policy snapshots, and args fingerprint binding exist. |
| Runs | Supported by backend now | `/v1/tasks` and `/v1/tasks/{id}` exist; richer columns need analytics joins. |
| Run Detail / Execution History | Supported with small Phase 9B adapter | Current run detail and graph pages already use tasks/traces/escalations separately. |
| Knowledge | Supported with small Phase 9B adapter | RAG service/tools exist; source/document admin and playground need API shaping. |
| Memory | Supported by backend now for read-only list | `/v1/memory` exists; pin/archive/delete mutation should not be shown. |
| Tools | Supported by backend now for read-only registry | `/v1/tools` exists; health/risk/policy details need system/policy enrichment. |
| MCP | Future / partial | MCP discovery and tools exist; trust review/fingerprint product API is a Phase 9B/P1 gap. |
| Integrations | Supported by backend now for Google status/disconnect | Gmail send must stay absent; scopes can be shown from Google status. |
| Policies | Supported by backend now as read-only explorer | Policy is deterministic code, not editable UI config. |
| Evals | Supported with small Phase 9B adapter | Eval scripts/results exist; product API/page shaping is needed. |
| Observability | Supported with small Phase 9B adapter | `/v1/analytics`, `/v1/system/summary`, traces, and OTel export data cover a first version. |
| Security | Future / partial | Guardrails/security tests exist; product endpoint should expose redacted metadata only. |
| Settings | Supported as read-only runtime info | Mutations should be limited to existing safe APIs such as theme/session/integration disconnect. |

## 7. Workspace Architecture

The Workspace must combine chat and live graph. Chat and graph can have focus
modes, but they must not become separate primary experiences.

Required regions:

- Header: run id, status, connection state, elapsed time, tokens, cost, latency,
  current step.
- Left: chat and high-signal run events.
- Center: live execution graph.
- Right: contextual inspector.
- Bottom: time-ordered run timeline.
- Approval overlay or inline inspector panel when a policy gate pauses the run.

Recommended desktop layout for 1440 px:

```text
Header: run status, connection, cost/tokens, controls

+--------------+------------------------------+-----------------+
| Chat         | Live Execution Graph          | Inspector       |
|              |                              |                 |
| Goal         | Goal -> Plan -> Memory ...    | Current node    |
| Agent output |                              | Inputs/Output   |
| Key events   | Approval node selected        | Policy/Metrics  |
+--------------+------------------------------+-----------------+
+---------------------------------------------------------------+
| Timeline: 0.0 Goal, 0.2 Plan, 1.8 Search, 5.0 Approval ...    |
+---------------------------------------------------------------+
```

Panel behavior:

- Chat width: 300-380 px default, collapsible to event rail.
- Inspector width: 340-420 px default, collapsible.
- Graph owns remaining width and is the visual center.
- Resizing should preserve minimum usable widths.
- Desktop supports Studio, Graph Focus, and Chat Focus modes. These are view
  modes for the same run state, not separate pages.
- Below roughly 1280 px, the inspector can move below the chat/graph pair or
  open as a drawer. Below roughly 1080 px, stack panels instead of showing
  three unusable columns.
- When approval is required, the approval card can pin inside the inspector and
  also display a small inline graph affordance.

Run identity/state flow:

```text
Workspace route/run chip -> current run id
current run id -> task detail + trace + escalations + live SSE
durable records + live events -> derived run model
derived run model -> chat summaries + graph nodes/edges + timeline + inspector
approval decision -> escalation API -> refetch durable state -> resume live SSE
```

Chat, graph, timeline, and inspector must all bind to that same derived run
model. Sending a prompt creates or continues one run; it must not fork a
separate chat-only state.

## 8. Workspace Layouts Considered

Layout A: Three-column studio.

- Structure: Chat | Graph | Inspector, timeline along bottom.
- Pros: All core surfaces are visible together; natural for engineering
  workstation use; inspector has stable home; graph remains central.
- Cons: Needs careful responsive behavior below 1280 px; chat can feel narrow
  during long conversations.
- Recommendation: best default.

Layout B: Graph-dominant command center.

- Structure: Full-width graph center, collapsible chat drawer left, inspector
  drawer right, timeline bottom.
- Pros: Strong visual emphasis on live execution; useful for demos and replay.
- Cons: Chat becomes secondary despite being the control surface; approval
  context can feel detached.
- Use as: focus mode, not default.

Layout C: Chat/graph split with execution console.

- Structure: Left half chat, right half graph, bottom console/timeline,
  inspector as slide-over.
- Pros: Good for smaller laptops; chat stays legible.
- Cons: Inspector interrupts flow; graph loses space; harder to compare input,
  output, policy, and evidence at once.
- Use as: 1280 px fallback.

Recommended production default: Layout A with Layout B as graph focus mode and
Layout C behavior below 1280 px.

## 9. Chat Architecture

Chat should be readable, not a raw trace dump.

Message/event card types:

- User request.
- Agent answer.
- Plan summary.
- Approval request summary.
- Tool result summary.
- Verification result summary.
- Recovery event summary.
- Memory saved/retrieved summary.
- Artifact produced.
- Error/blocker summary.

Rules:

- Do not mirror every trace row in chat.
- Detailed runtime data belongs in graph, inspector, and timeline.
- Approval cards in chat should summarize and link to the inspector for exact
  arguments.
- Agent messages must not expose hidden chain-of-thought. Show structured plan,
  selected evidence, and safe rationale metadata instead.

## 10. Live Graph Architecture

The graph visualizes actual runtime execution, not a decorative static
architecture map.

The graph must support dynamic growth. The full topology is not assumed to be
known at run start.

Node categories:

- `goal`: user objective accepted by runtime.
- `plan`: LLM or planner-produced structured work proposal.
- `llm`: model output, selection, review, synthesis, or quick reply.
- `router`: deterministic routing or code-owned branch decision.
- `tool`: native tool call.
- `subagent`: bounded delegated agent loop.
- `memory`: durable memory retrieval or write.
- `knowledge`: RAG/knowledge retrieval.
- `policy`: deterministic policy decision.
- `approval`: human gate.
- `execution`: runtime execution ledger, dedupe, retry, or effect boundary.
- `verification`: expected versus observed outcome.
- `recovery`: stranded task, ambiguous effect, retry, or failover handling.
- `synthesis`: final answer composition.
- `final`: terminal completion/failure/cancelled state.

Visual family system:

Keep semantic `node.type` values for data and inspection, but use a small,
consistent visual grammar:

```text
REASONING  plan, llm, synthesis
ACTION     tool, execution, subagent
CONTEXT    memory, knowledge
CONTROL    policy, approval, guardrail
QUALITY    verification
SYSTEM     goal, router, recovery, final
```

This avoids 15 unrelated node styles while still letting the inspector show
precise semantics.

Actor/source visual distinction:

- LLM: model-owned proposal or generation.
- Runtime: AgentForge validation, policy, routing, ledger, recovery, context,
  verification.
- Tool: external or local operation.
- Memory: durable user/project context.
- Knowledge: source-backed RAG evidence.
- Human: approval decision.

Key sequence to make visible:

```text
LLM proposes: use gmail_create_draft
Runtime validates: typed args accepted
Runtime enforces: policy REQUIRE_APPROVAL
Human authorizes: exact fingerprint approved
Runtime executes: effect ledger checked
Tool performs: Gmail draft created
Runtime verifies: expected draft observed
```

The user should understand this sequence from the graph and timeline without
opening source code.

Graph interactions:

- Click node: open inspector.
- Hover node: compact status, actor, duration, tokens, failures.
- Running node: visible but restrained pulse and active edge.
- Failed node: failure category and short safe reason.
- Retrying node: retry count and previous failure list.
- Policy node: decision, risk, action type, reason.
- Approval node: approve/reject controls when current and pending.
- Memory node: selected/dropped memories and provenance counts.
- Knowledge node: retrieved chunks, scores, source documents.
- LLM node: model, token usage, latency, context composition.
- Verification node: expected, observed, result, retry/recovery trigger.
- Grouped subagent: collapsible nested execution.

Graph controls:

- Fit view.
- Zoom and pan.
- Follow execution toggle.
- Pause auto-follow.
- Recenter current node.
- Collapse repeated operations.
- Show/hide skipped nodes.

## 11. Node Status Model

Use status labels, icons, shape/state, and color. Do not rely on color alone.

Statuses:

- `pending`: known but not started.
- `running`: active now.
- `waiting`: paused for dependency, rate limit, or handoff.
- `approval_required`: waiting for a human.
- `succeeded`: completed normally.
- `failed`: terminal failure.
- `retrying`: current or scheduled retry.
- `blocked`: cannot continue until external condition changes.
- `skipped`: intentionally not run.
- `recovered`: failure occurred and was handled.
- `cancelled`: deliberately stopped by user or operator.

Current backend mapping:

- `Task.status`: pending, running, awaiting_approval, completed, failed,
  cancelled.
- `Subtask.status`: pending, ready, running, needs_revision, done, escalated,
  failed, skipped.
- `TraceSpan.status`: ok/error plus started/ended timestamps.
- `Escalation.status`: pending, approved, rejected, took_over.

Phase 9B should normalize these into a UI status model instead of leaking
backend enum names directly into the graph.

## 12. Timeline

The graph shows topology. The timeline shows time.

Timeline rows:

- Timestamp/offset from run start.
- Event label.
- Actor/source.
- Node link.
- Duration where applicable.
- Status/result.
- Optional compact metadata: tokens, cost, policy decision, retry count.

Selection behavior:

- Selecting a timeline event selects the corresponding graph node.
- Selecting a graph node filters/highlights timeline events for that node.
- Timeline supports current-run auto-scroll and historical scrub.
- Approval pauses should be visually obvious as a gap between approval request
  and approval resolution.

Example:

```text
0.0s  Goal accepted
0.2s  Planning started
1.8s  Plan generated
1.9s  Web search started
3.2s  Web search complete
3.4s  Memory retrieved
4.9s  Gmail draft proposed
5.0s  Policy require_approval
12.3s Human approved
12.5s Execution ledger checked
13.1s Verification passed
13.2s Completed
```

## 13. Inspector

The inspector is contextual. Do not show every tab for every node.

Common fields:

- Node type, actor/source, status, start/end/duration.
- Safe reason/result summary.
- Related trace span ids, task id, subtask id.

LLM node tabs:

- Overview: model, purpose, status, duration.
- Context: budget composition, included blocks, dropped blocks.
- Output: structured result, safe message, plan proposal.
- Metrics: input/output/cached tokens, estimated cost, latency.

Tool node tabs:

- Overview: tool, source, action type, risk, execution safety.
- Input: validated args with sensitive values redacted.
- Output: result summary and artifact links.
- Policy: decision that allowed or gated the call.
- Metrics: duration, retries, dedupe/effect key state.

Policy node tabs:

- Overview: decision, action type, risk, rule reason.
- Snapshot: tool name, fingerprint, argument summary.
- Audit: decision time, approver where relevant.

Approval node tabs:

- Request: exact effect and args.
- Policy: decision/risk/reason.
- Decision: approve/reject/take-over state, actor, time.
- Diff: visible changed-argument warning when applicable.

Memory node tabs:

- Retrieved: selected memories, kind, importance, score, last used.
- Dropped: memory candidates omitted and reason.
- Provenance: source task/message/artifact.

Knowledge node tabs:

- Retrieved chunks, score, source, document, chunk id.
- Index and source state where available.

Verification node tabs:

- Expected.
- Observed.
- Result.
- Recovery/retry trigger.

## 14. Context Inspector

Context Inspector is a differentiating feature and should appear as an
inspector mode for LLM/context events and as a deep-linkable panel.

It visualizes:

- Context window size.
- Reserved output tokens.
- Usable input budget.
- Selected tokens.
- Tokens avoided.
- Mandatory/protected context.
- Conversation and rolling summary.
- Durable memory.
- RAG/knowledge.
- Tool results.
- Artifacts and references.
- Dropped optional context and reason.

Example composition:

```text
Context Window
72% used

System/tool prefix     2,100
Conversation           3,800
Rolling summary        1,200
Memory                   940
Tool results           2,300
RAG                    3,100
Reserved output        4,000
Dropped optional       1,450
Tokens avoided         5,900
```

Safety:

- Do not expose hidden chain-of-thought.
- Do not show raw secrets, OAuth tokens, private bodies, or unsafe malicious
  payloads.
- Show safe runtime metadata and user-appropriate content only.

## 15. Memory UX

Memory is a managed system, not a mysterious chat cache.

Memory categories:

- Preferences.
- Decisions.
- Facts.
- Project knowledge.
- Episodic summaries.
- Pinned items.
- Artifact references.

List fields:

- Kind.
- Content preview.
- Importance.
- Provenance.
- Created/updated time.
- Last used.
- Retrieval count where available.
- Status: active, superseded, archived where supported.

Capabilities:

- Search and filter.
- View provenance.
- View related run.
- View superseded-by relationship.
- Pin/unpin only if backend supports it.
- Archive/delete only if backend supports it.

Terminology must distinguish:

- Conversation summary: lossy task-level summary.
- Durable memory: stored cross-run memory.
- Retrieved memory: selected for one model call.
- Current context: final prompt composition for one call.

## 16. Knowledge UX

Knowledge is RAG/debug infrastructure for source-backed answers.

Main surfaces:

- Sources: upload, Drive, internal sources, future providers.
- Documents: ingestion status, chunk count, embedding status, last indexed,
  failures.
- Retrieval playground: query, retrieved chunks, scores, source documents,
  citation preview.

Useful states:

- Not indexed.
- Indexing.
- Indexed.
- Failed.
- Stale.
- Source disconnected.

The retrieval playground is critical because it lets users debug why a RAG tool
found or missed evidence before blaming the agent loop.

## 17. Tool Registry

Tools deserve a first-class screen.

Tool categories:

- Web.
- Knowledge.
- Files.
- Database.
- Google.
- Code.
- Agent.
- MCP.

Tool card fields:

- Name.
- Description.
- Availability/health.
- Source: native or MCP.
- Action type.
- Risk.
- Execution safety.
- Approval posture derived from deterministic policy.
- Recent success/failure and latency.

Tool detail:

- Typed schema.
- Policy classification and argument-dependent notes.
- Execution safety.
- Recent calls.
- Success/failure.
- Latency.
- Effect key/dedupe behavior when relevant.

Do not allow UI editing until backend supports editable tool definitions or
policy classifications.

## 18. MCP UX

MCP management should be separate from native Tools because trust and schema
change review are first-class.

MCP server fields:

- Server name.
- Transport.
- Command/config summary without secrets.
- Connection status.
- Last discovery.
- Discovered tools count.
- Trust/review status.
- Schema fingerprint.
- Changed-schema warning.

MCP tool fields:

- Tool name and description.
- Input schema.
- Declared action type/risk/execution safety.
- Effective policy posture.
- Fingerprint.
- Guardrail result for description.

Key warning:

```text
Server schema changed
Trust review required before treating this classification as reviewed.
```

The UI should make Phase 7D visible: description scan, MCP output framing,
credential-field protection, and fingerprint-based trust drift.

## 19. Integrations

Show connected services and scopes in understandable language.

Current service examples:

- Google sign-in.
- Gmail search/read and draft creation.
- Google Drive search/read.
- Google Photos read/pick where configured.

States:

- Connected.
- Needs re-consent.
- Disconnected.
- Error.
- Not configured.

Do not imply Gmail send exists. Use "Create drafts" instead of "Send email".

## 20. Approvals

Approval UX must make the exact authorized effect unambiguous.

Approval card fields:

- Agent wants to: action label.
- Tool: exact tool name.
- Target/effect: recipient, path, external service, or record where relevant.
- Arguments: inspectable, redacted where needed.
- Risk and action type.
- Policy decision and reason.
- Execution safety.
- Fingerprint.
- Expiry.
- Related run/node.

Buttons:

- Approve.
- Reject.
- Take over only if backend supports it for that escalation type.

Behavior:

- Approving executes only the stored validated snapshot.
- Changed args visibly require a new approval.
- Expired approvals must explain why a fresh proposal is needed.
- Rejection should be allowed with a note when backend supports it.

## 21. Policies

Initial screen: Policy Explorer.

Do not build a fake Policy Builder while policy is code-defined.

Show deterministic rules:

```text
READ / LOW or MEDIUM      ALLOW
LOCAL_WRITE              REQUIRE APPROVAL
EXTERNAL_WRITE           REQUIRE APPROVAL
HIGH risk                REQUIRE APPROVAL
DESTRUCTIVE              DENY
CRITICAL risk            DENY
UNKNOWN combination      REQUIRE APPROVAL
```

Also show:

- Tool classifications.
- Argument-dependent classification notes.
- Recent decisions.
- Denied actions.
- Approval-required actions.
- Link to policy source/documentation.

## 22. Runs

Runs is execution history.

Columns:

- Goal.
- Status.
- Started.
- Duration.
- Steps.
- Tools.
- Tokens.
- Cost, if available.
- Approvals.
- Retries.
- Owner.
- Last event.

Filters:

- Running.
- Waiting approval.
- Succeeded.
- Failed.
- Recovered.
- Blocked.
- Tool used.
- Date.
- Cost/token threshold.

Clicking a run opens Run Detail, which reuses the Workspace visualization with
historical data.

## 23. Observability

Observability should be agent-specific, not a generic Datadog clone.

Views:

- Active runs and queue/dependency state.
- Task latency.
- LLM latency.
- Tool latency.
- Token usage and cached-token rate.
- Cost by model/purpose.
- Errors by span/tool.
- Retries and recovery outcomes.
- Ambiguous effects.
- Approval latency.
- Dependency failures.

Drill-down path:

```text
metric -> filtered runs -> run detail -> graph node -> trace/span
```

Data should come from AgentForge DB tables and optional OTel/export metadata.
External observability sinks do not replace product state.

## 24. Evals

Evals must clearly separate deterministic safety from probabilistic quality.

Surfaces:

- Datasets.
- Evaluation runs.
- Deterministic pass/fail.
- Optional DeepEval quality metrics.
- Judge availability/skipped state.
- Trend and baseline comparison.
- Case detail with normalized safe trajectory.

Labels:

- Deterministic safety: release-blocking invariants.
- Probabilistic quality: model/judge-based score, trend, or threshold.

Do not present DeepEval as production runtime safety. It is eval/test tooling.

## 25. Security

Security uses safe metadata only.

Surfaces:

- Blocked attacks.
- Flagged retrieval.
- Secret detections.
- MCP trust changes.
- Guardrail stage.
- Risk category.
- Detector.
- Confidence.
- Safe source/sink metadata.

Do not expose raw secrets, malicious payloads, private Gmail bodies, OAuth
tokens, authorization headers, raw memory bodies, or raw tool outputs unless a
future secure evidence workflow is deliberately built.

## 26. Settings

Settings should expose only supported configuration.

Recommended sections:

- Profile and session.
- Theme.
- API base/deployment information.
- Model configuration: read-only unless backend exposes mutation.
- Runtime limits: read-only initially.
- Agent configuration: model, max steps, memory enabled, knowledge sources,
  context budget, subagent limits, shown as read-only or controlled where
  backend supports it.

Do not create a no-code agent builder until architecture supports multiple
editable agent definitions.

## 27. Frontend Event Model

Current backend events:

- SSE endpoint: `GET /v1/tasks/{task_id}/events`.
- Initial `snapshot` event with current task status.
- Live `span_start`.
- Live `span_end`.
- Live `task_status`.
- `stream_error`.

Current durable history:

- `/v1/tasks/{id}` for task, messages, subtasks.
- `/v1/tasks/{id}/trace` for `TraceSpan` rows.
- `/v1/escalations` for approvals.
- `ToolCall`, `LlmCall`, memory, audit, and analytics data are partly exposed
  through current APIs, not all through node-level endpoints.

Target normalized UI event model:

```text
RUN_STARTED
RUN_STATUS_CHANGED
RUN_COMPLETED

NODE_DISCOVERED
NODE_STARTED
NODE_PROGRESS
NODE_COMPLETED
NODE_FAILED
NODE_SKIPPED

LLM_STARTED
LLM_COMPLETED
LLM_FAILED

TOOL_PROPOSED
TOOL_VALIDATED
TOOL_EXECUTED
TOOL_FAILED

POLICY_DECISION
APPROVAL_REQUIRED
APPROVAL_RESOLVED
APPROVAL_EXPIRED

MEMORY_RETRIEVED
MEMORY_WRITTEN
KNOWLEDGE_RETRIEVED

CONTEXT_BUILT
CONTEXT_DROPPED

VERIFICATION_STARTED
VERIFICATION_COMPLETED

RETRY_SCHEDULED
RETRY_STARTED
RECOVERY_STARTED
RECOVERY_COMPLETED

ARTIFACT_CREATED
SECURITY_GUARDRAIL_RESULT
STREAM_SNAPSHOT
STREAM_ERROR
```

Phase 9B can initially derive many normalized events from existing
`TraceSpan`, `Task`, `Subtask`, and `Escalation` rows, but durable enriched
event records would improve accuracy.

Phase 9B event mapping:

| UI event | Existing backend source | Direct / Derived / Missing | Phase 9B action |
| --- | --- | --- | --- |
| RUN_STARTED | `Task.created_at`, `/v1/tasks` create response, SSE `snapshot` | Derived | Create run header from task row and snapshot. |
| RUN_STATUS_CHANGED | SSE `task_status`, `Task.status` | Direct | Normalize backend status into UI status labels. |
| RUN_COMPLETED | `Task.status=completed/failed/cancelled` | Direct | Treat terminal statuses as durable truth. |
| NODE_DISCOVERED | `TraceSpan.span_type/name`, `Subtask`, `Escalation` | Derived | Projection layer creates stable node ids. |
| NODE_STARTED | SSE `span_start` | Direct | Attach span to existing or newly discovered node. |
| NODE_COMPLETED | SSE `span_end status=ok`, trace refetch | Direct | Update node metadata from span output. |
| NODE_FAILED | SSE `span_end status=error`, `Subtask.status=failed` | Direct/Derived | Prefer span error, reconcile with subtask. |
| NODE_SKIPPED | `Subtask.status=skipped` | Direct | Render skipped node only if subtask exists. |
| LLM_STARTED / LLM_COMPLETED | `TraceSpan.span_type in sketch/tool_selection/reasoning/review/synthesize`, `LlmCall` rows | Derived | Map span purpose to reasoning family; add LLM call API for exact tokens. |
| TOOL_PROPOSED | `tool_selection` span output | Derived | Use safe structured proposal/rationale metadata only. |
| TOOL_VALIDATED | Tool execution path currently implicit | Missing | Add small backend event or span metadata after validation. |
| TOOL_EXECUTED / TOOL_FAILED | `tool_call` span, `ToolCall` rows | Direct/Derived | Add run-detail API for ledger rows and effect keys. |
| POLICY_DECISION | Escalation context, policy telemetry/audit where available | Derived/Missing | Expose per-call policy decision for allowed, approval, and denied calls. |
| APPROVAL_REQUIRED | `Escalation.status=pending`, `Task.status=awaiting_approval` | Direct | Render approval node/card from escalation context. |
| APPROVAL_RESOLVED | `Escalation.status`, `decided_at`, audit event | Direct | Refetch escalation and task after decide API. |
| APPROVAL_EXPIRED | No explicit expiry field today | Missing | Add expiry semantics only if backend enforces it. |
| MEMORY_RETRIEVED / MEMORY_WRITTEN | `TraceSpan.span_type=memory`, `MemoryEntry` | Derived | Stabilize UI-safe retrieved/selected/dropped shape. |
| KNOWLEDGE_RETRIEVED | `knowledge_search` tool span output | Derived | Stabilize chunk/source/score shape; do not imply unsupported rerank UI. |
| CONTEXT_BUILT / CONTEXT_DROPPED | Context engine telemetry/spans, not stable UI API | Missing | Add safe context-composition endpoint or enriched LLM spans. |
| VERIFICATION_STARTED / COMPLETED | Verification spans/tool outputs | Derived | Stabilize expected/observed/outcome schema. |
| RETRY / RECOVERY | `Subtask.attempt_count`, recovery modules, `ToolCall.success is NULL/False` | Derived/Missing | Add UI-safe recovery event records for ambiguous effects and stranded tasks. |
| ARTIFACT_CREATED | `/v1/tasks/{id}/artifacts` | Direct | Attach artifact events to timeline after refetch. |
| SECURITY_GUARDRAIL_RESULT | Guardrail tests/audit/metadata, no product endpoint | Missing | Add security events API with redacted metadata only. |
| STREAM_SNAPSHOT | SSE `snapshot` | Direct | Use snapshot to trigger durable refetch. |
| STREAM_ERROR | SSE `stream_error`, EventSource error | Direct | Show degraded banner and keep polling/refetching. |

## 28. Graph Data Model

Frontend graph shape:

```ts
type ExecutionActor =
  | "user"
  | "llm"
  | "runtime"
  | "tool"
  | "memory"
  | "knowledge"
  | "human";

type ExecutionNodeType =
  | "goal"
  | "plan"
  | "llm"
  | "router"
  | "tool"
  | "subagent"
  | "memory"
  | "knowledge"
  | "policy"
  | "approval"
  | "execution"
  | "verification"
  | "recovery"
  | "synthesis"
  | "final";

type ExecutionStatus =
  | "pending"
  | "running"
  | "waiting"
  | "approval_required"
  | "succeeded"
  | "failed"
  | "retrying"
  | "blocked"
  | "skipped"
  | "recovered"
  | "cancelled";

interface ExecutionNode {
  id: string;
  runId: string;
  type: ExecutionNodeType;
  actor: ExecutionActor;
  label: string;
  status: ExecutionStatus;
  startedAt?: string;
  completedAt?: string;
  parentId?: string;
  groupId?: string;
  traceSpanIds: string[];
  subtaskId?: string;
  toolName?: string;
  metadata: Record<string, unknown>;
}

interface ExecutionEdge {
  id: string;
  source: string;
  target: string;
  relation:
    | "sequence"
    | "parallel"
    | "route"
    | "uses_context"
    | "requires_approval"
    | "verifies"
    | "recovers"
    | "retries";
  status?: ExecutionStatus;
  metadata?: Record<string, unknown>;
}
```

Graph must support:

- Incremental node creation.
- Late-arriving metadata enrichment.
- Multiple events per node.
- Collapsible groups.
- Selected node persistence across refetch when ids are stable.

## 29. Frontend State Model

Do not create one enormous global store.

Recommended state split:

- Server state: SWR or equivalent for task, trace, escalations, runs, memory,
  knowledge, tools, metrics.
- Live stream state: task-scoped event buffer and connection state.
- Derived run model: memoized projection from durable rows + live events into
  graph nodes, timeline events, chat event summaries.
- View state: selected node, focused timeline event, panel sizes, collapsed
  panels, follow execution, filters.
- Approval state: pending decision, submitting state, optimistic transition
  only after API success.

Local state should be scoped to Workspace. Shared shell state should only hold
navigation, theme, and current run shortcut.

## 30. Reconnect And Recovery UX

Connection states:

- Connected.
- Connecting.
- Reconnecting.
- Disconnected.
- Stream degraded.
- Run completed while disconnected.

Rules:

- SSE is a live transport, not durable truth.
- On connect/reconnect, fetch durable task/trace/escalation state.
- The stream starts with a snapshot, but missed history comes from Postgres.
- If disconnected while a run is active, keep the last graph and show a banner.
- When reconnected, diff durable state and append "Recovered from snapshot" to
  the timeline.
- If a run completed while disconnected, show completed status and update graph
  from durable rows.
- Approval buttons should be disabled when connection/API state is unknown, or
  should perform a fresh API read before deciding.

This aligns with Phase 8: Postgres is source of truth; Redis/SSE is best-effort
live narration.

## 31. Responsive Strategy

Primary target: 1440 px desktop engineering workstation.

1440+:

- Layout A three-column studio.
- Timeline bottom.
- Inspector persistent.

1280:

- Slightly narrower chat.
- Inspector can collapse automatically if selected node is not active.
- Graph remains central.

Small laptop:

- Chat and graph split with inspector drawer.
- Timeline becomes collapsible bottom panel.
- Provide `Graph focus` and `Chat focus` modes while keeping current run status
  visible.

Tablet/read-only:

- Tabs: Chat, Graph, Timeline, Inspector.
- Approval card appears as a full-width accessible panel.
- Avoid trying to show three tiny columns.

Mobile:

- Not the primary Phase 9B target.
- Must not catastrophically break; show stacked navigation and one active panel.

## 32. Accessibility

Requirements:

- Node state cannot rely on color only. Use icons, labels, status text, and
  shape/accent.
- Graph nodes must be keyboard-focusable.
- Arrow keys or tab order should move between visible nodes and timeline items.
- Inspector updates must announce selected node changes.
- Approval actions need visible focus, button labels, confirmation, and disabled
  state explanations.
- Reduced motion should disable pulsing edges and use static running labels.
- Text should maintain contrast in dark and light themes.
- Raw status enum text should be converted to readable labels.

## 33. Design System Direction

Visual tone:

- Modern.
- Technical.
- Premium.
- Dense enough for engineers.
- Quiet and clear, not visually noisy.

Principles:

- Use hierarchy through spacing, typography, borders, surfaces, status
  indicators, and restrained motion.
- Avoid large decorative gradients, heavy glow, excessive glassmorphism, and
  nested cards.
- Use dark mode as first-class; keep light mode usable.
- Use icons for controls and status, with text where clarity matters.
- Cards are for repeated items and approvals, not every page section.
- Graph nodes should use distinct type treatments for LLM, runtime, tool,
  memory, knowledge, human approval, and final states.

Palette direction:

- Neutral dark surfaces.
- Steel/cyan for runtime.
- Green for success.
- Amber for running/waiting.
- Red for failure/deny.
- Violet or blue sparingly for LLM.
- Magenta/pink sparingly for human approval.
- Purple should not dominate the whole UI.

Motion:

- Running node pulse.
- Active edge flow.
- New event arrival.
- Approval transition.
- Inspector slide/fade.
- Panel resize.

Avoid constant decorative motion.

## 34. Dependency Recommendation

Current frontend dependencies already include:

- Next.js 15.
- React 19.
- Tailwind CSS 4.
- SWR.
- Radix UI primitives.
- lucide-react.
- `@xyflow/react`.

Recommendation:

- Use `@xyflow/react` for production graph rendering in Phase 9B.
- Do not hand-roll graph pan/zoom/edges.
- Do not install React Flow again; the package is already present as
  `@xyflow/react`.
- Use current SWR + SSE pattern, but add a normalized projection layer between
  backend records/events and graph/timeline/chat.
- Consider a small state helper only if Workspace local state becomes too
  tangled; do not add a global store first.

## 35. Backend Gaps

The backend can already support a strong first version through existing durable
rows and SSE, but these gaps limit fidelity:

| Gap | Why UI needs it | Existing source | Smallest backend change | MVP? |
| --- | --- | --- | --- | --- |
| Normalized run graph endpoint | Workspace needs stable nodes/edges without duplicating brittle mapping in components. | `/v1/tasks/{id}`, `/trace`, `/escalations` | Add frontend adapter first; later expose `/v1/tasks/{id}/execution-model`. | Yes, adapter version. |
| Rich semantic live events | SSE currently narrates spans, not policy/tool/memory semantics. | SSE `snapshot`, `span_start`, `span_end`, `task_status`, `stream_error` | Keep SSE; add metadata to spans/events only where derivation is brittle. | Yes, partial. |
| Per-call policy decision API | Policy nodes should show allowed, approval, and denied decisions, not only pending approvals. | Escalation context, policy module, audit/telemetry where available | Include decision snapshot on trace/tool call output or a task policy-events endpoint. | Yes for approval path, P1 for all calls. |
| ToolCall ledger detail | Execution nodes need effect key, ambiguous effect, retries, and result state. | `ToolCall` table, analytics aggregates | Add task-scoped tool-call endpoint joined through subtasks. | Yes for side-effect tools. |
| LLM call node metrics | LLM inspector needs exact model, tokens, cached tokens, cost, latency. | `LlmCall` table, analytics aggregate | Add task-scoped LLM call endpoint or enrich matching spans. | P1. |
| Context composition endpoint | Context Inspector needs included/dropped blocks and token budget. | Context engine/telemetry paths | Emit safe context build metadata tied to LLM span id. | P1, use graceful unknown in MVP. |
| Memory retrieval safe shape | Memory node needs selected/dropped/provenance. | `TraceSpan.span_type=memory`, `/v1/memory` | Stabilize memory span output schema and redact unsafe bodies. | Yes for memory screen, P1 for per-call dropped items. |
| Knowledge retrieval safe shape | Knowledge node/playground needs chunks, scores, source, document. | RAG API/tool output | Stabilize `knowledge_search` output and document/source identifiers. | P1. |
| Verification schema | Verification inspector needs expected, observed, outcome. | Verification spans/tool outputs | Normalize verification span output. | Yes for Gmail draft demo path. |
| Recovery/ambiguous effect events | Recovery nodes should appear only when recovery happens. | Recovery module, `ToolCall.success NULL`, Phase 8 hardening | Add safe recovery event/span output with action taken. | P1. |
| MCP trust API | MCP screen needs server status, fingerprints, review state, changed schema warnings. | Registry/discovery/config, Phase 7D tests | Add read-only MCP server/tool trust endpoint. | P1. |
| Security events API | Security screen must show redacted guardrail metadata. | Guardrails, audit, tests | Add product endpoint for safe security event metadata. | P2. |
| Settings mutation | Settings screen should not imply unsupported editing. | Config/env only | Keep read-only until explicit mutation APIs exist. | No. |

Phase 9B should avoid blocking on every backend gap. Start with derived events
from existing records, then add backend enrichments where derivation is brittle.

## 36. Implementation Sequence

Phase 9A:

1. Architecture document.
2. Functional static prototype.
3. Human review.

Phase 9B recommended:

Recommended frontend ownership:

```text
app/(shell)/workspace/
app/(shell)/runs/
app/(shell)/knowledge/
app/(shell)/memory/
app/(shell)/tools/
app/(shell)/integrations/
app/(shell)/approvals/
app/(shell)/evals/
app/(shell)/observability/
app/(shell)/govern/

components/execution/
  WorkspaceShell
  ExecutionCanvas
  ExecutionNode
  RunTimeline
  NodeInspector
  ContextInspector
  ApprovalPanel

components/chat/
  ConversationPanel
  RuntimeCard
  Composer

lib/run-model/
  fetchRunState
  normalizeEvents
  projectExecutionGraph
  projectTimeline
  projectChatSummaries
```

Rules:

- Event normalization and graph projection live in `lib/run-model`, not inside
  node rendering components.
- SWR owns server state. SSE is incremental narration that triggers refetch and
  enriches the current view.
- Local React state owns selected node, focused timeline event, panel sizes,
  collapsed panels, and focus mode.
- Approval state should be optimistic only after the decide API succeeds.

P0 production scope:

1. Add frontend normalized run projection layer from current APIs.
2. Merge current task feed and graph into one Workspace route.
3. Build Workspace Layout A with graph/chat/inspector/timeline bound to the
   same task id and derived run model.
4. Support approval inspect/approve/reject using existing escalation APIs and
   fingerprint context.
5. Add reconnect UX: show degraded state, refetch durable task/trace/escalation
   state, rebuild graph/timeline, resume SSE.
6. Redesign Runs and Run Detail around Execution History, not deterministic
   replay.
7. Add Playwright coverage for Workspace start/run/approval/reconnect paths.

P1 production scope:

1. Expand Knowledge, Memory, Tools, MCP, Integrations into the final IA using
   current APIs and read-only states where mutation is unsupported.
2. Add contextual inspector tabs for LLM, tool, policy, approval, memory,
   knowledge, verification, and recovery nodes.
3. Add task-scoped backend enrichments for policy decisions, tool ledger, LLM
   calls, context composition, memory/RAG retrieval, verification, and MCP
   trust where frontend derivation is brittle.
4. Add collapsed repeated operations and bounded rendering for long runs.

P2 production scope:

1. Add Evals, Observability, Security, and Settings polish.
2. Add security-events API and richer guardrail drill-down.
3. Add optional cost/latency trend views and eval comparison history.
4. Improve tablet/mobile read-only behavior after desktop workflow is stable.

## 37. Deliberately Excluded UI

Excluded from Phase 9A and initial Phase 9B:

- Production frontend rewrite in Phase 9A.
- Fake policy editor.
- No-code agent builder.
- Gmail send UI.
- Raw chain-of-thought viewer.
- Raw secret/payload viewer.
- Deterministic replay claims beyond durable run detail.
- Full RBAC/tenant administration.
- Marketplace for MCP servers.
- Drag-and-drop workflow builder.
- Generic Datadog clone.
- DeepEval as runtime safety surface.
- Mobile-first redesign.
- Editing unsupported backend settings.

## 38. Prototype Scope

The prototype under `docs/ui-prototype` demonstrates:

- All major screens in the proposed navigation.
- Recommended Workspace Layout A.
- Functional chat.
- Live graph simulation.
- Node selection and inspector updates.
- Timeline updates.
- Approval pause.
- Approval fingerprint invalidation when arguments change.
- Approve path resumes execution.
- Reject path stops safely.
- Reconnect recovery concept: durable snapshot rebuilds graph and timeline.
- Studio, graph focus, chat focus, and side-panel collapse controls.
- Responsive behavior for smaller widths.

The prototype uses mocked data and does not call production backend APIs.

Closure review screenshots captured from Playwright:

- `docs/ui-prototype/screenshots/workspace-closure-complete.png`
- `docs/ui-prototype/screenshots/workspace-closure-rejected.png`
- `docs/ui-prototype/screenshots/workspace-closure-responsive-1024.png`
