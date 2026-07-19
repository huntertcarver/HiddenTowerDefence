# Product storage, migrations, and operator guide

## Repository parity

SQLite is the local/test implementation. Cloud Spanner is the production
implementation. Services depend on one asynchronous repository contract.
Blocking Spanner client calls are isolated with `asyncio.to_thread`.

Durable records cover source items/comments, exact source runs, runtime trust,
heartbeat leases, scans, transitions, taint, incidents, tools, approvals,
quarantine, triage terms/entities, watchlists/matches, briefs, mock alerts,
queries, demo state, and ordered events.

## Migration safety

SQLite migrations run transactionally when the local repository connects.
Versions are ordered and checksummed.

The Spanner migration command handles the deployed pre-history schema:

- no initial tables: apply v1;
- all v1 tables and no history: create migration history and baseline v1;
- some v1 tables: fail and request operator investigation;
- existing history with a gap or changed checksum: fail;
- already current: no-op.

Migrations are additive. Compatible additions remain in place during an
application-image rollback.

## Recovery

- A persisted heartbeat lease prevents overlapping workers.
- Nonterminal exact Apify runs are resumed before a new Actor is started.
- Pending/processing source items are recovered on application startup.
- Source deduplication and global event allocation are transactional.
- Tool idempotency and approval claims survive request retries.

## Operator demo

1. Log in from the game HUD.
2. Start the repeatable sequence or inject one fixture.
3. Clean travelers enter; restricted travelers wait at the gate.
4. Approve to execute exactly once or deny to quarantine.
5. Locked incidents close the gate and arm crossbows.
6. Acknowledge, resolve with a reason, then resume.

Automatic demo recovery between scenarios is explicitly labeled and does not
delete production intelligence or audit history.

## Production handoff contract

- `PORT` defaults to `8080`.
- Liveness remains `/health`; readiness remains `/readyz`.
- Migrations remain `python -m app.migrations`.
- Existing production environment and secret names are unchanged.
- FastAPI serves the compiled game from the same container.

No platform contract change is required by this product implementation.
