# Versioned game event and reconnect contract

Every event is persisted before it is offered to a WebSocket subscriber.

```json
{
  "schema_version": 1,
  "id": 42,
  "type": "scan_completed",
  "source_item_id": "hn:123",
  "run_id": "apify-run-id",
  "entity_id": "hn:123",
  "correlation_id": "tool-or-source-correlation",
  "trust_state": "RESTRICTED",
  "payload": {
    "boundary": "tool_arguments",
    "threat_level": "Medium",
    "action": "Alert"
  },
  "occurred_at": "2026-07-19T00:00:00Z"
}
```

## Public payload rules

- Payloads contain lifecycle metadata, normalized findings, identifiers, and
  bounded display strings.
- Secret-like keys are recursively redacted before persistence/broadcast.
- Raw prompts, hostile content, provider request headers, credentials, and raw
  HiddenLayer JSON are excluded.
- Raw scan audit data is available only from the operator-protected evidence
  endpoint.
- Simulated records include `simulated: true`.

## Reconnect

1. Load `GET /api/scene` to reconstruct trust state, active approvals,
   incidents, demo state, and the latest cursor.
2. Render the authoritative snapshot without replaying old animations.
3. Open `WS /ws/events?after_id={cursor}`.
4. The server subscribes before replay, sends ordered persisted events, then
   discards buffered IDs already sent by replay.
5. The client deduplicates by global event ID and keeps bounded histories.

`GET /api/events?after_id={cursor}&limit={n}` provides the same cursor-based
catch-up contract for non-WebSocket clients.

Slow subscribers have bounded queues and are disconnected rather than allowed
to consume unbounded server memory.

## Game mapping

The backend is authoritative. The game maps lifecycle events to deterministic
movement and effects; it never decides whether content is safe. Entity and
console selections are joined through `entity_id` and `source_item_id`.
