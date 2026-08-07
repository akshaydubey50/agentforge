# API Design Guidelines

## Versioning

All public APIs are versioned in the URL path, e.g. `/v1/orders`. Breaking changes require a new
version prefix; additive changes (new optional fields, new endpoints) do not. A breaking change
is defined as anything that would require an existing client to change its code to avoid an
error — this includes removing a field, renaming a field, or changing a field's type.

We support at most two major versions of any public API at once. When a third version ships, the
oldest is deprecated with a minimum 90-day sunset window, announced in the #api-changes channel
and via the deprecation header `Sunset:` on every response.

## Request and Response Contracts

Every endpoint must define its request and response schema using Pydantic models. Raw dict
payloads are not permitted in any service past the edge layer. Response envelopes follow a
consistent shape: `{"data": ..., "meta": {...}}` for success, `{"error": {"code": ..., "message":
..., "request_id": ...}}` for failures.

Pagination uses cursor-based pagination for any list endpoint that can exceed 100 items, never
offset-based — offset pagination degrades badly under concurrent writes and is disallowed in
code review.

## Idempotency

Any endpoint that creates a resource (POST) and is not naturally idempotent must accept an
`Idempotency-Key` header. Keys are stored for 24 hours; a repeated request with the same key
within that window returns the original response rather than creating a duplicate resource.
This is mandatory for payment and refund endpoints without exception.

## Error Handling

Error codes are machine-readable, snake_case strings (e.g. `insufficient_inventory`), never raw
HTTP status text. The HTTP status code communicates the category (4xx client error, 5xx server
error); the error code communicates the specific reason. Client-facing error messages must never
include stack traces, internal hostnames, or database error text.

## Rate Limiting

Public APIs are rate-limited per API key using a token bucket with a default of 100 requests per
minute, burst of 20. Rate limit state is returned on every response via `X-RateLimit-Remaining`
and `X-RateLimit-Reset` headers. Teams needing a higher limit for a specific integration file a
request with the Platform team; ad hoc limit increases are not self-service.
