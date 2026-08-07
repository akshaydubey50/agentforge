# Feature Flag Usage Guide

## When to Use a Flag

Any change that is risky, gradual, or reversible-by-design should ship behind a flag: new user-
facing features, risky refactors, and anything that touches the payments or auth paths. Trivial
bug fixes and internal tooling changes do not need flags — over-flagging adds review overhead
without a corresponding safety benefit.

## Naming and Ownership

Flags are named `team_name.feature_description`, e.g. `checkout.new_refund_flow`. Every flag has
a required owner and an expiration date set at creation time; flags without an expiration date
are rejected at creation by the flag service.

## Rollout Stages

Standard rollout is staged: internal employees only, then 1%, 5%, 25%, 50%, 100% of traffic, with
a minimum 2-hour bake time at each stage below 25% and 24 hours at 50% and above before advancing.
Advancing early requires explicit sign-off from the flag owner's manager.

## Cleanup

Flags at 100% for more than 30 days must be cleaned up — the flag removed and the old code path
deleted — or they are auto-reported in the weekly "stale flags" digest to the owning team. Flags
left stale for more than 90 days are escalated to the team's engineering manager.

## Kill Switches

Flags used as kill switches (to disable a feature during an incident, see the Incident Response
Runbook) are exempt from the expiration and cleanup rules above, but must be explicitly tagged
`kill_switch` at creation so they're excluded from the stale-flags report.
