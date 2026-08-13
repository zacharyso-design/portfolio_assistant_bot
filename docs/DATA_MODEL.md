# Data model

Migration `001_initial.sql` is authoritative. SQLite foreign keys and WAL are enabled on every connection.

- `portfolio_groups`: case-insensitive unique groups; migration creates immutable-name system group `Unassigned`.
- `projects`: one non-null group, manual status/priority/next action, separate read-only SNOW metadata/checkpoint, stable folder path, current summary.
- `sources`: preserved originals and derived attachment/routed-segment relationships; scoped native-ID/SHA uniqueness and explicit processing state.
- `source_chunks`: deterministic text/locator, optional SNOW entry identity/checkpoint fields, plus project scope for retrieval.
- `source_chunks_fts`: FTS5 external-content index with insert/update/delete triggers. Startup records `fts5` or `like-fallback` in `app_settings`.
- `project_updates`: dated cited append-only knowledge/manual/routed updates; triggers reject update/delete.
- `action_items`: project-local commitments. AI may create/progress with citations; completion needs explicit user confirmation.
- `review_items`: reason, evidence, options, memory preview, and durable resolution/dismissal.
- `routing_rules`: narrow local deterministic rules linked to their creating review; only `active` can change and deletion is rejected.
- `daily_runs`: one immutable-on-rerun record per run date with the exact prior-day window, cited update IDs, and counts. A repeated run returns the existing record without calling the LLM or rewriting its window provenance.
- `schema_migrations` / `app_settings`: migration and retrieval-mode bookkeeping.

Originals, source rows, chunks, and updates are never silently rewritten or deleted. Manual status/priority changes are recorded as `manual_field` project updates and deliberately have no source citation because they are direct user mutations rather than derived knowledge.

For `snow_comments` rows, the preserved original is the complete imported CSV/XLSX export shared by every ticket source created from that import. Source metadata and chunk locators identify the ticket and row; the API does not disclose the workstation's absolute preservation path.
