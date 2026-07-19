# Security boundaries, taint, and controlled tools

## HiddenLayer boundaries

The pipeline persists a raw protected audit record and a normalized public
finding for:

1. ingested story/comment content;
2. the complete prompt before Nemotron;
3. the validated Nemotron response;
4. controlled tool name and arguments before dispatch;
5. controlled tool output before model reuse or completion.

Each boundary emits sanitized `scan_started` and `scan_completed` events. OAuth
tokens are cached, one authorization failure forces a refresh, transient
statuses receive bounded retries, and configured provider failure is normalized
into a fail-closed result.

## Trust and taint

Findings escalate the persisted runtime state monotonically during processing:

- `NORMAL`: controlled tools can run.
- `RESTRICTED`: `mock_web_fetch` may run; writes and outbound mock actions are
  deferred.
- `LOCKED`: raw source content is withheld and every tool is blocked.

A flagged source creates a durable taint record. Prompt, response, tool request,
and result records retain source lineage. A later clean scan cannot clear the
taint.

Operator recovery is intentionally staged:

1. acknowledge an open incident (`LOCKED` → `RESTRICTED`);
2. resolve the incident with a reason (clears the incident source taint);
3. explicitly resume when no active taint remains (`RESTRICTED` → `NORMAL`).

All transitions and reasons are persisted and emitted.

## Controlled tools

- `save_brief`: creates a durable intelligence brief.
- `draft_alert`: creates a mock outbox record and never sends email.
- `quarantine_item`: records durable quarantine state.
- `mock_web_fetch`: returns only repository-controlled fixture content.

Every model request is schema-validated and persisted before scanning.
Canonical source/tool/argument JSON produces a deterministic SHA-256
idempotency key.

Restricted writes create an approval containing every argument needed for
later execution. Approval atomically claims the pending action, executes it at
most once, scans its result, and records the final status. Repeated approval
requests cannot execute the tool again. Denial records quarantine without
calling the requested tool.

## Operator browser security

The browser sends `OPERATOR_TOKEN` only to the login endpoint. Successful
verification creates a short-lived signed HttpOnly, SameSite=Strict cookie and
returns a session-bound CSRF value held only in memory. Mutations require both
the cookie and CSRF header. Login and mutations are rate limited. Logout clears
the cookie.

Production requires `OPERATOR_TOKEN`; the raw token is never written to HTML,
JavaScript state, local storage, events, or logs.
