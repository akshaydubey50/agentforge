# Database Migration Guide

## Backward Compatibility Rule

Every schema migration must be backward compatible with the previous version of the application
code, because deploys are rolling and both old and new code run simultaneously for several
minutes during rollout. Adding a NOT NULL column without a default, renaming a column, or
dropping a column that old code still reads are all disallowed in a single migration.

## The Expand-Contract Pattern

Breaking-looking changes are done in three separate deploys: expand (add the new column/table
alongside the old one), migrate (backfill data and switch application code to the new shape),
contract (drop the old column/table once nothing reads it). Each phase ships and bakes
independently; do not combine expand and contract in one migration.

## Backfills

Backfills on tables over 1 million rows must run in batches (default batch size 5,000 rows) with
a sleep between batches to avoid replication lag and lock contention. Backfill scripts are
reviewed by the Database team, not just the owning team, before running against production.

## Rollback

Every migration must have a tested rollback path before merging. If a migration cannot be safely
rolled back (e.g. a destructive drop), it must ship only after the corresponding contract phase
has baked in production for at least one week with no issues.

## Review Process

Migrations touching tables larger than 10 million rows or any table in the `payments` or `auth`
schemas require sign-off from the Database team in addition to the normal two-approver code
review, regardless of migration size.
