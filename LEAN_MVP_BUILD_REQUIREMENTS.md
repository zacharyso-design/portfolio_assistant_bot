# Portfolio Assistant — Lean MVP Build Requirements

Revision 3, 2026-08-12

> **Status: background reference only.** The user-supplied **Codex Build Handoff — CHIO Portfolio Assistant, Revision 1 (2026-08-12)** explicitly supersedes this document where they conflict and authorizes the wider implemented scope. See `IMPLEMENTATION_STATUS.md` for the controlling-scope record.

## Mission

Build the smallest usable version of the Portfolio Assistant around one complete workflow:

> Select a project → drop a source onto it → preserve and process the source → update durable project knowledge with citations → ask questions against that knowledge.

The MVP is complete when this workflow is reliable. Features that do not directly enable or validate it are out of scope until every acceptance check passes.

## Product boundary

- Single user.
- Runs locally on the user's government-furnished Windows computer.
- Original files, extracted text, project knowledge, prompts, and model responses remain within the government environment.
- The only permitted external processing destination is the approved internal DoD/DHA LLM endpoint.
- Authoritative project files live in locally synced government OneDrive folders.
- The SQLite database lives outside the OneDrive sync root, such as under `%LOCALAPPDATA%\PortfolioAssistant\`.

## MVP capabilities

### 1. Projects

The application provides a simple project list and project page.

Creating a project requires only a name. The application:

1. Creates a unique project record.
2. Creates a matching folder beneath the configured OneDrive project root.
3. Opens the project page containing:
   - Current project knowledge summary
   - Recent knowledge updates
   - Source-processing history
   - Drag-and-drop area
   - Project-scoped question box

Portfolio groups, priority, status, dashboards, and archival workflows are not part of this MVP.

### 2. Direct drag-and-drop

A user can drag a source directly onto a known project. Because the destination project is explicit, no project-routing workflow is required.

The MVP must support saved Outlook email in both `.msg` and `.eml` formats. Email processing includes:

- Message subject, body, sender, recipients, date, native message ID when available, and thread metadata when available
- Original saved email
- Original attachments
- Extracted text from supported attachments

The first implementation may also support `.txt` and `.docx` because they are inexpensive to extract. Other formats, including PDF, Excel, CSV, scanned documents, and generic multi-project files, must not delay MVP completion. If dropped before support is added, the application preserves the original and displays `unsupported`; it never reports that the source was processed.

For every drop, the application:

1. Copies the original into the project's OneDrive folder unless it is already within that folder.
2. Computes a SHA-256 hash before AI processing.
3. Creates or links the source record.
4. Extracts supported text.
5. Stores searchable chunks with source locators.
6. Sends only that project's prior summary and the new source evidence to the approved LLM.
7. Applies the returned project-knowledge update in one database transaction.
8. Displays the result and its source citations on the project page.

### 3. Durable project-knowledge updates

Project knowledge consists of two intentionally simple layers:

- **Current summary:** a concise description of the project's current known state.
- **Update history:** append-only, dated changes derived from individual sources.

The LLM returns structured JSON:

```json
{
  "updated_summary": "Current, source-grounded project summary",
  "updates": [
    {
      "text": "Material change learned from the source",
      "citations": [
        {
          "source_id": 123,
          "chunk_id": 456
        }
      ]
    }
  ],
  "needs_review": false,
  "review_reason": null
}
```

Rules:

- Every update must cite at least one chunk from the newly dropped source.
- The updated summary may use the prior summary but may introduce new claims only when supported by cited source evidence.
- A malformed response, missing citation, unsupported destructive change, or explicit uncertainty sets the source to `needs_review`. It does not modify the current summary.
- Direct project drops otherwise update knowledge automatically.
- The application never changes project status, priority, action items, or other workflow fields because those fields do not exist in the MVP.
- Source records and update history are never silently deleted or rewritten.

This is enduring memory. Chat history is not project knowledge and does not update the summary.

### 4. Project-scoped questions

The user can ask a question from a project page.

For each question, the application:

1. Loads the project's current summary and update history.
2. Searches only that project's source chunks using SQLite FTS5.
3. Sends a bounded evidence package to the approved LLM.
4. Returns an answer with citations for every material factual claim.
5. Allows the user to open the cited excerpt and the preserved original source.

The MVP does not search across projects. It does not require embeddings. Chat messages do not need to be persisted.

### 5. Visible processing and recovery

Each source has one visible processing state:

- `captured`
- `processing`
- `pending_ai`
- `complete`
- `needs_review`
- `unsupported`
- `error`

Capturing a source never depends on the LLM. If the endpoint or key is unavailable:

- The original is preserved.
- The source is hashed and linked to the project.
- Its state becomes `pending_ai`.
- No knowledge update is applied.

Pending sources are retried at application launch and through a manual **Retry pending** control. Each source is processed independently. One failed source must not block later sources or cause already completed sources to run again.

## Explicitly deferred

Do not implement any of the following before all MVP acceptance checks pass:

- Custom portfolio groups
- Project priority or Green/Yellow/Red status
- Portfolio status board or daily portfolio summary
- Multi-project intake and project-assignment recommendations
- Learned routing rules
- ServiceNow-specific importing or column mapping
- Action-item creation, ownership, progress, or closure
- Meeting management, discussion tagging, or due-outs
- Brief generation
- Review-queue screen
- Scheduled background processing
- Project archival
- OCR or scanned-PDF processing
- Embeddings or a vector database
- Knowledge taxonomies, fact promotion, supersession graphs, or contradiction-resolution systems
- Team accounts, roles, or permissions
- Commercial provider SDKs or connectors
- React, TypeScript, Node, or another frontend build toolchain

These are backlog items, not incomplete MVP work.

## Environment decision gate

Before application code is written, perform one short workstation spike:

1. Verify the installed Python version.
2. Verify whether approved `pip` installation is available.
3. Verify SQLite FTS5.
4. Verify access to the configured OneDrive root.
5. Verify the internal LLM endpoint using TLS certificate validation.
6. Determine whether `.msg` parsing will use an approved Python package or locally installed Outlook automation.

Record the result and select one backend:

- Use FastAPI only when its dependencies can be installed from an approved source.
- Otherwise use a small Python standard-library server.

Do not build, maintain, or test two backends. The unselected option is deleted from the implementation plan.

If FTS5 is unavailable, use a bounded `LIKE` search behind the same query function for the MVP.

## Minimum implementation stack

- **Frontend:** vanilla HTML, CSS, and JavaScript served as static files.
- **Backend:** the one Python implementation selected by the environment gate.
- **Database:** local SQLite with foreign keys and WAL mode, outside OneDrive.
- **Files:** locally synced OneDrive project folders.
- **AI:** one small OpenAI-compatible HTTP/JSON adapter for the approved internal endpoint.
- **Configuration:** local configuration file for non-secret values; API key supplied through an environment variable. Do not store keys in SQLite or the repository.

TLS certificate verification is required. If the workstation requires a DoD CA bundle, configure that bundle. Failure to establish a verified connection leaves sources in `pending_ai`; the application must not disable certificate verification as an operational workaround.

The web server binds to loopback only.

## Lean SQLite schema

Only four application tables are required.

### `projects`

- `id`
- `name` — unique
- `folder_path` — unique and constrained beneath the configured OneDrive root
- `current_summary`
- `created_at`
- `updated_at`

### `sources`

- `id`
- `project_id`
- `source_type`
- `native_id` — nullable
- `sha256`
- `original_path`
- `metadata_json`
- `processing_state`
- `error_message` — nullable
- `model_id` — nullable
- `created_at`
- `processed_at` — nullable

An exact duplicate is identified by project plus native ID when available, otherwise project plus SHA-256. Re-dropping it links to the existing source and does not create another update.

### `source_chunks`

- `id`
- `source_id`
- `sequence`
- `text`
- `locator`

An FTS5 virtual table indexes chunk text.

### `project_updates`

- `id`
- `project_id`
- `source_id`
- `text`
- `citations_json`
- `created_at`

No other table may be added before MVP acceptance unless an existing acceptance check cannot be implemented correctly without it. That exception must be documented before the table is created.

## Minimum application interface

The application needs only these user-visible surfaces:

1. Project list with **New project**
2. Project page with drop zone
3. Current summary and cited update history
4. Source list with processing states and **Retry pending**
5. Project-scoped question box with cited answers

The backend needs only the routes required by those surfaces:

- List and create projects
- Read one project with its updates and sources
- Capture a source for a project
- Retry one or all pending sources
- Ask one project a question
- Read a cited excerpt and download its original source

Exact route naming is an implementation choice. Do not add generic administration, settings, audit, job, or provider-status APIs unless an acceptance check consumes them.

Original attachments are returned as downloads with a non-executable content disposition and opaque content type; they are never rendered inline in the application's origin.

## Processing integrity

- File copying and database mutation cannot be one atomic operation. Copy into a temporary file inside the final project folder, hash and verify it, rename it to the final name, and then commit the source record. On failure, surface the error and clean up only the temporary file.
- Process sources independently. There is no portfolio-wide all-or-nothing checkpoint.
- Use parameterized SQL.
- Validate that every file path resolves beneath the configured OneDrive root.
- Bound file size, extraction time, retry count, and evidence-package size.
- Never log source contents, API keys, or complete model prompts.
- Store the model identifier reported for each completed knowledge update.
- Refuse redirects or destinations outside loopback and the explicitly configured approved LLM host.

## Build order

The acceptance checks are the implementation plan:

1. Project creation and OneDrive folder
2. Source capture, preservation, hashing, and deduplication
3. Email extraction and automatic cited knowledge update
4. LLM-unavailable capture and exact-once retry
5. Project-scoped question answering and restart persistence

At the commit that adds a module, the running application must import and exercise it. Nothing may land only for a later phase.

If production code exceeds approximately 3,000 lines before all five checks pass, stop and re-scope rather than raising the limit.

Use fictional sample projects, messages, people, and ticket identifiers in the repository.

## Acceptance checks

The MVP is complete only when all five checks pass end to end.

### 1. Project creation

Create a project. Confirm that:

- One database project exists.
- One matching OneDrive folder exists.
- Restarting the application preserves and reopens the project.

### 2. Direct drop and deduplication

Drop a saved `.msg` or `.eml` onto the project. Confirm that:

- The original email and attachments are preserved.
- Extracted email and supported attachment text are linked to the project.
- One cited knowledge update is applied.
- The current summary is updated.

Drop the same source again. Confirm that no duplicate source, chunk, update, or copied original is created.

### 3. Project-scoped cited knowledge

Drop a second source with new project information. Confirm that:

- The current summary incorporates the supported change.
- The update history shows a distinct dated update.
- Every update citation opens the correct source excerpt and original.
- No unrelated project evidence was used.

### 4. AI-unavailable recovery

Disable the LLM key or endpoint and drop another email. Confirm that:

- The original and attachments are preserved.
- The source becomes `pending_ai`.
- No knowledge update is fabricated.

Restore the endpoint and retry. Confirm that the source completes exactly once and that later sources are not blocked by its earlier failure.

### 5. Project question answering

Restart the application and ask a question whose answer depends on the dropped sources. Confirm that:

- The answer uses only the active project's summary, updates, and chunks.
- Every material factual claim has a valid citation.
- The cited excerpt and original source are accessible.
- The answer and project knowledge remain correct after another restart.

## Completion rule

When these five checks pass, stop. Do not implement deferred features in the same build.

The next feature is chosen only after the working MVP has been used and the observed workflow—not the original wishlist—shows which addition has the highest value.
