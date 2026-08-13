# Architecture

The application is one local process: FastAPI serves the compiled React UI/API while one in-process worker claims captured or recoverable source records. SQLite and preserved originals are the durable coordination mechanism; there is no broker, Redis, cloud database, watcher, or second backend.

```text
Browser on 127.0.0.1
        |
FastAPI API + compiled React assets
        |
PortfolioService -- SQLite WAL/FTS5 (outside OneDrive)
        |
Preserved originals/chunks (inside configured OneDrive root)
        |
Fake adapter (tests) or approved internal HTTPS LLM only
```

## Capture and processing

1. Stream into a temporary file inside the final destination and enforce the configured byte limit.
2. Hash, inspect a native message ID when possible, and check project/scope deduplication.
3. Atomically rename the verified original, then commit the captured source row.
4. The worker extracts deterministic chunks/locators and preserves email attachments independently.
5. A bounded evidence package first goes through a cited project-fit check. Conflicting or low-fit direct intake remains `pending` and enters Review Queue before any project memory mutation.
6. After automatic or user-confirmed fit, the package goes to the knowledge-update operation and citation IDs are validated against exactly that evidence package.
7. Summary, append-only updates, supported action operations, chunk checkpoints, active memory state, and completed source state commit in one SQLite transaction.

AI unavailability leaves evidence durable in `pending_ai`; retry is idempotent. The worker stops automatically claiming a source after `automatic_ai_attempts` failures, while the manual **Retry pending** control can explicitly try it again. Unsupported inputs remain preserved as `unsupported`. Startup recovers interrupted `processing` rows.

## SNOW import

CSV/XLSX rows upsert by `Number`. `Updated` is only the stale-row guard. Cumulative comment boundaries and entry hashes are deterministic before any AI call. Only completed entry hashes count as checkpoints; malformed or pending-AI cells do not advance the project cell hash. CSV rows with fewer or more fields than their header go to review. XLSX values beyond the final named header go to review, while blank trailing cells remain valid because spreadsheet readers cannot distinguish an omitted optional cell from an intentionally blank one.

## Multi-project routing

The root intake source remains unassigned until decisions are reviewed. An approved segment creates a derived child source under the selected project and copies only the cited chunks into that project's FTS scope. The creating review decision and confirmed narrow routing rule remain linked and auditable.

## Source lifecycle

Removal is a reversible memory operation, not a filesystem deletion. The service atomically relocates the package between the project folder and managed `Archive`, updates the source tree and chunk scope in one transaction, records an append-only lifecycle event, and regenerates the current summary from active knowledge only. Knowledge, summaries, project chat, updates, and actions use committed active memory. Archive search remains broader so preserved pending, unsupported, and Shared Intake originals stay discoverable; only removed packages are excluded. A failed regeneration exposes no stale current summary; prior immutable versions remain available for audit.

## UI/runtime boundary

Node is a build dependency only. Vite emits repository-local assets with system fonts and no CDN/public runtime resources. FastAPI serves the SPA fallback after all `/api` routes.
