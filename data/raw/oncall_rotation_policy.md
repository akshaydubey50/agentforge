# On-Call Rotation Policy

## Rotation Structure

Each service team maintains a primary and secondary on-call rotation, one week at a time,
rotating every Monday at 10:00 local time to allow for a calm handoff during business hours
rather than overnight. The secondary is paged automatically if the primary does not acknowledge
a page within 5 minutes.

Engineers must complete the on-call shadow period described in the Onboarding Guide before
joining the primary rotation. Shadowing does not count toward the rotation schedule itself.

## Compensation

On-call weeks are compensated with a flat stipend plus time-off-in-lieu for any page acknowledged
outside 9am-9pm local time, tracked automatically from PagerDuty acknowledgment timestamps.
Engineers do not need to file anything manually to receive on-call compensation.

## Escalation Policy

If a page is not acknowledged within 5 minutes it escalates to the secondary; if the secondary
does not acknowledge within another 5 minutes it escalates to the team's engineering manager.
Repeated missed pages (more than 2 in a rolling 90-day window) trigger a conversation with the
manager about rotation fit, not disciplinary action by default.

## Swaps

Rotation swaps are self-service via the PagerDuty schedule override tool and do not require
manager approval, but must be posted in the team's on-call Slack channel at least 24 hours in
advance except for genuine emergencies.
