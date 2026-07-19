# Hidden Tower Defence — Product, Backend, and Full-Screen Game Remaining-Work Plan

## Purpose

This plan is for the agent responsible for the remaining application and game
work. It is self-contained and does not require reading:

- Software Manufacturing Plant
- ChatGraph

The goal is to complete the secure intelligence product and replace the current
placeholder Canvas with a full-screen, top-down pixel-art game synchronized
with sanitized live backend logs.

This plan is intentionally parallel-safe with
`PLATFORM_DELIVERY_REMAINING_PLAN.md`.

## Current application state

The repository currently contains:

- A FastAPI application.
- Local SQLite persistence.
- A production Cloud Spanner repository.
- Basic event persistence and WebSocket broadcast.
- Basic approvals and incidents.
- Apify, HiddenLayer, and Nemotron clients.
- A heartbeat that can invoke Apify.
- Controlled clean, restricted, and locked fixtures.
- A placeholder Canvas castle.
- A rudimentary intelligence query endpoint.
- Four basic policy/persistence tests.

Verified behavior:

- Live clean content remains `NORMAL` and reaches Nemotron.
- Live adversarial content is detected by HiddenLayer as `High`/`Alert`.
- The malicious item transitions to `LOCKED` before Nemotron.
- Public service URL:
  `https://hiddentowerdefence-588376054847.us-central1.run.app`
- Public health route: `/health`
- Public readiness route: `/readyz`

The current UI is not the requested frontend. It is a card-based placeholder
with primitive Canvas rectangles and side panels.

## Product objective

Build a secure developer-community intelligence product with a game-like
security demonstration:

- Real Hacker News stories and comments enter as untrusted content.
- HiddenLayer scans every required model and tool boundary.
- Nemotron/Claw creates structured intelligence.
- Users can query recent Hacker News developer-market signals.
- A full-screen top-down castle game visualizes the real backend pipeline.
- A sanitized backend console displays the same events in real time.
- Clean and controlled-malicious inputs run together during the demo.

All trend claims must be labeled as Hacker News/developer-community signals,
not comprehensive market-wide conclusions.

## Parallel file ownership

This product agent owns:

- `app/**`, except platform agents may only call `app.migrations`
- `tests/**`
- `fixtures/**`
- Frontend/game source and assets
- `pyproject.toml`
- `Dockerfile`
- Product portions of `README.md`
- New product documentation under `docs/product/**`
- New product validation reports under `reports/product/**`

This product agent must not edit:

- `infra/**`
- `.github/workflows/**`
- Terraform lock files
- Platform runbooks or platform validation reports

Preserve this deployment contract:

- Listen on `PORT`, default `8080`.
- Keep `/health` and `/readyz`.
- Keep `python -m app.migrations`.
- Keep existing production variable and secret names.
- Continue serving static/game assets from the same FastAPI service unless a
  documented handoff explicitly changes that contract.

If the platform contract must change, write
`docs/product/platform-contract-request.md`; do not edit platform-owned files.

## Parallel execution protocol

1. Create a dedicated product branch/worktree from the commit containing both
   remaining-work plans.
2. The platform agent creates a separate branch/worktree from the same commit.
3. Do not merge or cherry-pick platform changes while either agent is still
   implementing.
4. Respect the file ownership boundaries above.
5. Do not mutate GCP, Porkbun, GitHub deployment configuration, or Terraform
   state; the platform agent has sole production control-plane authority.
6. Record any required platform contract change under
   `docs/product/platform-contract-request.md`.
7. Finish and validate the product against the preserved container and runtime
   contract.
8. During integration, merge the product branch first so the platform agent can
   build and deploy the final product image through the completed delivery
   pipeline.

This sequencing lets both agents work simultaneously without editing the same
files or racing on production resources.

## Workstream 1 — Complete the persistence model

The plan requires durable business and security state, not only events.

Implement production/local parity for:

- Runtime trust state
- Heartbeat lease
- Source items and comments
- Exact Apify runs and terminal state
- Raw and normalized HiddenLayer scans
- Trust transitions and reasons
- Persistent taint records
- Nemotron triage
- Topics and entities
- Watchlists and matches
- Trend snapshots
- Saved briefs
- Deferred approvals
- Incidents and acknowledgement
- Quarantine records
- Mock outbox
- Ordered UI events
- Migration history

Requirements:

- Every source item is persisted before processing.
- Deduplication is transactional.
- Event IDs are globally ordered.
- Approval resolution and tool execution are atomic.
- Restart recovery resumes pending work.
- SQLite and Spanner repository behavior is covered by the same contract tests.
- Spanner operations never block the event loop.

### Migration safety

The current migration command replays raw `CREATE TABLE` statements. Replace it
with idempotent, version-tracked migrations:

1. Introduce a migration history table or use schema introspection.
2. Record each successful migration.
3. Skip already-applied migrations.
4. Fail on partially applied or out-of-order migrations.
5. Keep migrations additive and rollback-compatible.
6. Test first deploy and repeated no-op deploy.

The platform agent may invoke the migration command but must not implement it.

## Workstream 2 — Reliable Apify ingestion and recovery

Primary Actor:

- `gentle_cloud/hacker-news-scraper`
- Mode: `new`
- Include comments: true
- Batch size: 10–20

Fallback Actor:

- `onescales/hacker-news-data`

Implement:

- Persist run ID immediately after starting an Actor.
- Poll the exact run ID.
- Resume polling after restart.
- Normalize both Actor schemas.
- Retry bounded transient failures.
- Activate fallback only after the primary fails according to policy.
- Deduplicate by `hn:{id}`.
- Validate malformed items without crashing the heartbeat.
- Limit comments and content size before security scanning.
- Emit source/run events for the game and console.
- Record Actor name, run ID, dataset ID, duration, item count, and failure.

Do not query "latest run" as recovery logic.

## Workstream 3 — Complete all security boundaries

Current orchestration covers intake, prompt, and response scans. Complete the
full required set:

1. Ingested content
2. Prompt before Nemotron
3. Nemotron response
4. Tool name and arguments before dispatch
5. Tool result before returning it to the model

For every scan:

- Persist raw provider response.
- Persist normalized finding.
- Emit scan-started and scan-completed events.
- Preserve boundary and source lineage.
- Apply bounded retries and fail-closed policy.
- Never expose secret values in logs, events, or frontend payloads.

Implement durable taint:

- A flagged source ID taints derived prompts and tool actions.
- A later clean scan does not silently clear taint.
- Only explicit incident resolution clears it.

Implement policy transitions:

- `NORMAL`: controlled tools may run.
- `RESTRICTED`: reads may run; writes/outbound actions wait for approval.
- `LOCKED`: raw content is withheld from Nemotron and all tools stop.
- Acknowledge moves `LOCKED` down one level.
- Resume is a separate action returning to `NORMAL`.
- Every transition has a persisted reason.

## Workstream 4 — Complete controlled tool execution

Implement:

- `save_brief`
- `draft_alert`
- `quarantine_item`
- `mock_web_fetch`

Required flow:

1. Validate model output.
2. Persist tool request.
3. Scan tool name and arguments.
4. Check trust and taint policy.
5. Execute immediately, defer for approval, or block.
6. If deferred, persist all arguments required for later execution.
7. On approval, atomically execute once.
8. On denial, quarantine without execution.
9. Scan tool result.
10. Persist and emit the final outcome.

Use idempotency keys so repeated approval requests cannot execute twice.

`draft_alert` remains a mock outbox action. It must not send email.

## Workstream 5 — Intelligence product functionality

### Enrichment

Persist validated fields:

- Summary
- Category
- Priority
- Sentiment
- Topics
- Companies
- Products
- Technologies
- Repositories
- CVEs, where explicitly present
- Recommended action
- Rationale

### Watchlists

Add CRUD for watchlists containing:

- Name
- Search terms
- Companies/products
- Topics
- Minimum priority
- Optional sentiment or engagement thresholds

Persist matches with source evidence and rationale.

### Briefs and mock alerts

Add:

- Intelligence inbox
- Saved briefs
- Mock alert outbox
- Read/resolved state
- Links to source items and scans

### Deterministic trends

Do not ask Nemotron to invent counts. Calculate:

- Current versus previous time-window mentions
- Engagement changes
- Sentiment changes
- Topic/entity frequency
- Watchlist match volume
- Evidence count
- Confidence thresholds

Nemotron may summarize and explain these deterministic results.

### Claw query responses

`POST /api/intelligence/query` should:

1. Parse the requested topic and time range.
2. Retrieve relevant enriched records.
3. Calculate structured trend evidence.
4. Ask Nemotron to explain only the supplied evidence.
5. Return citations to source items/comments.
6. Clearly label scope and confidence.
7. Say when evidence is insufficient.

Add APIs for:

- Watchlists
- Briefs
- Trends
- Source evidence
- Query history

## Workstream 6 — Replace the frontend with a full-screen game

### Engine

Use a maintained browser 2D game engine suitable for:

- Tilemaps
- Sprite animation
- Cameras
- Input
- Responsive scaling
- Deterministic event-driven scenes

Phaser is the default recommendation. Vendor/build it through the product-owned
frontend toolchain; do not load runtime code from an unpinned CDN.

### Full-page experience

The game occupies the entire viewport. It should feel like a small top-down
pixel-art game, not a dashboard containing a canvas.

World layout:

- Road leading to the castle
- Outer walls
- Gatehouse
- Courtyard
- Central keep
- Guard stations
- Crossbow towers
- Intelligence/workshop building
- Quarantine area
- Outbound road for messengers

HUD and console are game overlays rather than separate page columns.

### Asset strategy

Create original, repository-owned pixel art:

- Tile sheet
- Castle walls and gates
- Towers and crossbows
- Guards
- Citizens
- Messengers
- Cloaked/restricted travelers
- Enemies
- Arrows and impact effects
- Alert beacons and banners
- Scrolls and tool icons

Assets may be programmatically generated or created as PNG sprite sheets.
If third-party assets are used:

- Require verified CC0 or compatible licensing.
- Commit the license and attribution.
- Do not depend on a remote asset host at runtime.
- Keep a consistent palette and tile size.

### Real-time entity mapping

Map backend events:

| Event | Game behavior |
| --- | --- |
| `content_received` | Traveler spawns and walks toward the gate |
| `scan_started` | Guards inspect the traveler |
| clean `scan_completed` | Gate opens; citizen enters |
| `model_started` | Keep/intelligence building activates |
| `model_completed` | Messenger exits carrying a scroll |
| `tool_requested` | Worker travels to workshop |
| `tool_completed` | Worker returns with result |
| `approval_created` | Traveler is detained at the gate |
| approved | Gate opens and traveler enters |
| denied | Guards escort traveler to quarantine |
| high/critical detection | Traveler becomes an enemy |
| `state_changed: LOCKED` | Gate closes and towers arm |
| `incident_created` | Crossbows fire; alarms activate |
| `heartbeat` | Watchtower beacon or flag pulses |

The backend is authoritative. The game never decides whether content is safe.

### Synchronized backend console

Add a collapsible/resizeable console overlay showing sanitized real-time
backend activity:

- Timestamp
- Source/run ID
- Event type
- Scan boundary
- Threat level/action
- Trust transition
- Model lifecycle
- Tool lifecycle
- Persistence outcome

Requirements:

- Console entries come from structured backend events/logs.
- Clicking an entity highlights its console history.
- Clicking a log focuses the corresponding entity/camera position.
- Secrets and full sensitive payloads are excluded.
- Raw HiddenLayer JSON is available only in a protected detail view.
- Reconnect retrieves missed events.

### Automatic demo mode

Add an operator-controlled demo mode:

- Real clean Apify items continue to arrive.
- Controlled malicious fixtures are injected at a safe cadence.
- Every simulated event is visibly labeled.
- Demo sequence covers clean, restricted, locked, malicious tool arguments, and
  malicious tool-result paths.
- The sequence is repeatable and can be reset without deleting production
  intelligence.

## Workstream 7 — Secure operator interaction

The browser must not receive `OPERATOR_TOKEN`.

Implement:

- Operator login/verification endpoint.
- Short-lived, signed, HttpOnly, Secure, SameSite cookie.
- CSRF protection for mutating browser requests.
- Session expiry and logout.
- Approval, denial, acknowledgement, resume, fixture injection, and demo-mode
  controls require the operator session.
- Read-only game viewing may remain public.
- Rate limiting for login and mutating routes.

Do not store the raw operator token in local storage, JavaScript, events, or
HTML.

## Workstream 8 — Event and reconnect correctness

Implement:

- Persist-before-broadcast for all events.
- Event schema version.
- Stable source/run/entity correlation IDs.
- Cursor-based replay.
- WebSocket reconnect with no duplicates.
- Initial scene reconstruction after refresh.
- Active approval and incident reconstruction.
- Bounded client event queue.
- Server-side protection against slow WebSocket clients.

The game must reconstruct the authoritative state rather than replay every old
animation from the beginning.

## Workstream 9 — Tests

### Unit tests

- All trust transitions
- Acknowledge and resume
- Taint propagation
- Tool policy
- Tool idempotency
- Actor normalization
- Trend calculations
- Citation generation
- Log redaction
- Operator session validation

### Repository contract tests

Run equivalent behavior against:

- SQLite
- Spanner emulator

Cover:

- Deduplication
- Event ordering
- Approval atomicity
- Restart recovery
- Migration idempotency

### Provider adapter tests

Use mocked HTTP for:

- Apify start/poll/dataset/fallback
- HiddenLayer OAuth, retries, refresh, and normalization
- Nemotron valid, malformed, repaired, and failed output

### API and WebSocket tests

- Public reads
- Protected mutations
- Event replay
- Reconnect
- Approval flow
- Incident flow
- Demo injection
- Intelligence queries

### Browser/game tests

Use browser automation to verify:

- Full viewport rendering
- Clean citizen path
- Restricted gate detention
- Approve and deny flows
- Enemy/crossbow path
- Messenger output path
- Console/entity synchronization
- Refresh recovery
- Desktop and tablet layouts

Capture screenshots or videos as review artifacts.

### Live bounded tests

- Maximum-three-item Apify run
- Clean HiddenLayer scan
- Adversarial HiddenLayer scan
- Minimal Nemotron completion
- Full live pipeline to persisted event and game-consumable output

Never print credentials or raw secret-bearing requests.

## Workstream 10 — Production handoff

The product agent does not deploy or edit infrastructure.

Before handoff:

1. Run Ruff and the full test suite.
2. Build the production Docker image locally or in CI.
3. Start the container and verify:
   - `/`
   - `/health`
   - `/readyz`
   - WebSocket
   - Static/game assets
4. Verify the migration command is idempotent.
5. Write `reports/product/product-validation-summary.yaml` with:
   - Status
   - Tests
   - Browser artifacts
   - Live provider results
   - Required platform contract
   - Known limitations
6. Provide the platform agent with the commit SHA and container contract.

## Required validation commands

At minimum:

```bash
ruff check app tests
pytest -q
python -m compileall -q app tests
docker build -t hiddentowerdefence:product .
```

Also run focused integration, browser, and live suites introduced by this work.

## Deliverables

- Complete persisted security and intelligence model
- Idempotent migrations
- Reliable primary/fallback Apify ingestion
- Five real HiddenLayer scan boundaries
- Durable taint, incidents, approvals, and tool execution
- Watchlists, briefs, trends, and cited Claw queries
- Full-screen Phaser top-down castle game
- Original/local pixel-art asset set
- Synchronized sanitized backend console
- Secure operator sessions
- Automatic mixed clean/malicious demo mode
- Comprehensive automated and browser tests
- Product validation report

## Definition of done

This product plan is complete when:

1. Real Apify content reaches HiddenLayer, Nemotron, persistence, and the game.
2. Clean content enters the castle as citizens.
3. Model output exits as messengers.
4. Restricted content waits at the gate for a secure operator decision.
5. Approval executes exactly once; denial quarantines.
6. High-risk content locks the agent and triggers crossbow defense.
7. Tool arguments and tool results are scanned and visibly represented.
8. Restart and browser refresh preserve authoritative state.
9. Users can query evidence-backed developer-community trends.
10. The live console and game remain synchronized.
11. The game occupies the full viewport and uses production-owned assets.
12. Migrations are repeatable and safe for CI/CD.
13. SQLite, Spanner emulator, API, WebSocket, provider, and browser tests pass.
14. No application change requires editing platform-owned files.
