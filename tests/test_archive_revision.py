from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
import pytest

from portfolio_assistant.db import Database
from portfolio_assistant.llm import FakeLlmAdapter, LlmUnavailable
from portfolio_assistant.services import PortfolioService, UnexpectedSummaryError


def _process(client, source_id: int):
    return client.post(f"/api/sources/{source_id}/retry").json()


def _eml() -> bytes:
    message = EmailMessage()
    message["Subject"] = "Archive readiness"
    message["From"] = "lead@example.test"
    message["To"] = "team@example.test"
    message["Date"] = "Thu, 13 Aug 2026 09:00:00 -0400"
    message["Message-ID"] = "<archive-readiness@example.test>"
    message.set_content("Leadership approved the deployment milestone for 17 November.")
    message.add_attachment(b"attachment bytes", maintype="application", subtype="octet-stream", filename="evidence.bin")
    message.add_attachment(b"second attachment bytes", maintype="application", subtype="octet-stream", filename="evidence.bin")
    return message.as_bytes()


def _xlsx() -> bytes:
    stream = BytesIO()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Milestones"
    worksheet.append(["Ticket", "Decision"])
    worksheet.append(["REQ-1842", "Deployment approved for 17 November"])
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def test_project_and_multi_file_packages_are_durable(client, project):
    project_folder = Path(project["folder_path"])
    descriptor = json.loads((project_folder / "_Assistant" / "project.json").read_text(encoding="utf-8"))
    assert descriptor["project_id"] == project["id"]
    assert descriptor["archive_id"].startswith("P-")

    response = client.post(
        f"/api/projects/{project['id']}/sources",
        files=[
            ("files", ("a.txt", b"First archived file", "text/plain")),
            ("files", ("b.txt", b"Second archived file", "text/plain")),
        ],
        data={"relative_paths": json.dumps(["Uploaded Folder/a.txt", "Uploaded Folder/sub/b.txt"])},
    )
    assert response.status_code == 202, response.text
    source = response.json()["source"]
    package = Path(source["ingestion_path"])
    assert (package / "Original" / "Uploaded Folder" / "a.txt").read_bytes() == b"First archived file"
    assert (package / "Original" / "Uploaded Folder" / "sub" / "b.txt").read_bytes() == b"Second archived file"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["original_files"]) == 2
    assert all(len(item["sha256"]) == 64 for item in manifest["original_files"])

    collision = client.post(
        f"/api/projects/{project['id']}/sources",
        files=[
            ("files", ("a.txt", b"collision one", "text/plain")),
            ("files", ("a.txt", b"collision two", "text/plain")),
            ("files", ("a-2.txt", b"collision three", "text/plain")),
        ],
        data={"relative_paths": json.dumps(["a.txt", "a.txt", "a-2.txt"])},
    )
    assert collision.status_code == 202, collision.text
    collision_manifest = json.loads(
        (Path(collision.json()["source"]["ingestion_path"]) / "manifest.json").read_text(encoding="utf-8")
    )
    assert len({item["relative_path"].casefold() for item in collision_manifest["original_files"]}) == 3

    malformed_paths = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("invalid-paths.md", b"Malformed metadata is rejected.", "text/markdown")},
        data={"relative_paths": "true"},
    )
    assert malformed_paths.status_code == 422
    malformed_intake_paths = client.post(
        "/api/intake/multi-project",
        files={"file": ("invalid-intake-paths.md", b"Malformed intake metadata.", "text/markdown")},
        data={"relative_paths": "{}"},
    )
    assert malformed_intake_paths.status_code == 422

    transcript_without_metadata = client.post(
        f"/api/projects/{project['id']}/sources",
        files=[
            ("files", ("agenda.md", b"Agenda", "text/markdown")),
            ("files", ("meeting.vtt", b"WEBVTT\n\n00:00.000 --> 00:01.000\nDecision", "text/vtt")),
        ],
    )
    assert transcript_without_metadata.status_code == 422
    transcript_with_metadata = client.post(
        f"/api/projects/{project['id']}/sources",
        files=[
            ("files", ("agenda.md", b"Agenda with metadata", "text/markdown")),
            ("files", ("meeting.vtt", b"WEBVTT\n\n00:00.000 --> 00:01.000\nApproved", "text/vtt")),
        ],
        data={"meeting_name": "Archive Review", "meeting_date": "2026-08-13"},
    )
    assert transcript_with_metadata.status_code == 202, transcript_with_metadata.text
    transcript_source = transcript_with_metadata.json()["source"]
    assert transcript_source["source_type"] == "meeting-transcript"
    transcript_manifest = json.loads(
        (Path(transcript_source["ingestion_path"]) / "manifest.json").read_text(encoding="utf-8")
    )
    assert transcript_manifest["source_type"] == "meeting-transcript"
    assert transcript_manifest["source_date"] == "2026-08-13"


def test_email_package_preserves_container_attachment_and_derivatives(client, project):
    eml_bytes = _eml()
    captured = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("archive.eml", eml_bytes, "message/rfc822")},
    ).json()["source"]
    assert _process(client, captured["id"])["processed"] == 1
    detail = client.get(f"/api/sources/{captured['id']}").json()
    package = Path(detail["ingestion_path"])
    assert (package / "Original" / "archive.eml").read_bytes() == eml_bytes
    attachments = [item for item in detail["original_files"] if item["is_attachment"]]
    assert len(attachments) == 2
    assert {(package / item["relative_path"]).read_bytes() for item in attachments} == {
        b"attachment bytes", b"second attachment bytes",
    }
    assert (package / "Assistant" / "source-summary.md").is_file()
    assert list((package / "Assistant" / "Extracted").glob("*.txt"))
    index = json.loads((package / "Assistant" / "index.json").read_text(encoding="utf-8"))
    assert "lead@example.test" in index["people"]
    assert "example.test" in index["organizations"]
    assert json.loads((package / "Assistant" / "knowledge-items.json").read_text(encoding="utf-8"))
    assert json.loads((package / "Assistant" / "citations.json").read_text(encoding="utf-8"))
    citation_id = client.get(f"/api/projects/{project['id']}/knowledge").json()[0]["citations"][0]["citation_id"]
    assert client.get(f"/api/citations/{citation_id}/original").content == eml_bytes
    (package / "Original" / "archive.eml").write_bytes(b"corrupted after ingestion")
    assert client.get(f"/api/citations/{citation_id}/original").status_code == 409


def test_spreadsheet_pasted_transcript_and_safe_derived_refresh(client, project):
    workbook_bytes = _xlsx()
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("tracker.xlsx", workbook_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()["source"]
    assert _process(client, source["id"])["processed"] == 1
    detail = client.get(f"/api/sources/{source['id']}").json()
    original = Path(detail["ingestion_path"]) / detail["original_files"][0]["relative_path"]
    assert original.read_bytes() == workbook_bytes
    assert client.get(f"/api/search?q=REQ-1842&project_id={project['id']}").json()
    refreshed = client.post(f"/api/sources/{source['id']}/refresh-derived")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["files_refreshed"] == 1
    assert original.read_bytes() == workbook_bytes

    pasted_text = "Speaker One: The portfolio decision applies to this project exactly as pasted."
    pasted = client.post(f"/api/projects/{project['id']}/notes", json={
        "title": "Pasted portfolio review", "text": pasted_text, "is_transcript": True,
        "meeting_name": "Portfolio Review", "meeting_date": "2026-08-13",
    })
    assert pasted.status_code == 202, pasted.text
    pasted_detail = client.get(f"/api/sources/{pasted.json()['id']}").json()
    pasted_original = Path(pasted_detail["ingestion_path"]) / pasted_detail["original_files"][0]["relative_path"]
    assert pasted_detail["capture_method"] == "pasted_text"
    assert pasted_original.name == "transcript-as-submitted.txt"
    assert pasted_original.read_text(encoding="utf-8") == pasted_text


def test_knowledge_and_living_summary_review_are_independent(client, project):
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("decision.txt", b"Leadership approved the revised deployment date.", "text/plain")},
    ).json()["source"]
    _process(client, source["id"])
    detail = client.get(f"/api/projects/{project['id']}").json()
    item = detail["knowledge_history"][0]
    summary = detail["living_summary"]
    assert summary["generation_state"] == "current"
    assert item["id"] in summary["current"]["content"]["sections"][0]["knowledge_item_ids"]
    search_results = client.get(
        f"/api/search?q=revised%20deployment%20date&project_id={project['id']}"
    ).json()
    assert any(result["result_type"] == "living_summary_claim" for result in search_results)

    client.patch(f"/api/projects/{project['id']}/living-summary/review", json={"status": "approved"})
    assert client.get(f"/api/projects/{project['id']}/knowledge").json()[0]["review_status"] == "unreviewed"
    client.patch(f"/api/projects/{project['id']}/knowledge/{item['id']}", json={"status": "approved"})
    refreshed = client.get(f"/api/projects/{project['id']}").json()
    assert refreshed["knowledge_history"][0]["review_status"] == "approved"
    assert refreshed["living_summary"]["review_status"] == "unreviewed"
    assert len(refreshed["living_summary"]["versions"]) >= 2
    version = refreshed["living_summary"]["versions"][-1]
    version_detail = client.get(
        f"/api/projects/{project['id']}/living-summary/versions/{version['revision']}"
    )
    assert version_detail.status_code == 200
    assert version_detail.json()["content"]["sections"]
    current_path = Path(refreshed["folder_path"]) / "_Assistant" / "living-summary" / "current.json"
    current_path.write_text("{truncated", encoding="utf-8")
    recovered = client.patch(
        f"/api/projects/{project['id']}/living-summary/review", json={"status": "flagged"}
    )
    assert recovered.status_code == 200
    assert json.loads(current_path.read_text(encoding="utf-8"))["sections"]


def test_failed_summary_keeps_knowledge_and_retry_succeeds(client, project, service, monkeypatch):
    monkeypatch.setattr(
        service.llm, "living_summary",
        lambda project_data, knowledge: (_ for _ in ()).throw(LlmUnavailable("Fictional summary outage")),
    )
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("outage.txt", b"A cited update survives summary failure.", "text/plain")},
    ).json()["source"]
    assert _process(client, source["id"])["processed"] == 1
    failed = client.get(f"/api/projects/{project['id']}").json()
    assert failed["knowledge_history"]
    assert failed["living_summary"]["generation_state"] == "failed"
    monkeypatch.setattr(service.llm, "living_summary", FakeLlmAdapter().living_summary)
    retried = client.post(f"/api/projects/{project['id']}/living-summary/regenerate").json()
    assert retried["generation_state"] == "current"


def test_unexpected_summary_failure_marks_failed_and_propagates(project, service, monkeypatch):
    monkeypatch.setattr(
        service.llm, "living_summary",
        lambda project_data, knowledge: (_ for _ in ()).throw(RuntimeError("fictional programming error")),
    )
    with pytest.raises(UnexpectedSummaryError) as raised:
        service.regenerate_living_summary(project["id"])
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert str(raised.value.__cause__) == "fictional programming error"
    summary = service.get_living_summary(project["id"])
    assert summary["generation_state"] == "failed"
    assert summary["error"] == "The operation failed. See application diagnostics for the safe error code."

    source, _ = service.capture_source(
        BytesIO(b"Knowledge commits even when unexpected summary generation fails."),
        "summary-programming-error.txt", project_id=project["id"],
    )
    assert service.process_source(source["id"]) == "complete"
    assert service.source_detail(source["id"])["processing_state"] == "complete"
    assert service.list_knowledge(project["id"])
    assert service.get_living_summary(project["id"])["generation_state"] == "failed"


def test_routed_source_is_canonical_once_and_linked_to_project(client, settings):
    first = client.post("/api/projects", json={"name": "Fictional Archive Alpha"}).json()
    second = client.post("/api/projects", json={"name": "Fictional Archive Beta"}).json()
    source = client.post(
        "/api/intake/multi-project",
        files={"file": ("portfolio.txt", b"Fictional Archive Alpha approved the plan.", "text/plain")},
        data={"meeting_name": "Portfolio Review", "meeting_date": "2026-08-13", "is_transcript": "true"},
    ).json()["source"]
    _process(client, source["id"])
    review = next(item for item in client.get("/api/reviews?status=open").json() if item["source_id"] == source["id"])
    resolved = client.post(f"/api/reviews/{review['id']}/resolve", json={
        "action": "apply", "target_project_id": second["id"], "rule": review["evidence"][0]["suggested_rule"],
    })
    assert resolved.status_code == 200, resolved.text
    canonical = Path(source["ingestion_path"])
    linked_sources = [item for item in client.get(f"/api/projects/{second['id']}").json()["sources"] if item["source_type"] == "routed_segment"]
    assert len(linked_sources) == 1
    linked = Path(linked_sources[0]["ingestion_path"])
    assert canonical != linked
    assert (canonical / "Original" / "portfolio.txt").is_file()
    assert not (linked / "Original").exists()
    manifest = json.loads((linked / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["canonical_source"] is False
    assert manifest["linked_ingestion_id"] == source["ingestion_id"]
    assert client.get(f"/api/projects/{first['id']}").json()["knowledge_history"] == []
    rebuilt_settings = replace(
        settings, app=replace(settings.app, database_path=settings.app.database_path.with_name("routed-rebuilt.db"))
    )
    rebuilt_db = Database(rebuilt_settings.app.database_path)
    rebuilt_db.migrate()
    rebuilt = PortfolioService(rebuilt_settings, rebuilt_db, FakeLlmAdapter())
    assert rebuilt.rebuild_index()["errors"] == 0
    rebuilt_link = next(
        item for item in rebuilt.get_project(second["id"])["sources"]
        if item["source_type"] == "linked-source"
    )
    rebuilt_knowledge = rebuilt.list_knowledge(second["id"])
    assert rebuilt_knowledge
    cited_path, _ = rebuilt.get_citation_original(rebuilt_knowledge[0]["citations"][0]["citation_id"])
    assert cited_path.read_bytes() == b"Fictional Archive Alpha approved the plan."
    assert rebuilt_link["linked_ingestion_id"] == source["ingestion_id"]


def test_fresh_database_rebuilds_from_onedrive_archive(client, project, settings):
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("rebuild.txt", b"Rebuildable source-grounded archive knowledge.", "text/plain")},
    ).json()["source"]
    _process(client, source["id"])
    rebuilt_settings = replace(settings, app=replace(settings.app, database_path=settings.app.database_path.with_name("rebuilt.db")))
    rebuilt_db = Database(rebuilt_settings.app.database_path)
    rebuilt_db.migrate()
    rebuilt = PortfolioService(rebuilt_settings, rebuilt_db, FakeLlmAdapter())
    counts = rebuilt.rebuild_index()
    assert counts["projects"] >= 1
    detail = rebuilt.get_project(project["id"])
    assert detail["knowledge_history"]
    assert detail["sources"]
    assert rebuilt.search_archive("Rebuildable", project_id=project["id"])
    second_counts = rebuilt.rebuild_index()
    assert second_counts["errors"] == 0
    assert len(rebuilt.get_project(project["id"])["knowledge_history"]) == len(detail["knowledge_history"])
    orphan = settings.app.one_drive_root / "CHIO Portfolio Assistant" / "Projects" / "Orphan" / "package"
    (orphan / "Assistant").mkdir(parents=True)
    (orphan / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "ingestion_id": "I-ORPHAN01", "project_id": "P-UNKNOWN",
        "source_type": "txt", "title": "Orphan", "created_at": "2026-08-13T00:00:00+00:00",
        "canonical_source": True, "original_files": [], "processing_status": "complete",
    }), encoding="utf-8")
    assert rebuilt.rebuild_index()["errors"] >= 1


def test_rebuild_keeps_distinct_citations_with_a_shared_excerpt_prefix(client, project, settings):
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("shared-prefix.md", b"Original evidence container.", "text/markdown")},
    ).json()["source"]
    _process(client, source["id"])
    package = Path(source["ingestion_path"])
    shared = ("The same long evidentiary prefix applies to both independently cited claims. " * 3)[:160]
    first_excerpt = f"{shared} First distinct cited conclusion."
    second_excerpt = f"{shared} Second distinct cited conclusion."
    extracted = next((package / "Assistant" / "Extracted").glob("*.txt"))
    extracted.write_text(f"{first_excerpt}\n{second_excerpt}\n", encoding="utf-8")
    (package / "Assistant" / "citations.json").write_text(json.dumps([
        {
            "citation_id": "C-SHARED-PREFIX-1", "original_relative_path": "Original/shared-prefix.md",
            "display_name": "shared-prefix.md", "source_type": "md", "locator": "claim one",
            "excerpt": first_excerpt, "source_date": "2026-08-13",
        },
        {
            "citation_id": "C-SHARED-PREFIX-2", "original_relative_path": "Original/shared-prefix.md",
            "display_name": "shared-prefix.md", "source_type": "md", "locator": "claim two",
            "excerpt": second_excerpt, "source_date": "2026-08-13",
        },
    ]), encoding="utf-8")
    (package / "Assistant" / "knowledge-items.json").write_text(json.dumps([
        {
            "knowledge_item_id": "K-SHARED-PREFIX-1", "text": "First rebuilt claim",
            "category": "decision", "source_date": "2026-08-13",
            "citation_ids": ["C-SHARED-PREFIX-1"], "review_status": "unreviewed",
        },
        {
            "knowledge_item_id": "K-SHARED-PREFIX-2", "text": "Second rebuilt claim",
            "category": "decision", "source_date": "2026-08-13",
            "citation_ids": ["C-SHARED-PREFIX-2"], "review_status": "unreviewed",
        },
    ]), encoding="utf-8")

    rebuilt_settings = replace(
        settings, app=replace(settings.app, database_path=settings.app.database_path.with_name("shared-prefix.db"))
    )
    rebuilt_db = Database(rebuilt_settings.app.database_path)
    rebuilt_db.migrate()
    rebuilt = PortfolioService(rebuilt_settings, rebuilt_db, FakeLlmAdapter())
    assert rebuilt.rebuild_index()["errors"] == 0
    items = {
        item["id"]: item for item in rebuilt.get_project(project["id"])["knowledge_history"]
        if item["id"].startswith("K-SHARED-PREFIX-")
    }
    assert set(items) == {"K-SHARED-PREFIX-1", "K-SHARED-PREFIX-2"}
    citation_ids = [items[item_id]["citations"][0]["citation_id"] for item_id in sorted(items)]
    assert citation_ids == ["C-SHARED-PREFIX-1", "C-SHARED-PREFIX-2"]
    with rebuilt.db.connect() as connection:
        chunks = connection.execute(
            "SELECT chunk_id FROM citation_records WHERE id IN (?, ?) ORDER BY id", citation_ids
        ).fetchall()
    assert len(chunks) == 2
    assert chunks[0]["chunk_id"] != chunks[1]["chunk_id"]
    assert rebuilt.rebuild_index()["errors"] == 0


def test_legacy_attachment_migration_preserves_attachment_in_package(project, service, settings):
    legacy = settings.app.one_drive_root / "Projects" / "legacy-project"
    attachment_dir = legacy / "attachments" / "1"
    attachment_dir.mkdir(parents=True)
    container = legacy / "legacy.eml"
    attachment = attachment_dir / "legacy-evidence.txt"
    container.write_bytes(b"legacy email bytes")
    attachment.write_bytes(b"legacy attachment evidence")
    now = "2026-08-12T12:00:00+00:00"
    with service.db.transaction() as connection:
        root = connection.execute(
            """INSERT INTO sources(
               project_id, source_type, sha256, original_filename, original_path,
               metadata_json, processing_state, created_at
               ) VALUES (?, 'eml', ?, 'legacy.eml', ?, '{}', 'complete', ?)""",
            (project["id"], hashlib.sha256(container.read_bytes()).hexdigest(), str(container), now),
        ).lastrowid
        child = connection.execute(
            """INSERT INTO sources(
               project_id, parent_source_id, source_type, native_id, sha256, original_filename,
               original_path, metadata_json, processing_state, created_at
               ) VALUES (?, ?, 'txt', ?, ?, 'legacy-evidence.txt', ?, '{}', 'complete', ?)""",
            (project["id"], root, f"legacy-attachment:{root}",
             hashlib.sha256(attachment.read_bytes()).hexdigest(), str(attachment), now),
        ).lastrowid
    result = service.migrate_archive()
    assert result["sources"] >= 1
    migrated = service.source_detail(int(root))
    package = Path(migrated["ingestion_path"])
    attachment_record = next(item for item in migrated["original_files"] if item["is_attachment"])
    assert (package / attachment_record["relative_path"]).read_bytes() == attachment.read_bytes()
    with service.db.connect() as connection:
        child_row = connection.execute("SELECT * FROM sources WHERE id = ?", (child,)).fetchone()
    assert Path(child_row["original_path"]).read_bytes() == attachment.read_bytes()
    assert Path(child_row["original_path"]).is_relative_to(package)
    rebuilt_settings = replace(
        settings, app=replace(settings.app, database_path=settings.app.database_path.with_name("legacy-rebuilt.db"))
    )
    rebuilt_db = Database(rebuilt_settings.app.database_path)
    rebuilt_db.migrate()
    rebuilt = PortfolioService(rebuilt_settings, rebuilt_db, FakeLlmAdapter())
    assert rebuilt.rebuild_index()["errors"] == 0
    rebuilt_source = next(
        source for source in rebuilt.get_project(project["id"])["sources"]
        if source["original_filename"] == "legacy.eml"
    )
    assert any(item["is_attachment"] for item in rebuilt.source_detail(rebuilt_source["id"])["original_files"])


def test_legacy_attachment_copy_failure_is_recorded_and_migration_continues(
    project, service, settings, monkeypatch,
):
    legacy = settings.app.one_drive_root / "Projects" / "legacy-copy-failure"
    attachment_dir = legacy / "attachments"
    attachment_dir.mkdir(parents=True)
    container = legacy / "legacy-container.eml"
    attachment = attachment_dir / "unavailable-during-copy.txt"
    preserved_attachment = attachment_dir / "preserved-after-failure.txt"
    container.write_bytes(b"legacy container")
    attachment.write_bytes(b"legacy attachment")
    preserved_attachment.write_bytes(b"preserved legacy attachment")
    now = "2026-08-12T12:00:00+00:00"
    with service.db.transaction() as connection:
        root = connection.execute(
            """INSERT INTO sources(
               project_id, source_type, sha256, original_filename, original_path,
               metadata_json, processing_state, created_at
               ) VALUES (?, 'eml', ?, 'legacy-container.eml', ?, '{}', 'complete', ?)""",
            (project["id"], hashlib.sha256(container.read_bytes()).hexdigest(), str(container), now),
        ).lastrowid
        connection.execute(
            """INSERT INTO sources(
               project_id, parent_source_id, source_type, native_id, sha256, original_filename,
               original_path, metadata_json, processing_state, created_at
               ) VALUES (?, ?, 'txt', ?, ?, 'unavailable-during-copy.txt', ?, '{}', 'complete', ?)""",
            (project["id"], root, f"legacy-copy-failure:{root}",
             hashlib.sha256(attachment.read_bytes()).hexdigest(), str(attachment), now),
        )
        connection.execute(
            """INSERT INTO sources(
               project_id, parent_source_id, source_type, native_id, sha256, original_filename,
               original_path, metadata_json, processing_state, created_at
               ) VALUES (?, ?, 'txt', ?, ?, 'preserved-after-failure.txt', ?, '{}', 'complete', ?)""",
            (project["id"], root, f"legacy-copy-success:{root}",
             hashlib.sha256(preserved_attachment.read_bytes()).hexdigest(),
             str(preserved_attachment), now),
        )

    real_copy = shutil.copy2

    def fail_attachment_copy(source, destination, *args, **kwargs):
        if Path(source) == attachment:
            raise OSError("fictional attachment read failure")
        return real_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr("portfolio_assistant.services.shutil.copy2", fail_attachment_copy)
    result = service.migrate_archive()
    assert result["sources"] >= 1
    assert result["missing_originals"] == 1
    package = Path(service.source_detail(int(root))["ingestion_path"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert (
        "Legacy attachment could not be archived due to a file-system error: "
        "unavailable-during-copy.txt"
    ) in manifest["errors"]
    migrated_attachment = next(
        item for item in manifest["original_files"]
        if item.get("original_name") == "preserved-after-failure.txt"
    )
    assert migrated_attachment["is_attachment"] is True
    assert (package / migrated_attachment["relative_path"]).read_bytes() == preserved_attachment.read_bytes()


def test_post_rename_database_failure_preserves_incomplete_package(project, service, monkeypatch):
    original_transaction = service.db.transaction

    @contextmanager
    def fail_transaction():
        raise sqlite3.OperationalError("fictional database outage")
        yield

    monkeypatch.setattr(service.db, "transaction", fail_transaction)
    with pytest.raises(sqlite3.OperationalError):
        service.capture_source(
            BytesIO(b"original survives the failed database insert"), "survives.txt",
            project_id=project["id"],
        )
    monkeypatch.setattr(service.db, "transaction", original_transaction)
    incomplete = list(Path(project["folder_path"]).glob("_INCOMPLETE_I-*"))
    assert len(incomplete) == 1
    assert (incomplete[0] / "Original" / "survives.txt").read_bytes() == b"original survives the failed database insert"
    with service.db.connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM sources WHERE original_filename = 'survives.txt'"
        ).fetchone()[0] == 0
