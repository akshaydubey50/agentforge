# Phase 2 implementation note — deterministic policy boundary

Written before any code, per the phase brief. Records what the current
approval path actually does, so the change can be a re-route rather than a
rebuild.

## Existing mechanisms that can be reused

| Need | What already does it | Change needed |
|---|---|---|
| Validated arguments | `tools/base.py` `validated_kwargs()` — the one gate both execution seams share (Phase 1) | none; policy runs *after* it and receives its output |
| Approval request | `Escalation(kind="tool_approval")` + `_create_escalation()` | none |
| Approval snapshot | `Escalation.context = {"tool_name", "kwargs"}` — JSONB, already the exact validated kwargs | add the policy decision to the same dict; no migration |
| Approval resume | `escalations.apply_escalation_decision()` → `nodes.run_gated_tool_call()` → `_run_tool_call()` | add a pre-execution guard; the execution itself is unchanged |
| Refusal-before-execution | `nodes._rejected_call()` — writes a `tool_call` span with `status=error`, marks the step unproductive, returns an observation for the loop | reuse verbatim for `DENY` |
| Audit | `audit.record()` + hash chain, `audit.Action` | one new action constant |
| Tracing | `graph/tracing.span()` | none |
| Config | `config.Settings` (pydantic `BaseSettings`) | one new field for approval expiry |
| Canonical (tool, args) identity | `deadcalls._signature()` | **not** reusable as-is: it strips injected kwargs, and `user_id` (whose Gmail a call acts as) is exactly what an approval must be bound to |

## Existing mechanisms that should be replaced

- `Tool.requires_approval` (class bool) and `Tool.needs_approval(kwargs)`
  (per-call override). Two tools of fifteen set them: `code_execution`
  (`requires_approval = True`) and `file_io` (override — `write` yes,
  `read`/`list` no). Both are deleted; policy decides instead. The `file_io`
  override is the proof that per-call, argument-dependent gating was already
  needed — it is the argument-dependent rule, not a manufactured example.

## Two defects the inspection turned up

1. **The approval gate covers one of the two execution seams.**
   `nodes._execute_subtask` checks `needs_approval` before running a tool.
   `tools/delegate_subagent.py`'s inner loop — the second place an
   LLM-proposed call becomes real kwargs, closed for *validation* in Phase 1
   — has no approval check at all. A sub-agent that proposes
   `code_execution` runs it, unapproved. Phase 1 fixed validation across both
   seams and left gating behind in one. Policy must be evaluated in both.

   A sub-agent cannot pause for a human (it runs synchronously inside a tool
   call), so its enforcement is *refuse and continue*, not escalate. That is
   strictly safer than today and honest about the limit.

2. **The resume path re-executes a stored dict without re-checking it.**
   `run_gated_tool_call` deliberately does not re-validate — correct, because
   what a human approved is that exact call. But it also never checks that
   the dict it is about to run is still the one that was approved, or that
   the approval is still fresh. Nothing in the API mutates
   `Escalation.context` today, so this is drift-resistance rather than a live
   hole; it is what makes "approve `send_email(to=A)`, resume with
   `send_email(to=B)`" impossible by construction instead of by accident.

## Smallest viable policy design

One new pure module, `src/agentsys/policy.py` (~120 lines), with no DB, no
network, no LLM and no settings import — so it is a tier-1 test target:

```
ActionType   READ | LOCAL_WRITE | EXTERNAL_WRITE | DESTRUCTIVE
Risk         LOW | MEDIUM | HIGH | CRITICAL        (str enum, no ordering —
                                                    every rule is equality)
PolicyDecision(decision, reason, action_type, risk)
decide(tool, args, *, user_id=None, environment=None) -> PolicyDecision
args_fingerprint(tool_name, kwargs) -> str
```

`Tool` gains `action_type` and `risk` as class attributes with **fail-closed
defaults** (`EXTERNAL_WRITE` / `HIGH` → `REQUIRE_APPROVAL`), so a tool that
forgets to classify itself is gated rather than silently allowed. Every
first-party tool declares explicitly; `MCPTool`, whose effects belong to a
third party, keeps the fail-closed default unless the operator declares the
server's classification in the `mcp_servers` config entry that already
exists.

`decide()` cannot raise: the rule body is wrapped and any exception becomes
`DENY`. §14's fail-closed requirement lives inside the function rather than
at each call site, because there are two call sites and one of them is in a
sub-agent loop.

Enums live in `policy.py` and `tools/base.py` imports them; `policy.py`
imports `Tool` only under `TYPE_CHECKING`, which is what keeps the cycle
from closing.

### Enforcement per seam

| Decision | `_execute_subtask` (main loop) | `delegate_subagent` (sub-agent loop) |
|---|---|---|
| `ALLOW` | existing execution path, untouched | existing execution path, untouched |
| `REQUIRE_APPROVAL` | existing `tool_approval` escalation, untouched | refused with an explanation; the sub-agent picks another approach |
| `DENY` | existing `_rejected_call()` | refused with an explanation |

### Deliberately not built

- No policy tables, no rules DSL, no YAML. Rules are a function in one file;
  move them to config the day a non-developer edits them.
- No new escalation kind, no new approval subsystem, no RBAC.
- No `Risk` ordering helper — every rule written is an equality or a
  membership test. Add ordering when Phase 3's ledger needs `>= WRITE`.
- No re-implementation of `file_io`'s sandbox path check as a policy rule.
  The tool resolves the path against the sandbox and refuses escapes; a
  second, weaker copy in policy is the drift `DbQueryArgs` already warns
  about. A write is gated on being a write; where it points stays the
  tool's call.
