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
5. A bounded evidence package goes to the selected LLM adapter.
6. Citation IDs are validated against exactly that package.
7. Summary, append-only updates, supported action operations, chunk checkpoints, and completed source state commit in one SQLite transaction.

AI unavailability leaves evidence durable in `pending_ai`; retry is idempotent. The worker stops automatically claiming a source after `automatic_ai_attempts` failures, while the manual **Retry pending** control can explicitly try it again. Unsupported inputs remain preserved as `unsupported`. Startup recovers interrupted `processing` rows.

## SNOW import

CSV/XLSX rows upsert by `Number`. `Updated` is only the stale-row guard. Cumulative comment boundaries and entry hashes are deterministic before any AI call. Only completed entry hashes count as checkpoints; malformed or pending-AI cells do not advance the project cell hash. CSV rows with fewer or more fields than their header go to review. XLSX values beyond the final named header go to review, while blank trailing cells remain valid because spreadsheet readers cannot distinguish an omitted optional cell from an intentionally blank one.

## Multi-project routing

The root intake source remains unassigned until decisions are reviewed. An approved segment creates a derived child source under the selected project and copies only the cited chunks into that project's FTS scope. The creating review decision and confirmed narrow routing rule remain linked and auditable.

## UI/runtime boundary

Node is a build dependency only. Vite emits repository-local assets with system fonts and no CDN/public runtime resources. FastAPI serves the SPA fallback after all `/api` routes.
