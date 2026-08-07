# Code Review Standards

## Approval Requirements

Every pull request into `main` requires two approvals, at least one from a member of the owning
team's CODEOWNERS list. Exceptions: hotfixes tagged `urgent` may merge with one approval plus a
follow-up review within 24 hours; a new hire's first PR requires only one approval from their
onboarding buddy (see the Onboarding Guide).

## What Reviewers Check

Reviewers are expected to check, in priority order: correctness (does it do what it claims),
test coverage (is the new behavior tested, not just the happy path), API contract adherence (see
API Design Guidelines), and readability. Style nits should be left as non-blocking comments
prefixed `nit:` — blocking a PR on formatting alone is discouraged since CI already enforces
formatting via `ruff` and `black`.

## Test Coverage

New code must maintain or improve the file's existing test coverage; there is no fixed global
percentage target, because a single hard threshold encourages padding tests rather than testing
meaningfully. CI will fail a PR if the diff's coverage drops by more than 5 percentage points
relative to the file's baseline.

## Size and Scope

PRs should be scoped to a single logical change. The soft guideline is under 400 changed lines;
PRs larger than that should include a comment explaining why it wasn't split, since large PRs
measurably take longer to review and have a higher post-merge defect rate internally.

## Merge Process

PRs are squash-merged by default so `main` has one commit per logical change. The squash commit
message must reference the ticket ID. Rebase-merge is permitted only for release-branch
backports, and must be explicitly requested in the PR description.
