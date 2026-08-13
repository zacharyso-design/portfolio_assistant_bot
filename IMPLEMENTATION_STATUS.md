# Implementation status

Revision 1 is complete against the supplied 2026-08-12 build handoff using fictional data and the deterministic fake LLM adapter. Production endpoint connectivity remains configuration-dependent because those values were not supplied.

The user-supplied **Codex Build Handoff — CHIO Portfolio Assistant, Revision 1 (2026-08-12)** is the controlling directive. `LEAN_MVP_BUILD_REQUIREMENTS.md` and `MVP_BUILD_REQUIREMENTS.md` are retained as explicitly superseded background references.

## Gate evidence

| Phase | Delivered | Verification |
| --- | --- | --- |
| 0 — environment/skeleton | Recorded spike, pinned Python/Node dependencies, TOML validation, SQLite migration/WAL/FTS5 fallback, fake/internal adapters, loopback FastAPI server, compiled React shell | Clean migration and configuration test passed on Python 3.14.4 / SQLite 3.50.4 with FTS5. Invalid Host/Origin tests pass. |
| 1 — portfolio/projects | System `Unassigned`, group management, project/folder creation, manual fields, grouped 250-row board, independent filters/search | Restart persistence and project-folder tests pass. Scale board measured 23.9 ms; filtered board 1.2 ms. |
| 2 — SNOW import | CSV/XLSX preservation/parser, stable-number upsert, separate SNOW metadata, stale guard, deterministic cumulative-entry hashes/checkpoints/retry | CSV and XLSX first/incremental/repeat/stale/malformed/outage cases pass; only two newly appended entries process oldest-to-newest. |
| 3 — direct intake/knowledge | Atomic capture, SHA/native-ID dedup, EML/MSG/attachment/text/transcript/DOCX/PDF extraction, pending/retry, cited transactional updates | Fictional EML and standards-compliant MSG with attachments pass; DOCX/text-layer PDF/transcript pass; scanned PDF/unknown input preserve as `unsupported`; exact-once outage and interrupted-attachment recovery pass. The citation allow-list is tested against the exact bounded evidence bytes. |
| 4 — chat/evidence | Project-only FTS retrieval, bounded cited chat, excerpt/original access | Contradictory two-project isolation, uncited-answer rejection, literal wildcard fallback, enriched citation/original tests pass. 50,000-chunk retrieval measured under 20 ms. |
| 5 — review/routing/actions | Multi-project intake, confirmation-only segment apply, derived project-scoped evidence, linked narrow routing rules, cited action create/progress and closure review | No pre-approval mutation, direct cross-project detection, corrected target/rule reuse, routed chat retrieval, incomplete-action completion, progress, closure protection, and no AI project completion pass. |
| 6 — morning/Windows/security | Prior-calendar-day idempotent run, banner/manual run, real Task Scheduler scripts, venv install, PyInstaller one-folder build, backup/security docs/tests | Daily window/idempotency pass. On 2026-08-12, the scheduled task was installed, triggered with `Last Result: 0`, and removed; the packaged executable served HTTP 200/FTS5. These workstation observations are reproducible with the documented build and scheduler scripts. |

## Automated acceptance

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -s
```

Result: **20 passed**. The suite includes the real background-worker claim path and explicit non-test fake-adapter guard. The warnings are an upstream FastAPI use of Python 3.14's deprecated `asyncio.iscoroutinefunction`; no application warning or failure occurred.

Scale fixture and measured workstation timings (LLM time excluded):

- 250 projects / 150 active.
- 50,000 source chunks.
- 5,000 project updates.
- 2,000 action items.
- Initial board API: under 100 ms (requirement: under 500 ms).
- Filter/search API: under 50 ms (under 250 ms).
- Project page service: under 5 ms (under 300 ms).
- FTS evidence retrieval: under 20 ms (under 500 ms).

Frontend production build: TypeScript and Vite succeeded with no public runtime assets. A dated 2026-08-12 in-app browser check at 1024 × 768 covered grouped keyboard-reachable portfolio rows, separate filters, manual project controls, direct drop, visible summary citations with exact source/locator and original access, persistent right-side project chat, Review Queue transcript controls, and fixed-column SNOW import. The document width remained within the viewport.

## Windows artifact

`dist\CHIO-Portfolio-Assistant-Windows.zip` contains the one-folder executable, compiled React assets, migrations, example configuration, README, and Task Scheduler scripts. On 2026-08-12, the packaged executable independently passed `config-test`, `migrate`, health, FTS5, and SPA HTTP checks. Rebuild with `scripts\Build-Distribution.ps1` and repeat those commands to reproduce the observation.

The dated workstation observations were manual release checks, not automated-test claims. They were performed with `scripts\Install-MorningTask.ps1` plus `schtasks /Run` and `schtasks /Query`, `scripts\Build-Distribution.ps1`, the packaged `PortfolioAssistant.exe --config config.toml config-test` / `migrate` / `serve` commands, HTTP requests to `/api/health` and `/`, and the in-app browser fixed to 1024 × 768. The commands/scripts are retained; the local Task Scheduler record and browser session were deliberately cleaned up afterward.

## Production configuration still required

1. Actual locally synced government OneDrive root.
2. Approved internal LLM base URL/chat path, model, authentication header/scheme, and key environment variable.
3. DoD CA bundle path if required by the internal endpoint.
4. Approved package/distribution installation method on the GFE.
5. GFE Task Scheduler permission and desired local run time if not 0600.

## Deferred unchanged

- Live ServiceNow synchronization, mapping UI, or outbound ticket changes.
- Cross-project/portfolio chat, embeddings, vector database, or knowledge graph.
- Separate Daily Digest, global action board, calendars/meeting management, discussion tags, or due-out screens.
- Briefs, staff messaging, Outlook/mailbox plugins, live email ingestion, or folder watching.
- OCR/scanned documents, images, audio/video processing or transcription.
- Mobile, multiple users/roles/login, cloud database, public/commercial APIs, analytics/telemetry, or general autonomous agents.
