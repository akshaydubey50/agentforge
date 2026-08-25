# Phase 5 implementation note - Gmail draft external action

Written before code, per the phase brief. This phase proves the full
AgentForge action lifecycle against one real external mutation: creating a
Gmail draft. It deliberately does not send email.

## How Gmail authentication currently works

Google authentication lives in `src/agentsys/integrations/google_oauth.py`.
The app uses one OAuth Authorization Code flow for both sign-in and Google
tool access. `build_auth_url()` sends the user's browser to Google with a
CSRF `state` stored in Redis. `login_or_connect()` exchanges the returned code
for tokens, fetches Google identity, upserts `User`, and upserts one
`GoogleConnection` for that user.

The current default scopes are:

- `openid`
- `userinfo.email`
- `userinfo.profile`
- `drive.readonly`
- `gmail.readonly`

Phase 5 adds the smallest Gmail write scope for draft creation:
`https://www.googleapis.com/auth/gmail.compose`. Existing users whose
`GoogleConnection.scopes` were recorded before Phase 5 will not magically gain
that scope when their token refreshes; refresh only preserves an existing
grant. Draft creation therefore checks for `gmail.compose` before calling
Gmail and returns a reconnect/re-consent message if it is missing.

## How account/user identity reaches Gmail tools

The LLM never chooses a Google account. `graph/nodes.py` owns runtime
injection through `_injected_kwargs()`: Gmail/Drive/Photos tool calls receive
`user_id` from `Task.owner_id`. The tool args models do not include `user_id`,
so a model proposal that names it is rejected by Phase 1 validation rather
than silently honored.

The Gmail tool then calls `get_valid_access_token(user_id)`. That function
selects `GoogleConnection` by `GoogleConnection.user_id`, so each task can
only act through the connected Google account of the authenticated task owner.

## How token refresh works

`get_valid_access_token(user_id)` checks the user's `GoogleConnection`. If no
connection exists, or Google OAuth is not configured, it raises
`GoogleOAuthError`. If the access token is near expiry, `_refresh()` exchanges
the stored refresh token for a new access token, updates the same connection
row, and returns the fresh token.

Tools convert `GoogleOAuthError` into a normal failed `ToolResult`, so auth
problems remain observations in the existing tool/review/replan path rather
than uncaught exceptions.

## Existing Gmail read capabilities

`gmail_search` calls `users.messages.list` and then `users.messages.get` with
metadata format. It returns message ids, sender, subject, date and snippets.

`gmail_read` calls `users.messages.get` with full format and decodes the MIME
tree to return headers and body text. Read bodies are wrapped as untrusted
content. Both tools are `ActionType.READ`, `Risk.MEDIUM`, and
`ExecutionSafety.IDEMPOTENT`.

## Smallest implementation for Gmail draft creation

Add one new tool in `src/agentsys/tools/gmail.py`:

```text
gmail_create_draft(to, subject, body)
```

The tool will create one plain-text MIME message and call Gmail's
`users.drafts.create` endpoint over `httpx`, matching the existing no-Google-SDK
style. It will return structured output with at least:

- `draft_id`
- `message_id` when Gmail returns it
- `to`
- `subject`
- a verification-friendly status

No raw OAuth token, account id, authorization header, or arbitrary Gmail API
payload is accepted from the model. `user_id` remains runtime-injected.

## Policy classification

Draft creation mutates an external system. It is not a read and not local to
the task sandbox.

The tool should declare:

```text
ActionType.EXTERNAL_WRITE
Risk.MEDIUM
```

Existing `policy.decide()` already requires approval for all
`EXTERNAL_WRITE` calls. No Gmail-specific policy engine is needed.

## Execution-safety classification

Gmail draft creation does not provide a reliable idempotency key that this
system can pass and Gmail will honor for `users.drafts.create`.

Therefore the tool must not be classified idempotent. A blind retry after a
timeout or crash could create duplicate drafts. The conservative Phase 3
classification is:

```text
ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT
```

This means Phase 3 will still dedupe a known successful effect by effect key,
but it will not automatically retry failed/ambiguous draft creation.

## Idempotency limitations

Successful local `ToolCall` rows dedupe a repeated same-effect call in the
same task/user scope. That protects against normal replays after success.

It does not prove exactly-once. If Gmail creates the draft and the worker dies
before the local success is persisted, the local row remains ambiguous. Because
there is no external idempotency key, the system cannot safely know whether a
second create would duplicate the draft.

## Verification strategy

After a successful draft create, the tool returns the Gmail `draft_id`.
Phase 4 verification should deterministically verify `gmail_create_draft` by
using existing `ToolCall` evidence and a minimal Gmail read helper:

- fetch the draft by id, when possible
- confirm the draft exists
- confirm recipient matches the requested `to`
- confirm subject matches the requested `subject`

Body verification should be conservative. Gmail may normalize MIME content, so
Phase 5 can avoid byte-for-byte body equality unless the parsed plain text is
available cleanly.

## Crash ambiguity

The critical unsafe window is:

```text
ToolCall(success=NULL) committed
Gmail draft created
worker dies before ToolCall success is persisted
```

Recovery will see the unresolved `ToolCall`. Because the tool is
`NON_RETRYABLE_SIDE_EFFECT`, Phase 3 must hold it ambiguous and refuse an
identical replay. The user or operator must inspect Gmail or decide how to
continue. Creating another draft blindly is not acceptable.

## Recovery strategy

Reuse Phase 3 recovery:

- `reconcile_orphaned_subtasks()` marks stranded subtasks failed
- `execution.resolve_ambiguous_calls()` leaves non-idempotent ambiguous
  calls unresolved
- subsequent identical execution is refused by the existing effect ledger
- Phase 4 verification maps the ambiguous refusal to human-safe handling

No Gmail-specific recovery table is added.

## Security/privacy concerns

- Access tokens, refresh tokens, authorization headers, and Google credentials
  must never be LLM arguments or trace output.
- `user_id` must remain injected from `Task.owner_id`.
- Approval snapshots necessarily include the recipient, subject and body
  because those are the meaningful effect being approved; they must not
  include OAuth secrets.
- Tool result and verification output should prefer `draft_id`, `message_id`,
  recipient and subject. Avoid unnecessary duplication of the full email body
  outside the already-existing validated kwargs / `Escalation.context` /
  `ToolCall.input` records. Trace spans redact the draft body.
- Gmail draft content is user-authored outbound content, not untrusted inbound
  content; inbound Gmail bodies remain wrapped by read tools.

## What we explicitly will NOT implement

- no `gmail_send_email`
- no draft update/delete/send
- no external idempotency-key protocol
- no compensation/saga framework
- no new execution ledger or action table
- no Plan/PlanStep tables
- no new workflow engine or planner redesign
- no other external writes such as Calendar, Drive writes, Slack, LinkedIn, X,
  database writes, filesystem deletion, or arbitrary MCP writes
