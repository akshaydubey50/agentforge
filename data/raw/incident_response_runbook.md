# Incident Response Runbook

## Severity Levels

Sev-1: customer-facing outage or data loss affecting more than 5% of traffic. Sev-2: significant
degradation, a leaked credential, or an outage affecting a single non-critical service. Sev-3:
minor bug affecting a small number of users with a workaround available. Only Sev-1 and Sev-2
incidents require a live incident channel and an assigned Incident Commander.

## Declaring an Incident

Anyone can declare an incident by running `/incident declare` in Slack, which creates a dedicated
channel, pages the current on-call engineer for the affected service, and starts the incident
timeline log automatically. Do not wait for on-call to declare — declaring early and standing
down later is always preferred over declaring late.

## Roles

The Incident Commander (IC) coordinates but does not personally debug; their job is
communication, sequencing, and deciding when to escalate further. The first on-call engineer to
respond is the default IC until they explicitly hand it off. A separate Communications Lead
posts customer-facing status updates for Sev-1 incidents every 30 minutes until resolution.

## Mitigation Before Root Cause

The immediate priority is mitigation, not diagnosis: rollback the last deploy, fail over to a
healthy region, or disable the offending feature flag first. Root-causing happens after the
customer impact has stopped, not before. Chasing root cause while customers are still affected is
explicitly called out as an anti-pattern in this process.

## Postmortems

Every Sev-1 and Sev-2 incident requires a blameless postmortem within 5 business days, using the
Postmortem Template. Postmortems are reviewed in the weekly Reliability sync and action items are
tracked as tickets with owners and due dates, not left as free-text suggestions.
