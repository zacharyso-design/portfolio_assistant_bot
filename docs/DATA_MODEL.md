# Data model

Migrations `001_initial.sql` and `002_onedrive_archive.sql` are authoritative. SQLite foreign keys and WAL are enabled on every connection. SQLite is a rebuildable local index; the OneDrive manifests and sidecars are the durable record.

- `portfolio_groups`: case-insensitive unique groups; migration creates immutable-name system group `Unassigned`.
- `projects`: one non-null group, manual status/priority/next action, separate read-only SNOW metadata/checkpoint, stable database/archive IDs and folder path, plus Living Summary revision, generation state, review state, timestamp, and safe error.
- `sources`: preserved originals and derived attachment/routed-segment relationships; scoped native-ID/SHA uniqueness, stable ingestion ID/path, capture/canonical-link metadata, source summary, processing version, and explicit processing state.
- `original_files`: every package-relative original or attachment path with original/stored names, byte size, SHA-256, and attachment flag.
- `citation_records`: durable citation IDs mapped to a source chunk, package-relative original path, locator, excerpt, type, display name, and source date.
- `knowledge_items`: project/source-grounded update text, category/date, citation IDs, independent review state, and optional superseded-item link.
- `summary_versions`: immutable Living Summary revisions with structured cited sections, Markdown export, model, generation state, and independent review state.
- `source_chunks`: deterministic text/locator, optional SNOW entry identity/checkpoint fields, plus project scope for retrieval.
- `source_chunks_fts`: FTS5 external-content index with insert/update/delete triggers. Startup records `fts5` or `like-fallback` in `app_settings`.
- `project_updates`: dated cited append-only knowledge/manual/routed updates; triggers reject update/delete.
- `action_items`: project-local commitments. AI may create/progress with citations; completion needs explicit user confirmation.
- `review_items`: reason, evidence, options, memory preview, and durable resolution/dismissal.
- `routing_rules`: narrow local deterministic rules linked to their creating review; only `active` can change and deletion is rejected.
- `daily_runs`: one immutable-on-rerun record per run date with the exact prior-day window, cited update IDs, and counts. A repeated run returns the existing record without calling the LLM or rewriting its window provenance.
- `schema_migrations` / `app_settings`: migration and retrieval-mode bookkeeping.

Package originals are never rewritten or deleted. Replaceable extracted files and sidecars may be regenerated only after their recorded original hashes pass. Manual status/priority changes are recorded as `manual_field` project updates and deliberately have no source citation because they are direct user mutations rather than derived knowledge.

`Assistant/index.json` is a durable archive sidecar, not just a transient index. For email sources it can contain sender, recipient, and CC display names and addresses, plus inferred organization domains. Treat the OneDrive archive as containing PII and protect its sharing, retention, and access accordingly.

For `snow_comments` rows, the preserved original is the complete imported CSV/XLSX export shared by every ticket source created from that import. Source metadata and chunk locators identify the ticket and row; the API does not disclose the workstation's absolute preservation path.
