# Engineering Onboarding Guide

## Week 1: Access and Setup

Every new engineer is provisioned with a laptop, VPN credentials, and access to the internal
GitHub organization on their first day. IT provisioning tickets are handled by the Platform
team and typically resolve within 4 business hours. If access has not been granted within
one business day, escalate in the #it-help Slack channel.

Local development environments are set up using the `devsetup` CLI tool, which installs the
correct Python version (3.11), Docker, and the internal `corp-cli` tool used for authenticating
against staging and production services. Run `devsetup init` from a fresh clone of any
repository to bootstrap the environment.

## Week 1: Required Reading

New engineers must read, in order: the API Design Guidelines, the Code Review Standards, and
the Security Guidelines for Handling Secrets. These three documents cover the non-negotiable
conventions every service in the monorepo follows. Skipping this step is the single most common
cause of PR review delays for new hires.

## Week 2: First Contribution

Every new engineer's first PR should be a "good first issue" tagged ticket, selected with their
onboarding buddy. The onboarding buddy is assigned during week 1 by the engineering manager and
is expected to pair with the new hire at least three times during the first two weeks.

First PRs are exempt from the usual two-approver rule described in the Code Review Standards —
one approval from the onboarding buddy is sufficient, but the PR must still pass CI.

## Week 3: On-Call Shadow

By week 3, new engineers shadow one on-call rotation (see the On-Call Rotation Policy) without
carrying the pager themselves. This is mandatory before an engineer can be added to the primary
on-call rotation for their team.

## Common Pitfalls

The most common onboarding blocker is VPN split-tunneling misconfiguration, which prevents
access to internal-only services like the staging database. If `corp-cli whoami` hangs for more
than 10 seconds, this is almost always the cause — re-run `devsetup vpn-fix`.
