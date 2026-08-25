"""The deterministic decision boundary between what the model proposes and
what actually runs.

    LLM proposes a tool call
              |
      typed validation          (tools/base.py's validated_kwargs -- Phase 1)
              |
      policy.decide(...)        <-- this module
              |
    ALLOW / REQUIRE_APPROVAL / DENY
              |
    existing execution / existing escalation machinery

WHAT THIS REPLACES

Approval used to be a boolean on a Python class: `Tool.requires_approval`,
with one per-call override (`file_io.needs_approval`, which gated `write` but
not `read`). Two tools of fifteen set it. That could not express risk tiers,
argument-dependent gating, or anything an operator might want to change
without editing a tool -- see docs/ARCHITECTURE_AUDIT.md 5.2.

WHY THIS MODULE IS PURE

No database, no network, no LLM call, no settings import. A policy decision
must be reproducible from its inputs alone: the same tool, the same validated
arguments and the same user must always produce the same decision, or the
audit record of a decision is not evidence of anything. It also makes the
whole rule set a tier-1 test target (tests/test_policy.py) -- free and
deterministic, so a broken rule never costs an API call to discover.

An LLM call in here would defeat the entire point. The model proposes; this
disposes.

THE RULES ARE A FUNCTION, NOT A TABLE

Deliberately not YAML, not a database, not a DSL. There are eleven rules and
one reader (this repo's developers). Move them to configuration the day a
non-developer needs to edit them, and not before -- a rules engine whose only
operator is the person who can already edit the code is a second, weaker
programming language.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    # Type-checking only, so the import cycle never closes: tools/base.py
    # imports ActionType/Risk from here to declare them on Tool.
    from agentsys.tools.base import Tool


class ActionType(str, Enum):
    """What a call does to the world, which is a different question from how
    badly it could go (see Risk).

    Four values, and no more until a real tool needs a fifth. There is
    deliberately no PURE/COMPUTE category for tools that only read their own
    arguments and return text (`generate_tweet`): "has no external effect" is
    what READ already means here, and a category with one member is a category
    that earns nothing.
    """

    READ = "read"
    LOCAL_WRITE = "local_write"
    """Writes confined to this task's own sandbox -- the workspace directory,
    an ephemeral container. Recoverable and invisible outside the task."""

    EXTERNAL_WRITE = "external_write"
    """Changes something outside this system: sends a message, posts content,
    creates a record in someone else's service. Not undoable by us."""

    DESTRUCTIVE = "destructive"
    """Removes or overwrites something that existed before this task."""


class Risk(str, Enum):
    """How bad the worst plausible outcome of one call is.

    A plain str enum with no ordering, because every rule below is an equality
    or a membership test. Add ordering (and the tests that pin it) the day a
    rule genuinely needs "at least MEDIUM" -- Phase 3's effect ledger is the
    likely first caller.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyDecisionType(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyDecision(BaseModel):
    decision: PolicyDecisionType
    reason: str
    """Written to be read by a human deciding whether to approve, and by
    whoever reads the audit chain afterwards. It names the rule that fired,
    not just the outcome."""

    action_type: ActionType
    risk: Risk
    """The EFFECTIVE classification, which is not always the tool's declared
    one: an argument-dependent rule can raise a call's action_type or risk
    above its tool's baseline (file_io is READ until `action="write"` makes
    that specific call a LOCAL_WRITE). Carried on the decision rather than
    re-derived from the tool at the audit seam, because re-deriving would
    record the baseline and lose the reason the call was actually gated."""


# --- argument-dependent classification -------------------------------------

def _effective(tool: "Tool", args: dict[str, Any]) -> tuple[ActionType, Risk]:
    """The classification of THIS call, starting from the tool's declared
    baseline and raising it where the validated arguments say so.

    This is the half of the engine that makes `same tool + different args ->
    different decision` possible. Only one registered tool currently needs it,
    and it needed it before policy existed: `file_io` shipped a
    `needs_approval` override precisely because reading your own workspace and
    writing to it are not the same action.

    Note what is NOT here: file_io's sandbox-escape check. The tool resolves
    every path against the task directory and refuses escapes (see
    _resolve_within_sandbox). A second, weaker copy of that decision in policy
    is exactly the drift DbQueryArgs's docstring warns about -- so a write is
    gated on being a write, and where it points stays the tool's call.
    """
    action_type, risk = tool.action_type, tool.risk

    if tool.name == "file_io":
        # read/list touch nothing; only write is an effect. Without this the
        # specialist would need human approval to look at its own workspace.
        if args.get("action") == "write":
            return ActionType.LOCAL_WRITE, Risk.MEDIUM
        return ActionType.READ, Risk.LOW

    return action_type, risk


# --- the rules -------------------------------------------------------------

def _apply_rules(action_type: ActionType, risk: Risk, tool_name: str) -> PolicyDecision:
    """The whole ruleset, in order. First match wins, and the last rule is a
    fail-closed catch-all rather than an implicit allow."""

    def decided(decision: PolicyDecisionType, reason: str) -> PolicyDecision:
        return PolicyDecision(decision=decision, reason=reason, action_type=action_type, risk=risk)

    # CRITICAL first, so no later rule can allow one. There is deliberately no
    # configured escape hatch: an allow path for critical actions that no
    # current tool needs would be a hypothetical hole with a real
    # implementation. Build it alongside the first tool that justifies it.
    if risk is Risk.CRITICAL:
        return decided(
            PolicyDecisionType.DENY,
            f"'{tool_name}' is classified CRITICAL risk; no rule permits a critical action",
        )

    if action_type is ActionType.DESTRUCTIVE:
        # DENY, not REQUIRE_APPROVAL. There is no destructive tool yet, which
        # means there is also no undo, no dry-run and no effect ledger to make
        # one safe to resume after a crash (Phase 3). Asking a human to
        # approve an action the system cannot execute safely just moves the
        # blame -- so the honest answer is that it cannot be run at all yet.
        return decided(
            PolicyDecisionType.DENY,
            f"'{tool_name}' performs a destructive action, and this system has no safe "
            "execution semantics for one yet (no dry-run, no undo, no durable effect record)",
        )

    if action_type is ActionType.EXTERNAL_WRITE:
        return decided(
            PolicyDecisionType.REQUIRE_APPROVAL,
            f"'{tool_name}' changes something outside this system and cannot be undone by it",
        )

    if risk is Risk.HIGH:
        return decided(
            PolicyDecisionType.REQUIRE_APPROVAL,
            f"'{tool_name}' is classified HIGH risk",
        )

    if action_type is ActionType.LOCAL_WRITE:
        return decided(
            PolicyDecisionType.REQUIRE_APPROVAL,
            f"'{tool_name}' writes to this task's workspace",
        )

    if action_type is ActionType.READ and risk in (Risk.LOW, Risk.MEDIUM):
        # MEDIUM reads are allowed because their constraints are enforced
        # where they belong and policy must not weaken them: db_query stays
        # behind its Phase-0 three-layer guard (statement shape, an
        # EXPLAIN-resolved relation allowlist, a read-only role), and the
        # Google tools stay behind the injected user_id that decides whose
        # account a call acts as. Gating them instead would ask a human to
        # re-approve every ordinary lookup, which trains humans to approve.
        return decided(
            PolicyDecisionType.ALLOW,
            f"'{tool_name}' is a {risk.value}-risk read",
        )

    # Fail closed. Reaching here means a combination the rules above do not
    # name, which is a gap in the ruleset -- and a gap must not be an allow.
    return decided(
        PolicyDecisionType.REQUIRE_APPROVAL,
        f"no rule covers {action_type.value}/{risk.value} for '{tool_name}'; "
        "gated pending an explicit rule",
    )


def decide(
    tool: "Tool",
    args: dict[str, Any],
    *,
    user_id: str | None = None,
) -> PolicyDecision:
    """The one entry point. `args` must be VALIDATED kwargs -- the output of
    tools/base.py's validated_kwargs, never raw model JSON. Both call sites
    (graph/nodes.py's _execute_subtask and the delegate_subagent loop) run
    validation first and would raise before reaching here if it failed.

    user_id is accepted and currently unused by any rule: every registered
    tool's risk is a property of the action, and per-user policy has no
    requirement behind it yet (ARCHITECTURE_AUDIT 5.2 lists it as a gap, not a
    Phase-2 deliverable). It is in the signature because both call sites
    already know it and a rule that needs it should not also need a signature
    change at both seams.

    There is deliberately no `environment` parameter. This application has no
    environment concept -- no such setting exists in config.py -- and adding
    one so that policy could accept it would be inventing the requirement to
    satisfy the abstraction.

    NEVER RAISES. A policy engine that can throw is a policy engine that can
    be made to fall open by breaking it -- the same reasoning as db_query's
    "FAIL CLOSED" note. Any exception in the rule body becomes DENY, and the
    reason says so, so the failure is visible rather than silently permissive.
    """
    try:
        action_type, risk = _effective(tool, args)
        return _apply_rules(action_type, risk, tool.name)
    except Exception as exc:  # noqa: BLE001 -- deliberate catch-all, see above
        return PolicyDecision(
            decision=PolicyDecisionType.DENY,
            reason=f"policy evaluation failed ({type(exc).__name__}: {exc}); denied because "
            "a policy that cannot decide must not permit",
            # Constants, NOT read back off the tool. The thing that raised may
            # BE the tool's classification -- and getattr(tool, "risk",
            # default) only swallows AttributeError, so a property raising
            # anything else would throw straight out of the handler that
            # exists to stop exactly that. The fail-closed path must not touch
            # the object it is failing closed about.
            action_type=ActionType.EXTERNAL_WRITE,
            risk=Risk.CRITICAL,
        )


# --- approval binding ------------------------------------------------------

def args_fingerprint(tool_name: str, kwargs: dict[str, Any]) -> str:
    """Canonical identity of one exact call, for binding an approval to it.

    Deliberately NOT deadcalls._signature(), which strips the injected kwargs.
    That is right for its purpose (two calls differing only in subtask_id are
    the same dead end) and wrong for this one: `user_id` decides whose Gmail
    or Drive a call acts as, and `task_id` decides which sandbox file_io
    writes to. An approval that does not cover those covers nothing.

    sort_keys so key order cannot change the fingerprint; default=str so an
    argument that isn't JSON-serialisable degrades to a stable string instead
    of raising inside the approval path.
    """
    payload = json.dumps({"tool": tool_name, "args": kwargs}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot(tool_name: str, kwargs: dict[str, Any], decision: PolicyDecision) -> dict[str, Any]:
    """Exactly what was approved, for Escalation.context.

    Stored in the existing JSONB column rather than a new table: the column
    already carried tool_name and kwargs, and the resume path already replays
    them verbatim (see nodes.run_gated_tool_call). This adds the decision that
    produced the request, plus a fingerprint the resume path re-checks -- so
    "approve send_email(to=A), resume with send_email(to=B)" is refused rather
    than merely unlikely.

    The fingerprint lives in the same dict it protects, so it is drift
    resistance, not tamper-proofing: it catches a future code path that
    changes the arguments without re-evaluating policy (an approver UI that
    lets a human tweak a field, a resume that re-derives kwargs from the
    model), which is the failure this is actually guarding against. Anything
    with write access to the row could update both halves.
    """
    return {
        "tool_name": tool_name,
        "kwargs": kwargs,
        "policy": decision.model_dump(mode="json"),
        "args_fingerprint": args_fingerprint(tool_name, kwargs),
    }
