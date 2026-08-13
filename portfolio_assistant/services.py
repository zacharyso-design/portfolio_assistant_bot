from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from openpyxl import load_workbook

from .config import Settings
from .db import Database, utc_now
from .extraction import (
    ExtractionFailure,
    TRANSCRIPT_SUFFIXES,
    UnsupportedSource,
    extract_source,
    inspect_native_id,
    normalize_text,
    safe_filename,
    sha256_file,
)
from .llm import LlmAdapter, LlmContractError, LlmUnavailable


PRIORITIES = ("Critical", "High", "Medium", "Low")
STATUSES = ("Green", "Yellow", "Red", "Complete")
ASSIGNEE_TYPES = ("me", "person", "team_office")
ACTION_STATES = ("open", "blocked", "complete")
RECOGNIZED_SNOW_COLUMNS = (
    "Number", "Opened", "Alternate Contact", "Assigned to", "Short description",
    "Priority", "State", "Category", "Subcategory", "Assignment group", "Updated",
    "Created", "Contact", "Configuration item", "Comments and Work notes", "Tags",
)
REQUIRED_SNOW_COLUMNS = (
    "Number", "Short description", "Assignment group", "Updated", "Comments and Work notes",
)
SNOW_ENTRY_HEADER = re.compile(
    r"(?m)^(?P<stamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*-\s*(?P<author>.+?)\s*\((?P<kind>[^)]+)\)\s*$"
)
RULE_TYPES = {"ticket_number", "project_name", "filename_phrase", "sender_subject", "meeting_workstream"}


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


class ValidationError(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except json.JSONDecodeError:
        return fallback


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, (UnsupportedSource, ExtractionFailure, ValidationError, LlmContractError, LlmUnavailable)):
        return str(exc)[:400]
    return "The operation failed. See application diagnostics for the safe error code."


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return text[:60] or "project"


class PortfolioService:
    def __init__(self, settings: Settings, db: Database, llm: LlmAdapter):
        self.settings = settings
        self.db = db
        self.llm = llm

    def _under_root(self, path: Path) -> Path:
        root = self.settings.app.one_drive_root.resolve()
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValidationError("Resolved path is outside the configured OneDrive root") from exc
        return resolved

    def _project(self, connection: sqlite3.Connection, project_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise NotFoundError("Project not found")
        return row

    def create_group(self, name: str, sort_order: int = 0) -> dict[str, Any]:
        clean = normalize_text(name)
        if not clean or len(clean) > 120:
            raise ValidationError("Portfolio group name must be 1-120 characters")
        with self.db.transaction() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO portfolio_groups(name, sort_order, is_system, created_at) VALUES (?, ?, 0, ?)",
                    (clean, int(sort_order), utc_now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("A portfolio group with that name already exists") from exc
            row = connection.execute("SELECT * FROM portfolio_groups WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def list_groups(self) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT g.*, count(p.id) AS project_count
                FROM portfolio_groups g LEFT JOIN projects p ON p.portfolio_group_id = g.id
                GROUP BY g.id ORDER BY g.sort_order, g.name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def update_group(self, group_id: int, *, name: str | None = None, sort_order: int | None = None) -> dict[str, Any]:
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM portfolio_groups WHERE id = ?", (group_id,)).fetchone()
            if not row:
                raise NotFoundError("Portfolio group not found")
            if row["is_system"] and name is not None and normalize_text(name).casefold() != "unassigned":
                raise ValidationError("The system Unassigned group cannot be renamed")
            new_name = normalize_text(name) if name is not None else row["name"]
            new_order = int(sort_order) if sort_order is not None else row["sort_order"]
            try:
                connection.execute(
                    "UPDATE portfolio_groups SET name = ?, sort_order = ? WHERE id = ?",
                    (new_name, new_order, group_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("A portfolio group with that name already exists") from exc
            updated = connection.execute("SELECT * FROM portfolio_groups WHERE id = ?", (group_id,)).fetchone()
        return dict(updated)

    def create_project(
        self,
        name: str,
        *,
        portfolio_group_id: int | None = None,
        snow_number: str | None = None,
        assignment_group: str | None = None,
        snow_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean = normalize_text(name)
        if not clean or len(clean) > 240:
            raise ValidationError("Project name must be 1-240 characters")
        project_id = str(uuid.uuid4())
        folder = self._under_root(
            self.settings.app.one_drive_root / "Projects" / f"{_slug(clean)}--{project_id}"
        )
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "sources").mkdir()
        (folder / "attachments").mkdir()
        now = utc_now()
        try:
            with self.db.transaction() as connection:
                if portfolio_group_id is None:
                    group = connection.execute(
                        "SELECT id FROM portfolio_groups WHERE name = 'Unassigned' COLLATE NOCASE"
                    ).fetchone()
                    portfolio_group_id = int(group["id"])
                elif not connection.execute("SELECT 1 FROM portfolio_groups WHERE id = ?", (portfolio_group_id,)).fetchone():
                    raise ValidationError("Portfolio group not found")
                connection.execute(
                    """
                    INSERT INTO projects(
                      id, name, portfolio_group_id, snow_number, assignment_group,
                      snow_metadata_json, folder_path, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, clean, portfolio_group_id, snow_number, assignment_group,
                     _json(snow_metadata or {}), str(folder), now, now),
                )
        except Exception:
            try:
                (folder / "sources").rmdir()
                (folder / "attachments").rmdir()
                folder.rmdir()
            except OSError:
                pass
            raise
        return self.get_project(project_id)

    def list_projects(
        self,
        *,
        query: str = "",
        portfolio_group_id: int | None = None,
        assignment_group: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 250,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if query.strip():
            pattern = f"%{query.strip()[:200]}%"
            clauses.append(
                "(p.name LIKE ? OR p.snow_number LIKE ? OR p.owner_text LIKE ? OR p.assignment_group LIKE ? OR p.next_action LIKE ? OR p.latest_change LIKE ?)"
            )
            params.extend([pattern] * 6)
        if portfolio_group_id is not None:
            clauses.append("p.portfolio_group_id = ?")
            params.append(portfolio_group_id)
        if assignment_group is not None:
            if assignment_group == "":
                clauses.append("coalesce(p.assignment_group, '') = ''")
            else:
                clauses.append("p.assignment_group = ? COLLATE NOCASE")
                params.append(assignment_group)
        if status:
            if status not in STATUSES:
                raise ValidationError("Invalid project status filter")
            clauses.append("p.status = ?")
            params.append(status)
        if priority:
            if priority not in PRIORITIES:
                raise ValidationError("Invalid project priority filter")
            clauses.append("p.priority = ?")
            params.append(priority)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        limit = max(1, min(limit, 250))
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.*, g.name AS portfolio_group_name
                FROM projects p JOIN portfolio_groups g ON g.id = p.portfolio_group_id
                {where}
                ORDER BY g.sort_order, g.name COLLATE NOCASE,
                  CASE p.priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
                  p.name COLLATE NOCASE LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            total = connection.execute(f"SELECT count(*) FROM projects p {where}", params).fetchone()[0]
            assignments = [row[0] or "" for row in connection.execute(
                "SELECT DISTINCT assignment_group FROM projects ORDER BY assignment_group COLLATE NOCASE"
            ).fetchall()]
            metrics = dict(connection.execute(
                """SELECT count(*) AS total,
                   sum(CASE WHEN status <> 'Complete' THEN 1 ELSE 0 END) AS active,
                   sum(CASE WHEN status = 'Red' THEN 1 ELSE 0 END) AS red
                   FROM projects"""
            ).fetchone())
        return {
            "items": [self._decode_project(dict(row)) for row in rows],
            "total": total,
            "limit": limit,
            "assignment_groups": assignments,
            "metrics": metrics,
        }

    def _decode_project(self, project: dict[str, Any]) -> dict[str, Any]:
        project["snow_metadata"] = _decode(project.pop("snow_metadata_json", "{}"), {})
        project["name_manually_overridden"] = bool(project["name_manually_overridden"])
        return project

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                """SELECT p.*, g.name AS portfolio_group_name
                   FROM projects p JOIN portfolio_groups g ON g.id = p.portfolio_group_id
                   WHERE p.id = ?""",
                (project_id,),
            ).fetchone()
            if not row:
                raise NotFoundError("Project not found")
            updates = connection.execute(
                "SELECT * FROM project_updates WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT 100",
                (project_id,),
            ).fetchall()
            sources = connection.execute(
                """SELECT s.*, parent.original_filename AS parent_original_filename
                   FROM sources s LEFT JOIN sources parent ON parent.id = s.parent_source_id
                   WHERE s.project_id = ? ORDER BY s.parent_source_id IS NOT NULL, s.created_at DESC, s.id DESC LIMIT 200""",
                (project_id,),
            ).fetchall()
            actions = connection.execute(
                "SELECT * FROM action_items WHERE project_id = ? ORDER BY state = 'complete', due_date, id DESC",
                (project_id,),
            ).fetchall()
            decoded_updates = [self._decode_update(item) for item in updates]
            decoded_actions = [self._decode_action(item) for item in actions]
            for update in decoded_updates:
                update["citations"] = self._enrich_citations(connection, update["citations"])
            for action in decoded_actions:
                action["citations"] = self._enrich_citations(connection, action["citations"])
        result = self._decode_project(dict(row))
        result["updates"] = decoded_updates
        result["sources"] = [self._decode_source(item) for item in sources]
        result["action_items"] = decoded_actions
        summary_citations: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        for update in reversed(decoded_updates):
            for citation in update["citations"]:
                pair = (int(citation["source_id"]), int(citation["chunk_id"]))
                if pair not in seen:
                    seen.add(pair)
                    summary_citations.append(citation)
        result["summary_citations"] = summary_citations
        return result

    def _enrich_citations(
        self, connection: sqlite3.Connection, citations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for citation in citations:
            try:
                source_id = int(citation["source_id"])
                chunk_id = int(citation["chunk_id"])
            except (KeyError, TypeError, ValueError):
                continue
            row = connection.execute(
                """SELECT c.text, c.locator, s.original_filename, s.meeting_name, s.meeting_date
                   FROM source_chunks c JOIN sources s ON s.id = c.source_id
                   WHERE c.id = ? AND c.source_id = ?""",
                (chunk_id, source_id),
            ).fetchone()
            if row:
                enriched.append({
                    "source_id": source_id,
                    "chunk_id": chunk_id,
                    "locator": row["locator"],
                    "original_filename": row["original_filename"],
                    "meeting_name": row["meeting_name"],
                    "meeting_date": row["meeting_date"],
                    "excerpt": row["text"][:600],
                })
        return enriched

    def _decode_update(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["citations"] = _decode(item.pop("citations_json", "[]"), [])
        return item

    def _decode_source(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = _decode(item.pop("metadata_json", "{}"), {})
        return item

    def _decode_action(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["citations"] = _decode(item.pop("citations_json", None), [])
        return item

    def update_project(self, project_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "portfolio_group_id", "status", "priority", "owner_text", "next_action", "next_action_due"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValidationError(f"Read-only or unknown project fields: {', '.join(sorted(unknown))}")
        changes: list[tuple[str, Any, Any]] = []
        now = utc_now()
        with self.db.transaction() as connection:
            project = self._project(connection, project_id)
            values: dict[str, Any] = {}
            for key, value in fields.items():
                if key == "name":
                    value = normalize_text(str(value))
                    if not value:
                        raise ValidationError("Project name cannot be blank")
                    values["name_manually_overridden"] = 1
                elif key == "status" and value not in STATUSES:
                    raise ValidationError("Invalid project status")
                elif key == "priority" and value not in PRIORITIES:
                    raise ValidationError("Invalid project priority")
                elif key == "portfolio_group_id":
                    if not connection.execute("SELECT 1 FROM portfolio_groups WHERE id = ?", (value,)).fetchone():
                        raise ValidationError("Portfolio group not found")
                elif key == "next_action_due" and value:
                    try:
                        date.fromisoformat(str(value))
                    except ValueError as exc:
                        raise ValidationError("next_action_due must be an ISO date") from exc
                if project[key] != value:
                    changes.append((key, project[key], value))
                values[key] = value
            if values:
                values["updated_at"] = now
                if "status" in values:
                    values["completed_at"] = now if values["status"] == "Complete" else None
                assignments = ", ".join(f"{key} = ?" for key in values)
                connection.execute(
                    f"UPDATE projects SET {assignments} WHERE id = ?",
                    (*values.values(), project_id),
                )
            for key, old, new in changes:
                if key in {"status", "priority"}:
                    text = f"User changed {key} from {old} to {new}."
                    connection.execute(
                        "INSERT INTO project_updates(project_id, source_id, update_type, text, citations_json, model_id, created_at) VALUES (?, NULL, 'manual_field', ?, '[]', NULL, ?)",
                        (project_id, text, now),
                    )
                    connection.execute("UPDATE projects SET latest_change = ? WHERE id = ?", (text, project_id))
        return self.get_project(project_id)

    def _destination_dir(self, project: sqlite3.Row | None, multi_project: bool) -> Path:
        if multi_project:
            return self._under_root(self.settings.app.one_drive_root / "_PortfolioAssistant" / "intake" / "multi-project")
        if project is None:
            raise ValidationError("A destination project is required")
        now = datetime.now()
        return self._under_root(Path(project["folder_path"]) / "sources" / f"{now:%Y}" / f"{now:%m}")

    def capture_source(
        self,
        stream: BinaryIO,
        filename: str,
        *,
        project_id: str | None,
        meeting_name: str | None = None,
        meeting_date: str | None = None,
        is_transcript: bool = False,
        multi_project: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        clean_name = safe_filename(filename, "source")
        suffix = Path(clean_name).suffix.casefold()
        if (suffix in TRANSCRIPT_SUFFIXES or is_transcript) and (not meeting_name or not meeting_date):
            raise ValidationError("Meeting name and meeting date are required for transcripts")
        if meeting_date:
            try:
                date.fromisoformat(meeting_date)
            except ValueError as exc:
                raise ValidationError("meeting_date must be an ISO date") from exc
        with self.db.connect() as connection:
            project = self._project(connection, project_id) if project_id else None
        if not multi_project and project is None:
            raise ValidationError("A destination project is required")
        if multi_project and project_id is not None:
            raise ValidationError("Multi-project intake cannot have a project destination before review")
        destination = self._destination_dir(project, multi_project)
        destination.mkdir(parents=True, exist_ok=True)
        temp_path = self._under_root(destination / f".{uuid.uuid4().hex}.capture.tmp{suffix}")
        total = 0
        limit = self.settings.app.max_file_mb * 1024 * 1024
        try:
            with temp_path.open("xb") as output:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > limit:
                        raise ValidationError(f"File exceeds the configured {self.settings.app.max_file_mb} MB limit")
                    output.write(block)
            digest = sha256_file(temp_path)
            native_id = inspect_native_id(temp_path)
            with self.db.connect() as connection:
                if multi_project:
                    existing = connection.execute(
                        "SELECT * FROM sources WHERE project_id IS NULL AND sha256 = ?", (digest,)
                    ).fetchone()
                elif native_id:
                    existing = connection.execute(
                        "SELECT * FROM sources WHERE project_id = ? AND native_id = ?", (project_id, native_id)
                    ).fetchone()
                else:
                    existing = connection.execute(
                        "SELECT * FROM sources WHERE project_id = ? AND sha256 = ? AND parent_source_id IS NULL",
                        (project_id, digest),
                    ).fetchone()
            if existing:
                temp_path.unlink(missing_ok=True)
                return self._decode_source(existing), True
            final_path = self._under_root(destination / f"{uuid.uuid4().hex[:12]}-{clean_name}")
            os.replace(temp_path, final_path)
            now = utc_now()
            source_type = suffix.lstrip(".") or "unknown"
            with self.db.transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO sources(
                      project_id, source_type, native_id, sha256, original_filename, original_path,
                      metadata_json, meeting_name, meeting_date, processing_state, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?, 'captured', ?)
                    """,
                    (project_id, source_type, native_id, digest, clean_name, str(final_path),
                     normalize_text(meeting_name or "") or None, meeting_date, now),
                )
                row = connection.execute("SELECT * FROM sources WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._decode_source(row), False
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def capture_note(self, project_id: str, text: str, title: str = "Manual note") -> dict[str, Any]:
        body = text.strip()
        if not body:
            raise ValidationError("Manual note cannot be blank")
        filename = f"{safe_filename(title, 'Manual note')}.txt"
        source, _ = self.capture_source(io.BytesIO(body.encode("utf-8")), filename, project_id=project_id)
        return source

    def recover_interrupted(self) -> int:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """UPDATE sources SET processing_state = 'captured', error_code = 'recovered_after_restart',
                   error_message = NULL WHERE processing_state = 'processing' AND parent_source_id IS NULL"""
            )
            children = [int(row["id"]) for row in connection.execute(
                "SELECT id FROM sources WHERE processing_state = 'processing' AND parent_source_id IS NOT NULL"
            ).fetchall()]
        for child_id in children:
            self._extract_attachment_child(child_id)
        return cursor.rowcount + len(children)

    def process_pending(self, *, manual: bool = False, source_id: int | None = None, limit: int = 20) -> dict[str, int]:
        clauses = ["parent_source_id IS NULL"]
        params: list[Any] = []
        if source_id is not None:
            clauses.append("id = ?")
            params.append(source_id)
            clauses.append("processing_state IN ('captured', 'pending_ai', 'error')")
        else:
            clauses.append("processing_state IN ('captured', 'pending_ai')")
            if not manual:
                clauses.append("retry_count < ?")
                params.append(self.settings.app.automatic_ai_attempts)
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT id, project_id FROM sources WHERE {' AND '.join(clauses)} ORDER BY created_at LIMIT ?",
                (*params, max(1, min(limit, 100))),
            ).fetchall()
        counts = {"processed": 0, "pending_ai": 0, "needs_review": 0, "unsupported": 0, "error": 0}
        for row in rows:
            state = self.process_source(int(row["id"])) if row["project_id"] else self.process_multi_source(int(row["id"]))
            if state == "complete":
                counts["processed"] += 1
            elif state in counts:
                counts[state] += 1
        return counts

    def retry_source(self, source_id: int) -> dict[str, int]:
        with self.db.connect() as connection:
            source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not source:
            raise NotFoundError("Source not found")
        counts = {"processed": 0, "pending_ai": 0, "needs_review": 0, "unsupported": 0, "error": 0}
        if source["parent_source_id"] is None:
            if source["processing_state"] == "needs_review":
                counts["needs_review"] = 1
                return counts
            return self.process_pending(manual=True, source_id=source_id, limit=1)
        if source["processing_state"] != "error":
            return counts
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE sources SET processing_state = 'processing', error_code = NULL, error_message = NULL WHERE id = ?",
                (source_id,),
            )
        self._extract_attachment_child(source_id)
        with self.db.connect() as connection:
            state = connection.execute("SELECT processing_state FROM sources WHERE id = ?", (source_id,)).fetchone()[0]
        if state == "complete":
            counts["processed"] = 1
        elif state in counts:
            counts[state] = 1
        return counts

    def process_source(self, source_id: int) -> str:
        with self.db.transaction() as connection:
            source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            if not source:
                raise NotFoundError("Source not found")
            if source["parent_source_id"] is not None:
                raise ValidationError("Attachment sources are processed with their parent")
            if source["processing_state"] == "complete":
                return "complete"
            if source["processing_state"] == "processing":
                return "processing"
            if source["project_id"] is None:
                raise ValidationError("Use multi-project processing for unassigned intake")
            claimed = connection.execute(
                """UPDATE sources SET processing_state = 'processing', error_code = NULL, error_message = NULL
                   WHERE id = ? AND processing_state <> 'processing'""",
                (source_id,),
            )
            if claimed.rowcount != 1:
                return "processing"
        try:
            self._ensure_extracted(source_id)
            evidence = self._source_evidence(source_id)
            if not evidence:
                raise UnsupportedSource("No supported text could be extracted")
            with self.db.connect() as connection:
                source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
                project = self._project(connection, source["project_id"])
            bounded, dropped = self._bounded_evidence(evidence)
            self._record_evidence_window(source_id, dropped)
            if not bounded:
                raise LlmContractError("No complete evidence chunk fits the configured evidence limit")
            cross_project = self._find_other_project_mentions(project["id"], bounded)
            if cross_project:
                options = [{"project_id": item["id"], "label": item["name"]} for item in cross_project]
                self._create_review(
                    kind="cross_project_evidence", source_id=source_id, project_id=project["id"],
                    question="This direct-project source names another project. How should it be routed?",
                    reason="Direct project intake cannot apply cross-project evidence silently.",
                    evidence=[{
                        "text": item["text"],
                        "citations": [{"source_id": item["source_id"], "chunk_id": item["chunk_id"]}],
                    } for item in bounded],
                    options=options, memory_preview="No routing rule is created from direct-project intake.",
                )
                self._set_source_state(
                    source_id, "needs_review", "cross_project_evidence",
                    ValidationError("Source names another known project"),
                )
                return "needs_review"
            result = self.llm.knowledge_update(project["current_summary"], bounded)
            self._commit_knowledge(source_id, result, bounded)
            return "complete"
        except UnsupportedSource as exc:
            self._set_source_state(source_id, "unsupported", "unsupported_type", exc)
            return "unsupported"
        except LlmUnavailable as exc:
            self._set_source_state(source_id, "pending_ai", "llm_unavailable", exc, increment_retry=True)
            return "pending_ai"
        except (LlmContractError, ValidationError) as exc:
            self._create_review(
                kind="malformed_llm", source_id=source_id, project_id=self._source_project_id(source_id),
                question="How should this source update the project?", reason=_safe_error(exc),
                evidence=[], options=[], memory_preview="No routing rule will be created.",
            )
            self._set_source_state(source_id, "needs_review", "llm_contract", exc)
            return "needs_review"
        except ExtractionFailure as exc:
            self._set_source_state(source_id, "error", "extraction_failed", exc)
            return "error"
        except Exception as exc:
            self._set_source_state(source_id, "error", "processing_failed", exc)
            return "error"

    def _find_other_project_mentions(
        self, selected_project_id: str, evidence: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        combined = "\n".join(str(item["text"]) for item in evidence).casefold()
        with self.db.connect() as connection:
            projects = connection.execute(
                "SELECT id, name, snow_number FROM projects WHERE id <> ?", (selected_project_id,)
            ).fetchall()
        matches = []
        for project in projects:
            name = normalize_text(project["name"]).casefold()
            snow_number = normalize_text(str(project["snow_number"] or "")).casefold()
            name_match = len(name.split()) >= 2 and re.search(
                rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", combined
            )
            snow_match = snow_number and re.search(
                rf"(?<![a-z0-9]){re.escape(snow_number)}(?![a-z0-9])", combined
            )
            if name_match or snow_match:
                matches.append({"id": project["id"], "name": project["name"]})
        return matches

    def _source_project_id(self, source_id: int) -> str | None:
        with self.db.connect() as connection:
            row = connection.execute("SELECT project_id FROM sources WHERE id = ?", (source_id,)).fetchone()
        return row["project_id"] if row else None

    def _set_source_state(
        self, source_id: int, state: str, code: str, error: Exception, *, increment_retry: bool = False
    ) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                """UPDATE sources SET processing_state = ?, error_code = ?, error_message = ?,
                   retry_count = retry_count + ? WHERE id = ?""",
                (state, code, _safe_error(error), int(increment_retry), source_id),
            )

    def _ensure_extracted(self, source_id: int) -> None:
        with self.db.connect() as connection:
            source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            existing = connection.execute(
                "SELECT count(*) FROM source_chunks WHERE source_id = ? OR source_id IN (SELECT id FROM sources WHERE parent_source_id = ?)",
                (source_id, source_id),
            ).fetchone()[0]
        metadata = _decode(source["metadata_json"], {})
        if existing and (metadata.get("_extraction_complete") is True or source["source_type"] in {"snow_comments", "routed_segment"}):
            return
        result = extract_source(
            Path(source["original_path"]),
            max_attachments=self.settings.app.max_attachments,
            max_text_bytes=self.settings.app.max_extracted_text_mb * 1024 * 1024,
        )
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE sources SET native_id = coalesce(native_id, ?), source_type = ?, metadata_json = ? WHERE id = ?",
                (result.native_id, result.source_type, _json({**result.metadata, "_extraction_complete": False}), source_id),
            )
            for sequence, chunk in enumerate(result.chunks):
                connection.execute(
                    "INSERT OR IGNORE INTO source_chunks(source_id, project_id, sequence, text, locator, processing_state) VALUES (?, ?, ?, ?, ?, 'captured')",
                    (source_id, source["project_id"], sequence, chunk["text"], chunk["locator"]),
                )
        if result.attachments and source["project_id"] is not None:
            self._preserve_attachments(source_id, result.attachments)
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE sources SET metadata_json = ? WHERE id = ?",
                (_json({**result.metadata, "_extraction_complete": True}), source_id),
            )

    def _preserve_attachments(self, source_id: int, attachments: Iterable[Any]) -> None:
        with self.db.connect() as connection:
            source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            project = self._project(connection, source["project_id"])
        directory = self._under_root(Path(project["folder_path"]) / "attachments" / str(source_id))
        directory.mkdir(parents=True, exist_ok=True)
        for sequence, attachment in enumerate(attachments):
            native_id = f"attachment:{source_id}:{sequence}"
            with self.db.connect() as connection:
                existing_child = connection.execute(
                    "SELECT id, processing_state FROM sources WHERE project_id = ? AND native_id = ?",
                    (source["project_id"], native_id),
                ).fetchone()
            if existing_child:
                if existing_child["processing_state"] != "complete":
                    self._extract_attachment_child(int(existing_child["id"]))
                continue
            filename = safe_filename(attachment.filename, f"attachment-{sequence + 1}")
            final_path = self._under_root(directory / f"{uuid.uuid4().hex[:10]}-{filename}")
            temp_path = self._under_root(directory / f".{uuid.uuid4().hex}.tmp")
            temp_path.write_bytes(attachment.data)
            digest = sha256_file(temp_path)
            os.replace(temp_path, final_path)
            now = utc_now()
            with self.db.transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO sources(project_id, parent_source_id, source_type, native_id, sha256,
                      original_filename, original_path, metadata_json, processing_state, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?)
                    """,
                    (source["project_id"], source_id, Path(filename).suffix.casefold().lstrip(".") or "attachment",
                     native_id, digest, filename, str(final_path),
                     _json({"content_type": attachment.content_type}), now),
                )
                if cursor.rowcount == 1:
                    child_id = int(cursor.lastrowid)
                else:
                    existing_child = connection.execute(
                        "SELECT id, processing_state FROM sources WHERE project_id = ? AND native_id = ?",
                        (source["project_id"], native_id),
                    ).fetchone()
                    if not existing_child or existing_child["processing_state"] == "complete":
                        continue
                    child_id = int(existing_child["id"])
            self._extract_attachment_child(child_id)

    def _extract_attachment_child(self, child_id: int) -> None:
        with self.db.connect() as connection:
            child = connection.execute("SELECT * FROM sources WHERE id = ?", (child_id,)).fetchone()
            if not child or child["parent_source_id"] is None or child["processing_state"] == "complete":
                return
        try:
            result = extract_source(
                Path(child["original_path"]),
                max_attachments=0,
                max_text_bytes=self.settings.app.max_extracted_text_mb * 1024 * 1024,
            )
            prior_metadata = _decode(child["metadata_json"], {})
            with self.db.transaction() as connection:
                for sequence, chunk in enumerate(result.chunks):
                    connection.execute(
                        """INSERT OR IGNORE INTO source_chunks(
                           source_id, project_id, sequence, text, locator, processing_state
                           ) VALUES (?, ?, ?, ?, ?, 'captured')""",
                        (child_id, child["project_id"], sequence, chunk["text"],
                         f"Attachment {child['original_filename']}: {chunk['locator']}"),
                    )
                connection.execute(
                    """UPDATE sources SET source_type = ?, metadata_json = ?, processing_state = 'complete',
                       processed_at = ?, error_code = NULL, error_message = NULL WHERE id = ?""",
                    (result.source_type, _json({**prior_metadata, **result.metadata}), utc_now(), child_id),
                )
        except UnsupportedSource as exc:
            self._set_source_state(child_id, "unsupported", "unsupported_attachment", exc)
        except ExtractionFailure as exc:
            self._set_source_state(child_id, "error", "attachment_extraction_failed", exc)
        except Exception as exc:
            self._set_source_state(child_id, "error", "attachment_processing_failed", exc)

    def _source_evidence(self, source_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id AS chunk_id, c.source_id, c.text, c.locator, c.comment_at, c.author,
                       s.original_filename, s.meeting_name, s.meeting_date
                FROM source_chunks c JOIN sources s ON s.id = c.source_id
                WHERE c.source_id = ? OR s.parent_source_id = ?
                ORDER BY c.source_id = ? DESC, c.source_id, c.sequence
                """,
                (source_id, source_id, source_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def _bounded_evidence(self, evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        remaining = self.settings.llm.max_evidence_chars
        bounded: list[dict[str, Any]] = []
        dropped = 0
        for item in evidence:
            copy = dict(item)
            text = str(copy["text"])
            if len(text) > remaining:
                dropped += 1
                continue
            copy["text"] = text
            remaining -= len(text)
            bounded.append(copy)
        return bounded, dropped

    def _record_evidence_window(self, source_id: int, dropped_chunks: int) -> None:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """UPDATE sources
                   SET metadata_json = json_set(coalesce(metadata_json, '{}'), '$.evidence_dropped_chunks', ?)
                   WHERE id = ?""",
                (dropped_chunks, source_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("Source not found")

    def _validate_citations(self, citations: Any, allowed: set[tuple[int, int]]) -> list[dict[str, int]]:
        if not isinstance(citations, list) or not citations:
            raise LlmContractError("Every material update requires a citation")
        validated = []
        for citation in citations:
            if not isinstance(citation, dict):
                raise LlmContractError("Citation must be an object")
            try:
                pair = (int(citation["source_id"]), int(citation["chunk_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise LlmContractError("Citation identifiers are invalid") from exc
            if pair not in allowed:
                raise LlmContractError("AI response cited evidence that was not supplied")
            validated.append({"source_id": pair[0], "chunk_id": pair[1]})
        return validated

    def _commit_knowledge(self, source_id: int, result: dict[str, Any], evidence: list[dict[str, Any]]) -> None:
        if not isinstance(result, dict):
            raise LlmContractError("Knowledge update must be a JSON object")
        if result.get("needs_review"):
            raise LlmContractError("The AI response requires user review")
        summary = result.get("updated_summary")
        updates = result.get("updates")
        if not isinstance(summary, str) or not summary.strip() or not isinstance(updates, list) or not updates:
            raise LlmContractError("Knowledge update is missing a summary or cited updates")
        allowed = {(int(item["source_id"]), int(item["chunk_id"])) for item in evidence}
        prepared_updates = []
        for update in updates:
            if not isinstance(update, dict) or not normalize_text(str(update.get("text", ""))):
                raise LlmContractError("Knowledge update text is missing")
            prepared_updates.append((normalize_text(update["text"]), self._validate_citations(update.get("citations"), allowed)))
        operations = result.get("action_item_operations", [])
        if not isinstance(operations, list):
            raise LlmContractError("action_item_operations must be an array")
        recommendations = result.get("project_field_recommendations", [])
        if not isinstance(recommendations, list):
            raise LlmContractError("project_field_recommendations must be an array")
        now = utc_now()
        with self.db.transaction() as connection:
            source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            if not source:
                raise NotFoundError("Source not found")
            if source["processing_state"] == "complete":
                return
            project_id = source["project_id"]
            project = self._project(connection, project_id)
            for text, citations in prepared_updates:
                connection.execute(
                    "INSERT INTO project_updates(project_id, source_id, update_type, text, citations_json, model_id, created_at) VALUES (?, ?, 'knowledge', ?, ?, ?, ?)",
                    (project_id, source_id, text, _json(citations), self.llm.model_id, now),
                )
            grounded_summary = " ".join(
                part for part in (project["current_summary"].strip(), *(text for text, _ in prepared_updates)) if part
            )[-10_000:]
            connection.execute(
                "UPDATE projects SET current_summary = ?, latest_change = ?, updated_at = ? WHERE id = ?",
                (grounded_summary, prepared_updates[-1][0], now, project_id),
            )
            for operation in operations:
                self._apply_ai_action(connection, project_id, source_id, operation, allowed, now)
            for recommendation in recommendations:
                self._insert_review(
                    connection, "project_field_recommendation", source_id, project_id,
                    "Do you want to change a manual project field?",
                    "AI recommendations never change project status or priority automatically.",
                    [recommendation], [], "No routing rule will be created.", now,
                )
            connection.execute(
                "UPDATE source_chunks SET processing_state = 'complete', processed_at = ? WHERE source_id = ? OR source_id IN (SELECT id FROM sources WHERE parent_source_id = ?)",
                (now, source_id, source_id),
            )
            connection.execute(
                """UPDATE sources SET processing_state = 'complete', model_id = ?, processed_at = ?,
                   error_code = NULL, error_message = NULL, retry_count = 0 WHERE id = ?""",
                (self.llm.model_id, now, source_id),
            )
            metadata = _decode(source["metadata_json"], {})
            if source["source_type"] == "snow_comments" and metadata.get("cell_hash"):
                connection.execute(
                    "UPDATE projects SET snow_comments_cell_hash = ? WHERE id = ?",
                    (metadata["cell_hash"], project_id),
                )

    def _apply_ai_action(
        self, connection: sqlite3.Connection, project_id: str, source_id: int,
        operation: dict[str, Any], allowed: set[tuple[int, int]], now: str,
    ) -> None:
        if not isinstance(operation, dict):
            raise LlmContractError("Action operation must be an object")
        kind = operation.get("operation")
        citations = self._validate_citations(operation.get("citations"), allowed)
        if kind == "create":
            description = normalize_text(str(operation.get("description", "")))
            assignee_type = operation.get("assignee_type")
            assignee_value = normalize_text(str(operation.get("assignee_value", "")))
            due = operation.get("due_date")
            try:
                confidence = float(operation.get("confidence", 0))
            except (TypeError, ValueError) as exc:
                raise LlmContractError("Action confidence must be a number") from exc
            complete = description and assignee_type in ASSIGNEE_TYPES and assignee_value and due and confidence >= 0.9
            if due:
                try:
                    date.fromisoformat(str(due))
                except ValueError:
                    complete = False
            if not complete:
                self._insert_review(
                    connection, "inferred_action", source_id, project_id,
                    "Who owns this action and when is it due?",
                    "The source did not provide enough explicit owner/due-date evidence.",
                    [operation], [], "No routing rule will be created.", now,
                )
                return
            existing = connection.execute(
                "SELECT 1 FROM action_items WHERE project_id = ? AND source_id = ? AND description = ?",
                (project_id, source_id, description),
            ).fetchone()
            if not existing:
                connection.execute(
                    """
                    INSERT INTO action_items(project_id, description, assignee_type, assignee_value,
                      due_date, state, source_id, citations_json, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'open', ?, ?, 'ai', ?, ?)
                    """,
                    (project_id, description, assignee_type, assignee_value, due, source_id,
                     _json(citations), now, now),
                )
        elif kind == "progress":
            action_id = operation.get("action_item_id")
            progress = normalize_text(str(operation.get("progress_text", "")))
            row = connection.execute(
                "SELECT * FROM action_items WHERE id = ? AND project_id = ?", (action_id, project_id)
            ).fetchone()
            if not row or not progress:
                self._insert_review(
                    connection, "inferred_action", source_id, project_id,
                    "Which action item should this progress update apply to?",
                    "The referenced action item or progress text is incomplete.",
                    [operation], [], "No routing rule will be created.", now,
                )
                return
            connection.execute(
                "UPDATE action_items SET progress_text = ?, source_id = ?, citations_json = ?, updated_at = ? WHERE id = ?",
                (progress, source_id, _json(citations), now, action_id),
            )
        elif kind == "request_close":
            self._insert_review(
                connection, "action_close", source_id, project_id,
                "Do you approve completing this action item?",
                "AI is never allowed to close an action item without explicit user approval.",
                [operation], [{"action_item_id": operation.get("action_item_id"), "choice": "complete"}],
                "No routing rule will be created.", now,
            )
        else:
            raise LlmContractError("Unsupported action item operation")

    def _insert_review(
        self, connection: sqlite3.Connection, kind: str, source_id: int | None, project_id: str | None,
        question: str, reason: str, evidence: Any, options: Any, memory_preview: str, now: str,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO review_items(kind, source_id, project_id, status, question, reason,
              evidence_json, options_json, memory_preview, created_at)
            VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
            """,
            (kind, source_id, project_id, question, reason, _json(evidence), _json(options), memory_preview, now),
        )
        return int(cursor.lastrowid)

    def _create_review(
        self, *, kind: str, source_id: int | None, project_id: str | None, question: str,
        reason: str, evidence: Any, options: Any, memory_preview: str,
    ) -> int:
        with self.db.transaction() as connection:
            return self._insert_review(
                connection, kind, source_id, project_id, question, reason,
                evidence, options, memory_preview, utc_now(),
            )

    def import_snow(self, stream: BinaryIO, filename: str) -> dict[str, Any]:
        clean_name = safe_filename(filename, "snow-export")
        suffix = Path(clean_name).suffix.casefold()
        if suffix not in {".csv", ".xlsx"}:
            raise ValidationError("SNOW import accepts only CSV or XLSX files")
        directory = self._under_root(
            self.settings.app.one_drive_root / "_PortfolioAssistant" / "imports" / "snow"
        )
        directory.mkdir(parents=True, exist_ok=True)
        temp_path = self._under_root(directory / f".{uuid.uuid4().hex}.tmp{suffix}")
        limit = self.settings.app.max_file_mb * 1024 * 1024
        total = 0
        try:
            with temp_path.open("xb") as output:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > limit:
                        raise ValidationError(f"Import exceeds the configured {self.settings.app.max_file_mb} MB limit")
                    output.write(block)
            digest = sha256_file(temp_path)
            final_path = self._under_root(directory / f"{datetime.now():%Y%m%d-%H%M%S}-{digest[:10]}-{clean_name}")
            if final_path.exists():
                temp_path.unlink()
            else:
                os.replace(temp_path, final_path)
            rows, columns = self._read_snow_rows(final_path)
            missing = [column for column in REQUIRED_SNOW_COLUMNS if column not in columns]
            if missing:
                raise ValidationError(f"Missing required SNOW columns: {', '.join(missing)}")
            result: dict[str, Any] = {
                "tickets_read": len(rows), "new_comments_applied": 0, "tickets_unchanged": 0,
                "review_or_error_count": 0, "pending_ai": 0, "affected_projects": [],
                "review_item_ids": [],
            }
            for row_number, raw in enumerate(rows, start=2):
                try:
                    row_result = self._import_snow_row(raw, row_number, final_path, digest)
                    result["new_comments_applied"] += row_result["new_comments_applied"]
                    result["tickets_unchanged"] += int(row_result["unchanged"])
                    result["pending_ai"] += int(row_result["pending_ai"])
                    if row_result.get("project_id") and row_result["project_id"] not in result["affected_projects"]:
                        result["affected_projects"].append(row_result["project_id"])
                    if row_result.get("review_id"):
                        result["review_or_error_count"] += 1
                        result["review_item_ids"].append(row_result["review_id"])
                except (ValidationError, ValueError) as exc:
                    review_id = self._create_review(
                        kind="snow_invalid_row", source_id=None, project_id=None,
                        question=f"How should SNOW import row {row_number} be handled?",
                        reason=_safe_error(exc), evidence=[{"row": row_number, "values": self._recognized_snow(raw)}],
                        options=[], memory_preview="No routing rule will be created.",
                    )
                    result["review_or_error_count"] += 1
                    result["review_item_ids"].append(review_id)
            return result
        finally:
            temp_path.unlink(missing_ok=True)

    def _read_snow_rows(self, path: Path) -> tuple[list[dict[str, Any]], list[str]]:
        if path.suffix.casefold() == ".csv":
            data = path.read_bytes()
            text = None
            for encoding in ("utf-8-sig", "cp1252"):
                try:
                    text = data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                raise ValidationError("CSV encoding must be UTF-8 or Windows-1252")
            reader = csv.DictReader(io.StringIO(text))
            columns = [str(column).strip() for column in (reader.fieldnames or [])]
            rows = [{str(key).strip(): value for key, value in row.items()} for row in reader]
            return rows, columns
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            iterator = sheet.iter_rows(values_only=True)
            header = next(iterator, None)
            if not header:
                raise ValidationError("XLSX export is empty")
            columns = [str(value).strip() if value is not None else "" for value in header]
            rows = [dict(zip(columns, values)) for values in iterator if any(value is not None for value in values)]
            return rows, columns
        finally:
            workbook.close()

    def _recognized_snow(self, row: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for column in RECOGNIZED_SNOW_COLUMNS:
            value = row.get(column)
            if isinstance(value, (datetime, date)):
                value = value.isoformat()
            result[column] = "" if value is None else str(value)
        return result

    def _parse_snow_datetime(self, value: Any, field: str) -> str:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime.combine(value, time.min)
        else:
            text = normalize_text(str(value or ""))
            if not text:
                raise ValidationError(f"{field} is required")
            parsed = None
            for candidate in (text, text.replace("Z", "+00:00")):
                try:
                    parsed = datetime.fromisoformat(candidate)
                    break
                except ValueError:
                    continue
            if parsed is None:
                for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
                    try:
                        parsed = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        continue
            if parsed is None:
                raise ValidationError(f"{field} is not a recognized date/time")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")

    def _parse_snow_entries(self, cell: str) -> list[dict[str, str]]:
        matches = list(SNOW_ENTRY_HEADER.finditer(cell))
        if not matches or cell[:matches[0].start()].strip():
            raise ValidationError("Comments and Work notes could not be split at deterministic entry boundaries")
        entries = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(cell)
            body = cell[match.end():end].strip()
            if not body:
                raise ValidationError("A SNOW comment entry has no body")
            stamp = datetime.strptime(match.group("stamp"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            entries.append({
                "timestamp": stamp.isoformat(timespec="seconds"),
                "author": normalize_text(match.group("author")),
                "entry_type": normalize_text(match.group("kind")),
                "body": body,
            })
        return entries

    def _entry_hash(self, number: str, entry: dict[str, str]) -> str:
        material = "\x1f".join((
            number.casefold(), entry["timestamp"], entry["author"].casefold(),
            entry["entry_type"].casefold(), normalize_text(entry["body"]),
        ))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _import_snow_row(
        self, raw: dict[str, Any], row_number: int, export_path: Path, export_sha: str
    ) -> dict[str, Any]:
        number = normalize_text(str(raw.get("Number") or ""))
        name = normalize_text(str(raw.get("Short description") or ""))
        assignment = normalize_text(str(raw.get("Assignment group") or ""))
        comments = str(raw.get("Comments and Work notes") or "")
        if not number or not name or not assignment or not comments.strip():
            raise ValidationError("Number, Short description, Assignment group, and Comments and Work notes are required")
        updated_at = self._parse_snow_datetime(raw.get("Updated"), "Updated")
        metadata = self._recognized_snow(raw)
        with self.db.connect() as connection:
            project = connection.execute("SELECT * FROM projects WHERE snow_number = ?", (number,)).fetchone()
        if project is None:
            created = self.create_project(
                name, snow_number=number, assignment_group=assignment, snow_metadata=metadata
            )
            project_id = created["id"]
            with self.db.connect() as connection:
                project = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        else:
            project_id = project["id"]
        cell_hash = hashlib.sha256(comments.encode("utf-8")).hexdigest()
        if project["snow_comments_cell_hash"] == cell_hash:
            return {"project_id": project_id, "new_comments_applied": 0, "unchanged": True, "pending_ai": False}
        if project["snow_updated_at"]:
            stored = datetime.fromisoformat(project["snow_updated_at"])
            incoming = datetime.fromisoformat(updated_at)
            if incoming < stored:
                return {"project_id": project_id, "new_comments_applied": 0, "unchanged": True, "pending_ai": False}
        try:
            entries = self._parse_snow_entries(comments)
        except ValidationError as exc:
            source_id = self._insert_snow_source(
                project_id, number, cell_hash, export_path, export_sha,
                metadata={"cell_hash": cell_hash, "row_number": row_number, "parse_failed": True},
                state="needs_review",
            )
            with self.db.transaction() as connection:
                connection.execute(
                    "INSERT INTO source_chunks(source_id, project_id, sequence, text, locator, processing_state) VALUES (?, ?, 0, ?, 'unparsed cumulative comments', 'needs_review')",
                    (source_id, project_id, comments),
                )
                review_id = self._insert_review(
                    connection, "snow_unparseable_comments", source_id, project_id,
                    "Where are the entry boundaries in this cumulative SNOW comment cell?",
                    str(exc), [{"source_id": source_id, "excerpt": comments[:1200]}], [],
                    "No routing rule will be created.", utc_now(),
                )
            return {"project_id": project_id, "new_comments_applied": 0, "unchanged": False, "pending_ai": False, "review_id": review_id}
        now = utc_now()
        with self.db.transaction() as connection:
            current = self._project(connection, project_id)
            next_name = current["name"] if current["name_manually_overridden"] else name
            connection.execute(
                """
                UPDATE projects SET name = ?, assignment_group = ?, snow_state = ?, snow_priority = ?,
                  snow_updated_at = ?, snow_metadata_json = ?, updated_at = ? WHERE id = ?
                """,
                (next_name, assignment, normalize_text(str(raw.get("State") or "")) or None,
                 normalize_text(str(raw.get("Priority") or "")) or None, updated_at,
                 _json(metadata), now, project_id),
            )
        prepared = []
        with self.db.connect() as connection:
            for entry in entries:
                entry_hash = self._entry_hash(number, entry)
                exists = connection.execute(
                    """SELECT 1 FROM source_chunks c JOIN sources s ON s.id = c.source_id
                       WHERE s.project_id = ? AND c.entry_hash = ? AND c.processing_state = 'complete'""",
                    (project_id, entry_hash),
                ).fetchone()
                if not exists:
                    prepared.append((entry_hash, entry))
        if not prepared:
            with self.db.transaction() as connection:
                connection.execute(
                    "UPDATE projects SET snow_comments_cell_hash = ? WHERE id = ?", (cell_hash, project_id)
                )
            return {"project_id": project_id, "new_comments_applied": 0, "unchanged": True, "pending_ai": False}
        with self.db.connect() as connection:
            existing_source = connection.execute(
                "SELECT id, processing_state FROM sources WHERE project_id = ? AND native_id = ?",
                (project_id, f"snow:{number}:{cell_hash}"),
            ).fetchone()
        if existing_source:
            state = self.process_source(int(existing_source["id"]))
            return {
                "project_id": project_id,
                "new_comments_applied": len(prepared) if state == "complete" else 0,
                "unchanged": state == "complete",
                "pending_ai": state == "pending_ai",
                "review_id": self._latest_source_review(int(existing_source["id"])) if state == "needs_review" else None,
            }
        source_id = self._insert_snow_source(
            project_id, number, cell_hash, export_path, export_sha,
            metadata={"cell_hash": cell_hash, "row_number": row_number}, state="captured",
        )
        prepared.sort(key=lambda pair: pair[1]["timestamp"])
        with self.db.transaction() as connection:
            for sequence, (entry_hash, entry) in enumerate(prepared):
                connection.execute(
                    """
                    INSERT INTO source_chunks(source_id, project_id, sequence, text, locator,
                      entry_hash, comment_at, author, entry_type, processing_state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'captured')
                    """,
                    (source_id, project_id, sequence, entry["body"],
                     f"SNOW {number}, {entry['timestamp']} - {entry['author']} ({entry['entry_type']})",
                     entry_hash, entry["timestamp"], entry["author"], entry["entry_type"]),
                )
        state = self.process_source(source_id)
        return {
            "project_id": project_id,
            "new_comments_applied": len(prepared) if state == "complete" else 0,
            "unchanged": False,
            "pending_ai": state == "pending_ai",
            "review_id": self._latest_source_review(source_id) if state == "needs_review" else None,
        }

    def _insert_snow_source(
        self, project_id: str, number: str, cell_hash: str, export_path: Path, export_sha: str,
        *, metadata: dict[str, Any], state: str,
    ) -> int:
        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM sources WHERE project_id = ? AND native_id = ?",
                (project_id, f"snow:{number}:{cell_hash}"),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cursor = connection.execute(
                """
                INSERT INTO sources(project_id, source_type, native_id, sha256, original_filename,
                  original_path, metadata_json, processing_state, created_at)
                VALUES (?, 'snow_comments', ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, f"snow:{number}:{cell_hash}", export_sha, export_path.name,
                 str(export_path), _json(metadata), state, utc_now()),
            )
        return int(cursor.lastrowid)

    def _latest_source_review(self, source_id: int) -> int | None:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT id FROM review_items WHERE source_id = ? ORDER BY id DESC LIMIT 1", (source_id,)
            ).fetchone()
        return int(row["id"]) if row else None

    def process_multi_source(self, source_id: int) -> str:
        with self.db.transaction() as connection:
            source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            if not source:
                raise NotFoundError("Source not found")
            if source["project_id"] is not None:
                raise ValidationError("This source already belongs to a project")
            if source["processing_state"] == "complete":
                return "complete"
            if source["processing_state"] == "processing":
                return "processing"
            claimed = connection.execute(
                """UPDATE sources SET processing_state = 'processing', error_code = NULL, error_message = NULL
                   WHERE id = ? AND processing_state <> 'processing'""",
                (source_id,),
            )
            if claimed.rowcount != 1:
                return "processing"
        try:
            self._ensure_extracted(source_id)
            evidence = self._source_evidence(source_id)
            if not evidence:
                raise UnsupportedSource("No supported text could be extracted")
            with self.db.connect() as connection:
                source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
                projects = [dict(row) for row in connection.execute(
                    "SELECT id, name, snow_number FROM projects ORDER BY name"
                ).fetchall()]
            deterministic = self._match_routing_rule(source, evidence)
            if deterministic:
                routed_evidence = evidence
                dropped = 0
                segments = [{
                    "text": item["text"][:900], "project_id": deterministic["target_project_id"],
                    "confidence": 1.0, "reason": f"Matched local routing rule #{deterministic['id']}.",
                    "citations": [{"source_id": item["source_id"], "chunk_id": item["chunk_id"]}],
                    "matched_rule_id": deterministic["id"],
                } for item in evidence]
            else:
                routed_evidence, dropped = self._bounded_evidence(evidence)
                self._record_evidence_window(source_id, dropped)
                if not routed_evidence:
                    raise LlmContractError("No complete evidence chunk fits the configured evidence limit")
                routed = self.llm.route(routed_evidence, projects)
                segments = routed.get("segments") if isinstance(routed, dict) else None
                if not isinstance(segments, list) or not segments:
                    raise LlmContractError("Routing response did not contain segments")
            allowed = {(int(item["source_id"]), int(item["chunk_id"])) for item in routed_evidence}
            now = utc_now()
            with self.db.transaction() as connection:
                for segment in segments:
                    if not isinstance(segment, dict) or not normalize_text(str(segment.get("text", ""))):
                        raise LlmContractError("Routing segment is missing text")
                    citations = self._validate_citations(segment.get("citations"), allowed)
                    target = segment.get("project_id")
                    if target and not connection.execute("SELECT 1 FROM projects WHERE id = ?", (target,)).fetchone():
                        target = None
                    filename_phrase = normalize_text(Path(source["original_filename"]).stem).casefold()[:120]
                    suggested_rule = {"rule_type": "filename_phrase", "pattern": filename_phrase, "context": {}}
                    evidence_payload = [{
                        "text": normalize_text(segment["text"]), "citations": citations,
                        "confidence": float(segment.get("confidence", 0)), "reason": str(segment.get("reason", "")),
                        "proposed_project_id": target, "suggested_rule": suggested_rule,
                        "matched_rule_id": segment.get("matched_rule_id"),
                    }]
                    options = [{"project_id": project["id"], "label": project["name"]} for project in projects]
                    self._insert_review(
                        connection, "multi_project_route", source_id, target,
                        "Which project should receive this cited segment?",
                        str(segment.get("reason") or "Multi-project intake always requires confirmation."),
                        evidence_payload, options,
                        f"Remember filename phrase “{filename_phrase}” for the confirmed project.", now,
                    )
                connection.execute(
                    "UPDATE sources SET processing_state = 'needs_review', model_id = ?, processed_at = ? WHERE id = ?",
                    (self.llm.model_id, now, source_id),
                )
            return "needs_review"
        except UnsupportedSource as exc:
            self._set_source_state(source_id, "unsupported", "unsupported_type", exc)
            return "unsupported"
        except LlmUnavailable as exc:
            self._set_source_state(source_id, "pending_ai", "llm_unavailable", exc, increment_retry=True)
            return "pending_ai"
        except (ExtractionFailure, LlmContractError, ValidationError) as exc:
            self._set_source_state(source_id, "error", "multi_project_processing_failed", exc)
            return "error"

    def _match_routing_rule(self, source: sqlite3.Row, evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
        combined = "\n".join(item["text"] for item in evidence).casefold()
        filename = source["original_filename"].casefold()
        metadata = _decode(source["metadata_json"], {})
        with self.db.connect() as connection:
            rules = connection.execute("SELECT * FROM routing_rules WHERE active = 1 ORDER BY id").fetchall()
        for row in rules:
            rule = dict(row)
            pattern = rule["pattern"].casefold()
            context = _decode(rule["context_json"], {})
            matched = False
            if rule["rule_type"] in {"ticket_number", "project_name"}:
                matched = pattern in combined
            elif rule["rule_type"] == "filename_phrase":
                matched = pattern in filename
            elif rule["rule_type"] == "sender_subject":
                sender = str(metadata.get("sender", "")).casefold()
                subject = str(metadata.get("subject", "")).casefold()
                matched = pattern in subject and str(context.get("sender_domain", "")).casefold() in sender
            elif rule["rule_type"] == "meeting_workstream":
                matched = pattern == str(source["meeting_name"] or "").casefold() and str(context.get("workstream", "")).casefold() in combined
            if matched:
                return rule
        return None

    def list_reviews(self, status: str = "open") -> list[dict[str, Any]]:
        if status not in {"open", "resolved", "dismissed", "all"}:
            raise ValidationError("Invalid review status")
        where = "" if status == "all" else "WHERE r.status = ?"
        params: tuple[Any, ...] = () if status == "all" else (status,)
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT r.*, p.name AS project_name, s.original_filename
                FROM review_items r
                LEFT JOIN projects p ON p.id = r.project_id
                LEFT JOIN sources s ON s.id = r.source_id
                {where} ORDER BY r.created_at DESC, r.id DESC
                """,
                params,
            ).fetchall()
            decoded = [self._decode_review(row) for row in rows]
            for review in decoded:
                for evidence in review["evidence"]:
                    if isinstance(evidence, dict) and isinstance(evidence.get("citations"), list):
                        evidence["citations"] = self._enrich_citations(connection, evidence["citations"])
        return decoded

    def _decode_review(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["evidence"] = _decode(item.pop("evidence_json", "[]"), [])
        item["options"] = _decode(item.pop("options_json", "[]"), [])
        item["resolution"] = _decode(item.pop("resolution_json", None), None)
        return item

    def resolve_review(self, review_id: int, resolution: dict[str, Any]) -> dict[str, Any]:
        action = resolution.get("action", "apply")
        if action not in {"apply", "dismiss"}:
            raise ValidationError("Review action must be apply or dismiss")
        now = utc_now()
        with self.db.transaction() as connection:
            review = connection.execute("SELECT * FROM review_items WHERE id = ?", (review_id,)).fetchone()
            if not review:
                raise NotFoundError("Review item not found")
            if review["status"] != "open":
                raise ConflictError("Review item is already resolved")
            if action == "dismiss":
                connection.execute(
                    "UPDATE review_items SET status = 'dismissed', resolution_json = ?, resolved_at = ? WHERE id = ?",
                    (_json(resolution), now, review_id),
                )
            elif review["kind"] in {"multi_project_route", "cross_project_evidence"}:
                self._resolve_routing_review(
                    connection, review, resolution, now,
                    learn_rule=review["kind"] == "multi_project_route",
                )
            elif review["kind"] == "action_close":
                action_id = int(resolution.get("action_item_id") or 0)
                item = connection.execute(
                    "SELECT * FROM action_items WHERE id = ? AND project_id = ?", (action_id, review["project_id"])
                ).fetchone()
                if not item:
                    raise ValidationError("Action item was not found in this project")
                connection.execute(
                    "UPDATE action_items SET state = 'complete', completed_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, action_id),
                )
                connection.execute(
                    "UPDATE review_items SET status = 'resolved', resolution_json = ?, resolved_at = ? WHERE id = ?",
                    (_json(resolution), now, review_id),
                )
            elif review["kind"] == "project_field_recommendation":
                field = resolution.get("field")
                value = resolution.get("value")
                if field not in {"status", "priority"}:
                    raise ValidationError("Only a user-confirmed status or priority recommendation can be applied")
                valid = STATUSES if field == "status" else PRIORITIES
                if value not in valid:
                    raise ValidationError(f"Invalid project {field}")
                project = self._project(connection, review["project_id"])
                connection.execute(
                    f"UPDATE projects SET {field} = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                    (value, now, now if field == "status" and value == "Complete" else project["completed_at"], review["project_id"]),
                )
                text = f"User approved changing {field} from {project[field]} to {value}."
                connection.execute(
                    "INSERT INTO project_updates(project_id, source_id, update_type, text, citations_json, created_at) VALUES (?, ?, 'manual_field', ?, '[]', ?)",
                    (review["project_id"], review["source_id"], text, now),
                )
                connection.execute(
                    "UPDATE review_items SET status = 'resolved', resolution_json = ?, resolved_at = ? WHERE id = ?",
                    (_json(resolution), now, review_id),
                )
            elif review["kind"] == "inferred_action":
                evidence = _decode(review["evidence_json"], [])
                proposed = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
                description = normalize_text(str(resolution.get("description") or proposed.get("description") or ""))
                assignee_type = resolution.get("assignee_type") or proposed.get("assignee_type")
                assignee_value = normalize_text(str(resolution.get("assignee_value") or proposed.get("assignee_value") or ""))
                due = resolution.get("due_date") or proposed.get("due_date")
                if not description or assignee_type not in ASSIGNEE_TYPES or not assignee_value or not due:
                    raise ValidationError("Confirmed action description, assignee, and due date are required")
                try:
                    date.fromisoformat(str(due))
                except ValueError as exc:
                    raise ValidationError("Confirmed action due date must be an ISO date") from exc
                citations = proposed.get("citations") if isinstance(proposed.get("citations"), list) else []
                connection.execute(
                    """INSERT INTO action_items(project_id, description, assignee_type, assignee_value,
                       due_date, state, source_id, citations_json, created_by, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'open', ?, ?, 'user', ?, ?)""",
                    (review["project_id"], description, assignee_type, assignee_value, due,
                     review["source_id"], _json(citations), now, now),
                )
                connection.execute(
                    "UPDATE review_items SET status = 'resolved', resolution_json = ?, resolved_at = ? WHERE id = ?",
                    (_json(resolution), now, review_id),
                )
            else:
                connection.execute(
                    "UPDATE review_items SET status = 'resolved', resolution_json = ?, resolved_at = ? WHERE id = ?",
                    (_json(resolution), now, review_id),
                )
            source_id = review["source_id"]
            if source_id:
                root = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
                if root and (root["project_id"] is None or review["kind"] == "cross_project_evidence"):
                    open_count = connection.execute(
                        "SELECT count(*) FROM review_items WHERE source_id = ? AND status = 'open'", (source_id,)
                    ).fetchone()[0]
                    if open_count == 0:
                        connection.execute(
                            "UPDATE sources SET processing_state = 'complete', processed_at = ? WHERE id = ?",
                            (now, source_id),
                        )
            updated = connection.execute(
                """SELECT r.*, p.name AS project_name, s.original_filename
                   FROM review_items r LEFT JOIN projects p ON p.id = r.project_id
                   LEFT JOIN sources s ON s.id = r.source_id WHERE r.id = ?""",
                (review_id,),
            ).fetchone()
        return self._decode_review(updated)

    def _resolve_routing_review(
        self, connection: sqlite3.Connection, review: sqlite3.Row,
        resolution: dict[str, Any], now: str, *, learn_rule: bool,
    ) -> None:
        target_project_id = str(resolution.get("target_project_id") or "")
        target = self._project(connection, target_project_id)
        evidence = _decode(review["evidence_json"], [])
        if not evidence:
            raise ValidationError("Routing review has no preserved evidence")
        segment = evidence[0]
        citations = segment.get("citations", [])
        source = connection.execute("SELECT * FROM sources WHERE id = ?", (review["source_id"],)).fetchone()
        if not source:
            raise ValidationError("Routing source no longer exists")
        allowed_rows = connection.execute(
            "SELECT id, source_id FROM source_chunks WHERE source_id = ?", (source["id"],)
        ).fetchall()
        allowed = {(int(row["source_id"]), int(row["id"])) for row in allowed_rows}
        valid_citations = self._validate_citations(citations, allowed)
        derived_native = f"routed-review:{review['id']}"
        existing = connection.execute(
            "SELECT id FROM sources WHERE project_id = ? AND native_id = ?",
            (target_project_id, derived_native),
        ).fetchone()
        if existing:
            derived_source_id = int(existing["id"])
        else:
            cursor = connection.execute(
                """
                INSERT INTO sources(project_id, parent_source_id, source_type, native_id, sha256,
                  original_filename, original_path, metadata_json, meeting_name, meeting_date,
                  processing_state, model_id, created_at, processed_at)
                VALUES (?, ?, 'routed_segment', ?, ?, ?, ?, ?, ?, ?, 'complete', ?, ?, ?)
                """,
                (target_project_id, source["id"], derived_native, source["sha256"],
                 source["original_filename"], source["original_path"],
                 _json({"review_id": review["id"]}), source["meeting_name"], source["meeting_date"],
                 source["model_id"], now, now),
            )
            derived_source_id = int(cursor.lastrowid)
            derived_citations = []
            for sequence, citation in enumerate(valid_citations):
                chunk = connection.execute(
                    "SELECT * FROM source_chunks WHERE id = ? AND source_id = ?",
                    (citation["chunk_id"], citation["source_id"]),
                ).fetchone()
                inserted = connection.execute(
                    """
                    INSERT INTO source_chunks(source_id, project_id, sequence, text, locator, processing_state, processed_at)
                    VALUES (?, ?, ?, ?, ?, 'complete', ?)
                    """,
                    (derived_source_id, target_project_id, sequence, chunk["text"], chunk["locator"], now),
                )
                derived_citations.append({"source_id": derived_source_id, "chunk_id": int(inserted.lastrowid)})
            text = normalize_text(str(segment["text"]))
            connection.execute(
                "INSERT INTO project_updates(project_id, source_id, update_type, text, citations_json, model_id, created_at) VALUES (?, ?, 'routed_review', ?, ?, ?, ?)",
                (target_project_id, derived_source_id, text, _json(derived_citations), source["model_id"], now),
            )
            current = target["current_summary"].strip()
            summary = f"{current} {text}".strip()[-10_000:]
            connection.execute(
                "UPDATE projects SET current_summary = ?, latest_change = ?, updated_at = ? WHERE id = ?",
                (summary, text, now, target_project_id),
            )
        stored_resolution = {**resolution, "derived_source_id": derived_source_id}
        if learn_rule:
            rule = resolution.get("rule") or segment.get("suggested_rule") or {}
            rule_type = str(rule.get("rule_type") or "")
            pattern = normalize_text(str(rule.get("pattern") or "")).casefold()
            context = rule.get("context") if isinstance(rule.get("context"), dict) else {}
            self._validate_routing_rule(rule_type, pattern, context, target)
            connection.execute(
                """
                INSERT OR IGNORE INTO routing_rules(rule_type, pattern, context_json, target_project_id,
                  created_from_review_id, active, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (rule_type, pattern, _json(context), target_project_id, review["id"], now),
            )
            stored_resolution["confirmed_rule"] = {
                "rule_type": rule_type, "pattern": pattern, "context": context,
            }
        connection.execute(
            "UPDATE review_items SET project_id = ?, status = 'resolved', resolution_json = ?, resolved_at = ? WHERE id = ?",
            (target_project_id, _json(stored_resolution), now, review["id"]),
        )

    def _validate_routing_rule(
        self, rule_type: str, pattern: str, context: dict[str, Any], target: sqlite3.Row
    ) -> None:
        if rule_type not in RULE_TYPES or not 3 <= len(pattern) <= 120:
            raise ValidationError("A valid narrow routing rule preview must be confirmed")
        if any(character in pattern for character in ("/", "\\", "\x00")):
            raise ValidationError("Routing rule patterns cannot contain paths")
        if rule_type == "ticket_number" and not re.fullmatch(r"[a-z]{2,12}\d{3,20}", pattern):
            raise ValidationError("Ticket-number rules require one exact ticket number")
        if rule_type == "project_name" and pattern != normalize_text(target["name"]).casefold():
            raise ValidationError("Project-name rules require the exact confirmed project name")
        if rule_type == "sender_subject":
            domain = normalize_text(str(context.get("sender_domain", ""))).casefold()
            if not domain or any(character.isspace() for character in domain):
                raise ValidationError("Sender/subject rules require one sender domain and subject phrase")
        if rule_type == "meeting_workstream" and not normalize_text(str(context.get("workstream", ""))):
            raise ValidationError("Meeting rules require a recurring meeting name and named workstream")

    def list_routing_rules(self) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """SELECT r.*, p.name AS target_project_name
                   FROM routing_rules r JOIN projects p ON p.id = r.target_project_id
                   ORDER BY r.created_at DESC"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["active"] = bool(item["active"])
            item["context"] = _decode(item.pop("context_json", "{}"), {})
            result.append(item)
        return result

    def set_routing_rule_active(self, rule_id: int, active: bool) -> dict[str, Any]:
        with self.db.transaction() as connection:
            if not connection.execute("SELECT 1 FROM routing_rules WHERE id = ?", (rule_id,)).fetchone():
                raise NotFoundError("Routing rule not found")
            try:
                connection.execute("UPDATE routing_rules SET active = ? WHERE id = ?", (int(active), rule_id))
            except sqlite3.IntegrityError as exc:
                raise ConflictError("An identical active routing rule already exists") from exc
        return next(item for item in self.list_routing_rules() if item["id"] == rule_id)

    def list_actions(self, project_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            self._project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM action_items WHERE project_id = ? ORDER BY state = 'complete', due_date, id",
                (project_id,),
            ).fetchall()
        return [self._decode_action(row) for row in rows]

    def create_action(self, project_id: str, values: dict[str, Any]) -> dict[str, Any]:
        description = normalize_text(str(values.get("description", "")))
        assignee_type = values.get("assignee_type", "me")
        assignee_value = normalize_text(str(values.get("assignee_value", "me")))
        due = values.get("due_date") or None
        state = values.get("state", "open")
        if not description or assignee_type not in ASSIGNEE_TYPES or not assignee_value or state not in ACTION_STATES:
            raise ValidationError("Action description, assignee, and state are invalid")
        if due:
            date.fromisoformat(str(due))
        now = utc_now()
        with self.db.transaction() as connection:
            self._project(connection, project_id)
            cursor = connection.execute(
                """
                INSERT INTO action_items(project_id, description, assignee_type, assignee_value,
                  due_date, state, progress_text, created_by, created_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'user', ?, ?, ?)
                """,
                (project_id, description, assignee_type, assignee_value, due, state,
                 normalize_text(str(values.get("progress_text", ""))) or None,
                 now, now, now if state == "complete" else None),
            )
            row = connection.execute("SELECT * FROM action_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._decode_action(row)

    def update_action(self, project_id: str, action_id: int, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"description", "assignee_type", "assignee_value", "due_date", "state", "progress_text"}
        if set(values) - allowed:
            raise ValidationError("Unknown action item field")
        if values.get("state") == "complete":
            raise ValidationError("Use the explicit complete action endpoint")
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM action_items WHERE id = ? AND project_id = ?", (action_id, project_id)
            ).fetchone()
            if not row:
                raise NotFoundError("Action item not found")
            merged = dict(row)
            merged.update(values)
            if merged["assignee_type"] not in ASSIGNEE_TYPES or merged["state"] not in {"open", "blocked"}:
                raise ValidationError("Invalid action item assignee or state")
            if merged.get("due_date"):
                date.fromisoformat(str(merged["due_date"]))
            connection.execute(
                """UPDATE action_items SET description = ?, assignee_type = ?, assignee_value = ?,
                   due_date = ?, state = ?, progress_text = ?, updated_at = ?, completed_at = NULL WHERE id = ?""",
                (normalize_text(str(merged["description"])), merged["assignee_type"],
                 normalize_text(str(merged["assignee_value"])), merged.get("due_date"), merged["state"],
                 normalize_text(str(merged.get("progress_text") or "")) or None, utc_now(), action_id),
            )
            updated = connection.execute("SELECT * FROM action_items WHERE id = ?", (action_id,)).fetchone()
        return self._decode_action(updated)

    def complete_action(self, project_id: str, action_id: int, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValidationError("Explicit user confirmation is required")
        now = utc_now()
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM action_items WHERE id = ? AND project_id = ?", (action_id, project_id)
            ).fetchone()
            if not row:
                raise NotFoundError("Action item not found")
            connection.execute(
                "UPDATE action_items SET state = 'complete', completed_at = ?, updated_at = ? WHERE id = ?",
                (now, now, action_id),
            )
            updated = connection.execute("SELECT * FROM action_items WHERE id = ?", (action_id,)).fetchone()
        return self._decode_action(updated)

    def ask_project(self, project_id: str, question: str) -> dict[str, Any]:
        clean = normalize_text(question)
        if not clean:
            raise ValidationError("Question cannot be blank")
        with self.db.connect() as connection:
            project = self._project(connection, project_id)
            recent_updates = [self._decode_update(row) for row in connection.execute(
                "SELECT * FROM project_updates WHERE project_id = ? ORDER BY created_at DESC LIMIT 10",
                (project_id,),
            ).fetchall()]
        chunks = self.db.search_chunks(project_id, clean, limit=12)
        if not chunks:
            return {
                "answer": "", "claims": [],
                "uncertainty": "No matching evidence was found in this project.",
                "model_id": self.llm.model_id, "evidence_dropped_chunks": 0,
            }
        evidence = [{
            "source_id": int(row["source_id"]), "chunk_id": int(row["id"]), "text": row["text"],
            "locator": row["locator"], "original_filename": row["original_filename"],
            "meeting_name": row.get("meeting_name"), "meeting_date": row.get("meeting_date"),
        } for row in chunks]
        bounded, dropped = self._bounded_evidence(evidence)
        if not bounded:
            return {
                "answer": "", "claims": [],
                "uncertainty": "No matching project evidence was found within the configured evidence limit.",
                "model_id": self.llm.model_id, "evidence_dropped_chunks": dropped,
            }
        result = self.llm.chat(
            clean,
            _json({"current_summary": project["current_summary"], "recent_updates": recent_updates}),
            bounded,
        )
        if not isinstance(result, dict) or not isinstance(result.get("answer"), str):
            raise LlmContractError("Chat response is malformed")
        allowed = {(item["source_id"], item["chunk_id"]) for item in bounded}
        claims = result.get("claims", [])
        if not isinstance(claims, list):
            raise LlmContractError("Chat claims must be an array")
        enriched: list[dict[str, Any]] = []
        for claim in claims:
            if not isinstance(claim, dict) or not normalize_text(str(claim.get("text", ""))):
                raise LlmContractError("Every chat claim requires text")
            citations = self._validate_citations(claim.get("citations"), allowed)
            claim_copy = {"text": str(claim.get("text", "")), "citations": []}
            for citation in citations:
                matching = next(item for item in bounded if item["source_id"] == citation["source_id"] and item["chunk_id"] == citation["chunk_id"])
                claim_copy["citations"].append({**citation, **{
                    "locator": matching["locator"], "original_filename": matching["original_filename"],
                    "meeting_name": matching.get("meeting_name"), "meeting_date": matching.get("meeting_date"),
                    "excerpt": matching["text"][:600],
                }})
            enriched.append(claim_copy)
        if result["answer"].strip() and not enriched:
            raise LlmContractError("A substantive chat answer must contain claim citations")
        if not result["answer"].strip() and not normalize_text(str(result.get("uncertainty") or "")):
            raise LlmContractError("Chat must return either a cited answer or an uncertainty statement")
        return {
            "answer": result["answer"], "claims": enriched, "uncertainty": result.get("uncertainty"),
            "model_id": self.llm.model_id, "evidence_dropped_chunks": dropped,
        }

    def get_chunk(self, chunk_id: int) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                """SELECT c.*, s.original_filename, s.meeting_name, s.meeting_date
                   FROM source_chunks c JOIN sources s ON s.id = c.source_id WHERE c.id = ?""",
                (chunk_id,),
            ).fetchone()
            if not row:
                raise NotFoundError("Source excerpt not found")
        return dict(row)

    def get_source_original(self, source_id: int) -> tuple[Path, str]:
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            if not row:
                raise NotFoundError("Source not found")
        path = self._under_root(Path(row["original_path"]))
        if not path.is_file():
            raise NotFoundError("Preserved original is unavailable")
        return path, safe_filename(row["original_filename"], "source")

    def run_daily(self, run_date: date | None = None) -> dict[str, Any]:
        local_now = datetime.now().astimezone()
        target = run_date or local_now.date()
        with self.db.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM daily_runs WHERE run_date = ?", (target.isoformat(),)
            ).fetchone()
        if existing:
            return self._decode_daily(existing)
        local_tz = local_now.tzinfo
        window_start_local = datetime.combine(target - timedelta(days=1), time.min, tzinfo=local_tz)
        window_end_local = datetime.combine(target, time.min, tzinfo=local_tz)
        window_start = window_start_local.astimezone(timezone.utc).isoformat()
        window_end = window_end_local.astimezone(timezone.utc).isoformat()
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT u.id AS update_id, u.project_id, u.text, u.update_type, u.citations_json,
                       u.created_at, p.name AS project_name
                FROM project_updates u JOIN projects p ON p.id = u.project_id
                WHERE u.created_at >= ? AND u.created_at < ? ORDER BY u.created_at, u.id
                """,
                (window_start, window_end),
            ).fetchall()
            review_count = int(connection.execute(
                "SELECT count(*) FROM review_items WHERE status = 'open'"
            ).fetchone()[0])
            action_count = int(connection.execute(
                "SELECT count(*) FROM action_items WHERE updated_at >= ? AND updated_at < ?",
                (window_start, window_end),
            ).fetchone()[0])
            action_rows = connection.execute(
                """SELECT a.id AS action_item_id, a.project_id, a.description, a.state,
                          a.progress_text, a.created_at, a.updated_at, p.name AS project_name
                   FROM action_items a JOIN projects p ON p.id = a.project_id
                   WHERE a.updated_at >= ? AND a.updated_at < ? ORDER BY a.updated_at, a.id""",
                (window_start, window_end),
            ).fetchall()
        evidence = [{
            "kind": "project_update", "update_id": int(row["update_id"]), "project_id": row["project_id"],
            "project_name": row["project_name"], "text": row["text"],
            "update_type": row["update_type"], "citations": _decode(row["citations_json"], []),
        } for row in rows]
        action_evidence = [{
            "kind": "action_item", "action_item_id": int(row["action_item_id"]),
            "project_id": row["project_id"], "project_name": row["project_name"],
            "description": row["description"], "state": row["state"],
            "progress_text": row["progress_text"], "updated_at": row["updated_at"],
        } for row in action_rows]
        combined_evidence = [*evidence, *action_evidence]
        counts = {
            "projects_changed": len({item["project_id"] for item in combined_evidence}),
            "updates": len(evidence), "action_changes": action_count, "open_reviews": review_count,
        }
        result = self.llm.daily(combined_evidence, counts)
        if not isinstance(result, dict) or not isinstance(result.get("summary"), str):
            raise LlmContractError("Daily update response is malformed")
        supplied_ids = {item["update_id"] for item in evidence}
        raw_ids = result.get("update_ids", [])
        if not isinstance(raw_ids, list):
            raise LlmContractError("Daily update citations must be an array")
        try:
            cited_ids = [int(value) for value in raw_ids]
        except (TypeError, ValueError) as exc:
            raise LlmContractError("Daily update citation identifiers are invalid") from exc
        if any(value not in supplied_ids for value in cited_ids):
            raise LlmContractError("Daily update cited an update outside the prior-day window")
        if evidence and not cited_ids:
            raise LlmContractError("Daily project statements require committed update citations")
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO daily_runs(run_date, window_start, window_end, summary_text,
                  project_changes_json, counts_json, model_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_date) DO UPDATE SET
                  window_start=excluded.window_start, window_end=excluded.window_end,
                  summary_text=excluded.summary_text, project_changes_json=excluded.project_changes_json,
                  counts_json=excluded.counts_json, model_id=excluded.model_id, updated_at=excluded.updated_at
                """,
                (target.isoformat(), window_start, window_end, result["summary"],
                 _json({"updates": evidence, "action_changes": action_evidence, "cited_update_ids": cited_ids}), _json(counts),
                 self.llm.model_id, now, now),
            )
            row = connection.execute("SELECT * FROM daily_runs WHERE run_date = ?", (target.isoformat(),)).fetchone()
        return self._decode_daily(row)

    def get_daily(self, run_date: date | None = None) -> dict[str, Any] | None:
        target = (run_date or datetime.now().astimezone().date()).isoformat()
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM daily_runs WHERE run_date = ?", (target,)).fetchone()
        return self._decode_daily(row) if row else None

    def _decode_daily(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["project_changes"] = _decode(item.pop("project_changes_json", "{}"), {})
        item["counts"] = _decode(item.pop("counts_json", "{}"), {})
        return item

    def scheduler_status(self) -> dict[str, Any]:
        task_name = "CHIO Portfolio Assistant Morning Update"
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            installed = result.returncode == 0
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            installed = False
        return {
            "installed": installed,
            "message": "Morning task installed" if installed else "Morning task not installed",
            "run_time": self.settings.app.daily_run_time,
        }

    def configuration_status(self, *, test_llm: bool = False) -> dict[str, Any]:
        status = {
            "database_path": str(self.settings.app.database_path),
            "one_drive_root": str(self.settings.app.one_drive_root),
            "bind": f"{self.settings.app.bind_host}:{self.settings.app.bind_port}",
            "retrieval_mode": self.db.fts_mode,
            "llm_adapter": self.settings.llm.adapter,
            "llm_model": self.llm.model_id,
            "api_key_present": bool(os.environ.get(self.settings.llm.api_key_env)) if self.settings.llm.adapter == "internal" else True,
            "scheduler": self.scheduler_status(),
        }
        if test_llm:
            try:
                status["llm_test"] = self.llm.test_connection()
            except (LlmUnavailable, LlmContractError) as exc:
                status["llm_test"] = {"ok": False, "error": _safe_error(exc)}
        return status
