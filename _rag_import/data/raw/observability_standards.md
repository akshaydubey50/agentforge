# Logging and Observability Standards

## Structured Logging

All services log structured JSON, never plain text, with a mandatory field set: `timestamp`,
`service`, `request_id`, `level`, `message`. The `request_id` must be propagated across service
boundaries via the `X-Request-ID` header so a single request can be traced end-to-end across
multiple services in the log aggregator.

## Log Levels

`ERROR` is reserved for conditions requiring human attention; a caught exception that was handled
gracefully is `WARNING`, not `ERROR`. Services with an `ERROR` log rate above the alerting
threshold (default 1% of requests) page on-call automatically — noisy `ERROR` logging causes
alert fatigue and is treated as a bug in the logging itself, not just cosmetic.

## Log Retention

Logs are retained for 30 days in hot storage (queryable) and 1 year in cold storage (archival,
not queryable without a restore request). Logs containing PII are redacted at write time by the
logging middleware; raw PII must never reach the log aggregator, retention period aside.

## Tracing

Every service is instrumented with OpenTelemetry; spans must include the operation name, duration,
and status at minimum. Sampling is 100% for error traces and 1% for successful traces by default,
configurable per-service if a team needs deeper visibility during an investigation.

## Dashboards

Every service on-call rotation must have a linked dashboard showing request rate, error rate, and
P50/P95/P99 latency, reviewed for accuracy during onboarding (see the Onboarding Guide). Missing
or broken dashboards are a blocking finding in the quarterly service review.
