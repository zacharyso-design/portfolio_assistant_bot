# Portfolio Assistant — Minimum Build Requirements

> **Status: historical background only.** The user-supplied **Codex Build Handoff — CHIO Portfolio Assistant, Revision 1 (2026-08-12)** is the controlling build directive. It narrows and supersedes this older wishlist where they conflict; unapproved items here are not implementation requirements.

## Product boundary

Single-user web application running entirely on the user's government-furnished Windows computer. Files, database records, retrieval indexes, prompts, and model responses stay inside the government environment. The only AI connection is an approved internal DoD/DHA API endpoint.

The MVP includes:

- A status board for 100+ projects, organized into custom portfolio groups.
- Manual project priority: `Critical`, `High`, `Medium`, `Low`.
- Manual project status: `Green`, `Yellow`, `Red`, `Complete`. The AI cannot change priority or status.
- Direct drag-and-drop of `.msg`, `.eml`, `.pdf`, `.docx`, `.txt`, `.xlsx`, and `.csv` onto an individual project; supported updates are applied automatically.
- Multi-project transcript/file intake. The user supplies meeting name and date; the AI recommends project assignments and changes for review.
- ServiceNow import from `.xlsx` or `.csv` with reusable column mapping. Support the supplied export's `Number`, `Priority`, `State`, `Updated`, work notes, and tags.
- Project actions with owner type (`Me`, named person, or team/office), due date, progress, source, and history. AI may create actions and update progress; closure, deletion, or material reassignment requires confirmation.
- Project-scoped chat with citations that open the exact source excerpt and original file.
- Meeting discussion, decisions, and due-outs linked to the meeting, project, and transcript.
- An uncertain-item review queue. Corrected project assignments automatically update transparent, editable routing rules.
- A morning incremental update from the last successful processing checkpoint.
- Archived completed projects that retain searchable history.

Deferred: team accounts/permissions, portfolio-wide chat, live ServiceNow sync, commercial SaaS connectors, required embeddings, mobile apps, and autonomous changes to priority/status.

## Minimum stack

- **Frontend:** React + TypeScript, built as static assets.
- **Backend:** Python 3.12 + FastAPI, serving both API and frontend.
- **Database/search:** local SQLite using WAL mode and FTS5.
- **Files:** locally synced government OneDrive project folders through the Windows filesystem.
- **Scheduler:** Windows Task Scheduler runs the morning incremental-processing command.
- **LLM:** a provider-neutral HTTP adapter for the approved internal endpoint. Base URL, model, authentication, timeout, and context size are configuration.

Node is needed only to build the frontend. Production requires Python and the application files, or a packaged executable if permitted.

## Project folders and source preservation

Creating a project creates a matching folder under a configured OneDrive root. Store original project files and extracted email attachments there. After successful email processing, retain message ID, thread ID when present, sender/recipients, timestamp, subject, body, attachment relationships, source hash, and processing lineage in SQLite.

Compute SHA-256 before processing. Exact matches link to the existing source. Never auto-delete a near duplicate. Changed files create a version relationship to the prior source.

## Lean SQLite schema

| Table | Minimum purpose |
| --- | --- |
| `portfolio_groups` | ID, unique name, sort order |
| `projects` | Group, unique name, OneDrive path, priority, status, summary, timestamps/archive time |
| `sources` | Type, native ID, SHA-256, file path, parent/version, meeting, processing state, metadata |
| `project_sources` | Source/project assignment, method, confidence, review state |
| `source_chunks` | Project-filtered searchable text with source and page/section locator |
| `project_updates` | Dated, sourced update with confidence and supersession |
| `memory_facts` | Sourced decision, rationale, commitment, preference/rule, unresolved question, or stable fact |
| `action_items` | Project, owner type/name, due date, state, source, timestamps |
| `action_item_events` | Immutable progress, reassignment, due-date, and closure history |
| `meetings` / `meeting_items` | Meeting identity plus project-linked discussion, decision, due-out, unresolved question |
| `review_items` | Proposed mutation, reason, confidence, state, user resolution |
| `routing_rules` | Learned pattern, target project, example, enabled flag, use/success count |
| `ingestion_runs` | Run type, start/end, last-success checkpoint, counts, errors |
| `chat_messages` | Project-scoped history; never authoritative project data |

FTS5 indexes `source_chunks.text` and `memory_facts.normalized_fact`. Enable foreign keys. Index all `project_id` columns, source SHA/native ID, action state/due date, project group/status/priority, source chunks by project/source, and open reviews by state/date.

## Retrieval and chat

1. Require one active project for each chat request.
2. Load structured project state: status, priority, summary, open actions, decisions, commitments, and unresolved questions.
3. Run project-filtered FTS5 over memory facts and source chunks.
4. Send a bounded evidence package to the approved LLM endpoint.
5. Require supported facts, synthesis, and missing information to be distinguished.
6. Return a source and locator for every material claim; a citation opens the excerpt and OneDrive original.

Embeddings remain an optional later adapter; the MVP must work without them.

## Ingestion controls

- Idempotent processing: repeating a source or checkpoint cannot duplicate updates, facts, actions, due-outs, or reviews.
- One transaction per source; failed extraction leaves prior project state unchanged and creates a visible error.
- Direct project drop applies supported changes automatically; low-confidence or destructive changes go to review.
- Multi-project sources require review of assignments and proposed changes before application.
- Each corrected assignment updates an inspectable routing rule with its example; rules can be disabled or deleted.
- Every derived record retains source, locator, run, model/version, and timestamp.
- The morning checkpoint advances only after the complete run commits successfully.

## Minimum API

- `GET/POST/PATCH /api/projects`; `POST /api/projects/{id}/sources`
- `GET/PATCH /api/projects/{id}/actions`; explicit `POST .../{action_id}/close`
- `GET /api/projects/{id}/updates`; `POST /api/projects/{id}/chat`
- `POST /api/intake/multi-project`; `POST /api/reviews/{id}/resolve`
- `POST /api/import/servicenow`
- `GET/POST /api/meetings`; `GET /api/meetings/{id}/items`
- `POST /api/jobs/morning-update`; `GET /api/jobs/{id}`
- `GET/PATCH /api/settings`; `GET/PATCH /api/routing-rules`

Mutations write an audit event. Paths must remain under the configured OneDrive root. SQL is parameterized. The LLM adapter receives only the active project's evidence package.

## Acceptance checks

1. Create a project and OneDrive folder in exactly one portfolio group.
2. Drop the same `.msg` twice and produce one source, one update set, and a duplicate link.
3. Drop a project file and create a cited update and action.
4. Upload/name/date a multi-project transcript, correct one assignment, and see the routing rule affect the next similar intake.
5. Import CSV and Excel ServiceNow exports and update only changed tickets.
6. Ask one project a question and receive citations; verify no other project's evidence was used.
7. Allow automatic progress updates but require confirmation before action closure.
8. Run the morning job twice from the same checkpoint with no duplicate output.
9. Archive a completed project and retrieve its cited history.
10. Restart the computer/application and retain structured memory, review history, and search.
