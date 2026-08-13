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


def test_lifecycle_migration_inherits_root_state_for_existing_attachment_trees(tmp_path):
    database_path = tmp_path / "pre-lifecycle.db"
    migrations = Path(__file__).parents[1] / "portfolio_assistant" / "migrations"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript((migrations / "001_initial.sql").read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES ('001_initial', '2026-08-13')"
        )
        connection.executescript((migrations / "002_onedrive_archive.sql").read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES ('002_onedrive_archive', '2026-08-13')"
        )
        group_id = connection.execute(
            """INSERT INTO portfolio_groups(name, sort_order, is_system, created_at)
               VALUES ('Migration', 0, 0, '2026-08-13')"""
        ).lastrowid
        connection.execute(
            """INSERT INTO projects(id, name, portfolio_group_id, folder_path, created_at, updated_at)
               VALUES ('P-MIGRATION', 'Migration project', ?, 'C:/fictional/project',
                       '2026-08-13', '2026-08-13')""",
            (group_id,),
        )
        root_id = connection.execute(
            """INSERT INTO sources(
               project_id, source_type, sha256, original_filename, original_path,
               processing_state, created_at
               ) VALUES ('P-MIGRATION', 'eml', 'root-digest', 'root.eml', 'C:/fictional/root.eml',
                         'pending_ai', '2026-08-13')"""
        ).lastrowid
        child_id = connection.execute(
            """INSERT INTO sources(
               project_id, parent_source_id, source_type, native_id, sha256,
               original_filename, original_path, processing_state, created_at, processed_at
               ) VALUES ('P-MIGRATION', ?, 'txt', 'attachment:1', 'child-digest',
                         'child.txt', 'C:/fictional/child.txt', 'complete',
                         '2026-08-13', '2026-08-13')""",
            (root_id,),
        ).lastrowid
        intake_id = connection.execute(
            """INSERT INTO sources(
               project_id, source_type, sha256, original_filename, original_path,
               processing_state, created_at
               ) VALUES (NULL, 'txt', 'intake-digest', 'portfolio.txt',
                         'C:/fictional/portfolio.txt', 'needs_review', '2026-08-13')"""
        ).lastrowid
        routed_id = connection.execute(
            """INSERT INTO sources(
               project_id, parent_source_id, source_type, native_id, sha256,
               original_filename, original_path, processing_state, created_at, processed_at
               ) VALUES ('P-MIGRATION', ?, 'routed_segment', 'routed-review:7',
                         'routed-digest', 'portfolio.txt', 'C:/fictional/portfolio.txt',
                         'complete', '2026-08-13', '2026-08-13')""",
            (intake_id,),
        ).lastrowid
        connection.commit()
    finally:
        connection.close()

    database = Database(database_path)
    database.migrate()
    with database.connect() as migrated:
        rows = migrated.execute(
            "SELECT id, memory_state, project_fit_confirmed FROM sources WHERE id IN (?, ?, ?) ORDER BY id",
            (root_id, child_id, intake_id),
        ).fetchall()
        routed = migrated.execute(
            "SELECT memory_state, project_fit_confirmed FROM sources WHERE id = ?", (routed_id,)
        ).fetchone()
    assert [row["memory_state"] for row in rows] == ["pending", "pending", "pending"]
    assert [row["project_fit_confirmed"] for row in rows] == [0, 0, 1]
    assert routed["memory_state"] == "active"
    assert routed["project_fit_confirmed"] == 1


def test_source_removal_is_recoverable_and_excluded_from_every_memory_surface(
    client, project, settings,
):
    unique = "ORCHID-REMOVAL-7419"
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": (
            "wrong-project-note.txt",
            (
                f"Fictional Atlas Modernization approved {unique}. "
                "ACTION: Validate archive safety | OWNER: me | DUE: 2026-09-01"
            ).encode(),
            "text/plain",
        )},
    )
    assert response.status_code == 202, response.text
    source = response.json()["source"]
    processed = _process(client, source["id"])
    assert processed["processed"] == 1 or processed["already_complete"] == 1

    before = client.get(f"/api/projects/{project['id']}").json()
    active = next(item for item in before["sources"] if item["id"] == source["id"])
    assert active["memory_state"] == "active"
    assert before["knowledge_history"] and before["action_items"] and before["updates"]
    removed_knowledge_id = before["knowledge_history"][0]["id"]
    assert any(item.get("source_id") == source["id"] for item in client.get(
        "/api/search", params={"q": unique, "project_id": project["id"]}
    ).json())

    removed_response = client.post(
        f"/api/projects/{project['id']}/sources/{source['id']}/remove",
        json={"reason": "Accidentally added to the wrong project"},
    )
    assert removed_response.status_code == 200, removed_response.text
    removed = removed_response.json()
    assert removed["memory_state"] == "removed"
    removed_package = Path(removed["ingestion_path"])
    assert removed_package.is_dir()
    assert settings.app.one_drive_root / "CHIO Portfolio Assistant" / "Archive" in removed_package.parents
    assert Path(removed["original_path"]).is_file()

    after = client.get(f"/api/projects/{project['id']}").json()
    assert after["knowledge_history"] == []
    assert after["updates"] == []
    assert after["action_items"] == []
    assert after["living_summary"]["current"]["content"]["sections"] == []
    assert client.get(
        "/api/search", params={"q": unique, "project_id": project["id"]}
    ).json() == []
    assert client.patch(
        f"/api/projects/{project['id']}/knowledge/{removed_knowledge_id}",
        json={"status": "approved"},
    ).status_code == 404
    chat = client.post(
        f"/api/projects/{project['id']}/chat", json={"question": unique}
    ).json()
    assert chat["answer"] == ""
    assert "No matching evidence" in chat["uncertainty"]
    assert client.post(f"/api/sources/{source['id']}/retry").status_code == 409

    lifecycle_path = removed_package / "Assistant" / "source-lifecycle.jsonl"
    events = [json.loads(line) for line in lifecycle_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event_type"] == "removed_from_memory"
    assert events[-1]["reason"] == "Accidentally added to the wrong project"

    restored_response = client.post(
        f"/api/projects/{project['id']}/sources/{source['id']}/restore"
    )
    assert restored_response.status_code == 200, restored_response.text
    restored = restored_response.json()
    assert restored["memory_state"] == "active"
    assert Path(restored["ingestion_path"]).parent == Path(project["folder_path"])
    restored_project = client.get(f"/api/projects/{project['id']}").json()
    assert restored_project["knowledge_history"]
    assert restored_project["action_items"]
    assert any(item.get("source_id") == source["id"] for item in client.get(
        "/api/search", params={"q": unique, "project_id": project["id"]}
    ).json())
    rebuilt = client.post(f"/api/projects/{project['id']}/knowledge/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["active_sources"] == 1
    assert rebuilt.json()["knowledge_items"] >= 1


def test_removing_progress_source_does_not_hide_user_owned_action(client, project):
    created = client.post(f"/api/projects/{project['id']}/actions", json={
        "description": "Publish the user-owned archive guide",
        "assignee_type": "me",
        "assignee_value": "Me",
        "due_date": "2026-09-15",
        "state": "open",
    })
    assert created.status_code == 201, created.text
    action = created.json()
    progress_response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": (
            "user-action-progress.txt",
            f"PROGRESS [{action['id']}]: User-owned guide is ready for review.".encode(),
            "text/plain",
        )},
    )
    progress_source = progress_response.json()["source"]
    assert _process(client, progress_source["id"])["processed"] == 1
    progressed = next(
        item for item in client.get(f"/api/projects/{project['id']}").json()["action_items"]
        if item["id"] == action["id"]
    )
    assert progressed["progress_text"] == "User-owned guide is ready for review."
    assert progressed["source_id"] is None

    removed = client.post(
        f"/api/projects/{project['id']}/sources/{progress_source['id']}/remove",
        json={"reason": "Progress note was added to the wrong project"},
    )
    assert removed.status_code == 200, removed.text
    remaining = client.get(f"/api/projects/{project['id']}").json()["action_items"]
    remaining_action = next(item for item in remaining if item["id"] == action["id"])
    assert remaining_action["progress_text"] is None
    listed_action = next(item for item in client.get(
        f"/api/projects/{project['id']}/actions"
    ).json() if item["id"] == action["id"])
    assert listed_action["progress_text"] is None

    restored = client.post(
        f"/api/projects/{project['id']}/sources/{progress_source['id']}/restore"
    )
    assert restored.status_code == 200, restored.text
    restored_action = next(
        item for item in client.get(f"/api/projects/{project['id']}").json()["action_items"]
        if item["id"] == action["id"]
    )
    assert restored_action["progress_text"] == "User-owned guide is ready for review."


def test_removing_source_preserves_unrelated_open_review(client, project):
    action = client.post(f"/api/projects/{project['id']}/actions", json={
        "description": "Confirm the retained review decision",
        "assignee_type": "me",
        "assignee_value": "Me",
        "due_date": "2026-09-20",
        "state": "open",
    }).json()
    close_response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": (
            "close-review.txt", f"COMPLETE [{action['id']}]".encode(), "text/plain"
        )},
    )
    close_source = close_response.json()["source"]
    assert _process(client, close_source["id"])["processed"] == 1
    review = next(
        item for item in client.get("/api/reviews?status=open").json()
        if item["source_id"] == close_source["id"] and item["kind"] == "action_close"
    )

    removed = client.post(
        f"/api/projects/{project['id']}/sources/{close_source['id']}/remove",
        json={"reason": "The evidence belongs outside this project"},
    )
    assert removed.status_code == 200, removed.text
    still_open = client.get("/api/reviews?status=open").json()
    assert any(item["id"] == review["id"] for item in still_open)


def test_package_less_source_removal_returns_conflict(client, project):
    now = "2026-08-13T12:00:00+00:00"
    with client.app.state.db.transaction() as connection:
        source_id = connection.execute(
            """INSERT INTO sources(
               project_id, source_type, native_id, sha256, original_filename, original_path,
               metadata_json, processing_state, created_at, processed_at, memory_state,
               project_fit_confirmed, memory_state_changed_at
               ) VALUES (?, 'snow_comments', 'snow:PACKAGELESS', 'packageless-digest',
                         'snow-export.csv', 'C:/fictional/snow-export.csv', '{}', 'complete',
                         ?, ?, 'active', 1, ?)""",
            (project["id"], now, now, now),
        ).lastrowid
    response = client.post(
        f"/api/projects/{project['id']}/sources/{source_id}/remove",
        json={"reason": "Package-less regression"},
    )
    assert response.status_code == 409
    assert "no movable OneDrive package" in response.json()["detail"]


def test_malformed_llm_review_can_be_retried(client, project, service, monkeypatch):
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": (
            "retry-contract.txt", b"Fictional retry contract evidence.", "text/plain"
        )},
    )
    source = response.json()["source"]
    original_update = service.llm.knowledge_update
    monkeypatch.setattr(service.llm, "knowledge_update", lambda *args, **kwargs: {
        "updated_summary": "Malformed retry fixture",
        "updates": [{"text": "Missing citation", "citations": []}],
        "action_item_operations": [],
        "project_field_recommendations": [],
    })
    assert _process(client, source["id"])["needs_review"] == 1
    malformed = next(
        item for item in client.get("/api/reviews?status=open").json()
        if item["source_id"] == source["id"] and item["kind"] == "malformed_llm"
    )

    monkeypatch.setattr(service.llm, "knowledge_update", original_update)
    retried = _process(client, source["id"])
    assert retried["processed"] == 1
    assert all(
        item["id"] != malformed["id"] for item in client.get("/api/reviews?status=open").json()
    )


def test_project_fit_review_supports_explicit_keep_and_archive_choices(client):
    alpha = client.post("/api/projects", json={"name": "Fictional Fit Alpha"}).json()
    beta = client.post("/api/projects", json={"name": "Fictional Fit Beta"}).json()

    kept_response = client.post(
        f"/api/projects/{alpha['id']}/sources",
        files={"file": ("keep-despite-fit.txt", b"Fictional Fit Beta owns KEEP-FIT-7712.", "text/plain")},
    )
    kept_source = kept_response.json()["source"]
    assert _process(client, kept_source["id"])["needs_review"] == 1
    kept_review = next(
        item for item in client.get("/api/reviews?status=open").json()
        if item["source_id"] == kept_source["id"]
    )
    kept = client.post(f"/api/reviews/{kept_review['id']}/resolve", json={"action": "keep"})
    assert kept.status_code == 200, kept.text
    alpha_detail = client.get(f"/api/projects/{alpha['id']}").json()
    active_source = next(item for item in alpha_detail["sources"] if item["id"] == kept_source["id"])
    assert active_source["memory_state"] == "active"
    assert any("KEEP-FIT-7712" in item["text"] for item in alpha_detail["knowledge_history"])

    archived_response = client.post(
        f"/api/projects/{alpha['id']}/sources",
        files={"file": ("archive-without-use.txt", b"Fictional Fit Beta owns ARCHIVE-FIT-8813.", "text/plain")},
    )
    archived_source = archived_response.json()["source"]
    assert _process(client, archived_source["id"])["needs_review"] == 1
    archived_review = next(
        item for item in client.get("/api/reviews?status=open").json()
        if item["source_id"] == archived_source["id"]
    )
    archived = client.post(
        f"/api/reviews/{archived_review['id']}/resolve", json={"action": "remove"}
    )
    assert archived.status_code == 200, archived.text
    archived_detail = client.get(f"/api/sources/{archived_source['id']}").json()
    assert archived_detail["memory_state"] == "removed"
    assert "Archive" in Path(archived_detail["ingestion_path"]).parts
    assert all(
        "ARCHIVE-FIT-8813" not in item["text"]
        for item in client.get(f"/api/projects/{alpha['id']}").json()["knowledge_history"]
    )
    restored = client.post(
        f"/api/projects/{alpha['id']}/sources/{archived_source['id']}/restore"
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["memory_state"] == "pending"
    assert restored.json()["project_fit_confirmed"] == 0
    assert restored.json()["processing_state"] == "captured"
    assert _process(client, archived_source["id"])["needs_review"] == 1
    replacement_review = next(
        item for item in client.get("/api/reviews?status=open").json()
        if item["source_id"] == archived_source["id"]
    )
    assert replacement_review["kind"] == "project_fit"


def test_removed_source_lifecycle_rebuilds_from_archive(client, project, settings):
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": (
            "archived-memory.txt",
            b"Fictional Atlas Modernization records REBUILD-REMOVED-3187.",
            "text/plain",
        )},
    )
    source = response.json()["source"]
    _process(client, source["id"])
    removed = client.post(
        f"/api/projects/{project['id']}/sources/{source['id']}/remove",
        json={"reason": "Archive rebuild regression"},
    )
    assert removed.status_code == 200, removed.text

    fresh_settings = replace(
        settings,
        app=replace(settings.app, database_path=settings.app.database_path.parent / "rebuilt.db"),
    )
    fresh_db = Database(fresh_settings.app.database_path)
    fresh_db.migrate()
    rebuilt_service = PortfolioService(fresh_settings, fresh_db, FakeLlmAdapter())
    counts = rebuilt_service.rebuild_index()
    assert counts["errors"] == 0
    rebuilt_project = rebuilt_service.get_project(project["id"])
    rebuilt_source = next(item for item in rebuilt_project["sources"] if item["ingestion_id"] == source["ingestion_id"])
    assert rebuilt_source["memory_state"] == "removed"
    assert rebuilt_project["knowledge_history"] == []
    assert rebuilt_service.search_archive("REBUILD-REMOVED-3187", project_id=project["id"]) == []
    detail = rebuilt_service.source_detail(rebuilt_source["id"])
    assert any(event["event_type"] == "removed_from_memory" for event in detail["lifecycle"])


def test_pending_archive_state_survives_database_rebuild(client, settings):
    alpha = client.post("/api/projects", json={"name": "Fictional Rebuild Alpha"}).json()
    beta = client.post("/api/projects", json={"name": "Fictional Rebuild Beta"}).json()
    response = client.post(
        f"/api/projects/{alpha['id']}/sources",
        files={"file": (
            "pending-archive.txt", b"Fictional Rebuild Beta owns PENDING-REBUILD-2194.", "text/plain"
        )},
    )
    source = response.json()["source"]
    assert _process(client, source["id"])["needs_review"] == 1
    review = next(
        item for item in client.get("/api/reviews?status=open").json()
        if item["source_id"] == source["id"]
    )
    assert client.post(
        f"/api/reviews/{review['id']}/resolve", json={"action": "remove"}
    ).status_code == 200

    fresh_settings = replace(
        settings,
        app=replace(settings.app, database_path=settings.app.database_path.parent / "pending-rebuilt.db"),
    )
    fresh_db = Database(fresh_settings.app.database_path)
    fresh_db.migrate()
    rebuilt_service = PortfolioService(fresh_settings, fresh_db, FakeLlmAdapter())
    assert rebuilt_service.rebuild_index()["errors"] == 0
    rebuilt_source = next(
        item for item in rebuilt_service.get_project(alpha["id"])["sources"]
        if item["ingestion_id"] == source["ingestion_id"]
    )
    assert rebuilt_source["memory_state"] == "removed"
    assert rebuilt_source["archived_previous_memory_state"] == "pending"
    restored = rebuilt_service.restore_source_to_memory(alpha["id"], rebuilt_source["id"])
    assert restored["memory_state"] == "pending"
    assert restored["project_fit_confirmed"] == 0
    assert rebuilt_service.process_source(rebuilt_source["id"]) == "needs_review"


def test_removal_never_exposes_a_stale_current_summary_when_ai_is_down(client, project, service):
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": (
            "stale-summary.txt",
            b"Fictional Atlas Modernization records STALE-SUMMARY-9921.",
            "text/plain",
        )},
    )
    source = response.json()["source"]
    _process(client, source["id"])
    assert "STALE-SUMMARY-9921" in client.get(f"/api/projects/{project['id']}").json()["current_summary"]

    service.llm.available = False
    removed = client.post(
        f"/api/projects/{project['id']}/sources/{source['id']}/remove",
        json={"reason": "Verify stale summary protection"},
    )
    assert removed.status_code == 200, removed.text
    detail = client.get(f"/api/projects/{project['id']}").json()
    assert detail["current_summary"] == ""
    assert detail["living_summary"]["generation_state"] == "failed"
    assert detail["living_summary"]["current"] is None
    assert client.patch(
        f"/api/projects/{project['id']}/living-summary/review", json={"status": "approved"}
    ).status_code == 409
    current_file = Path(project["folder_path"]) / "_Assistant" / "living-summary" / "current.json"
    assert json.loads(current_file.read_text(encoding="utf-8"))["sections"] == []

    restored = client.post(f"/api/projects/{project['id']}/sources/{source['id']}/restore")
    assert restored.status_code == 200, restored.text
    restored_project = client.get(f"/api/projects/{project['id']}").json()
    assert restored_project["living_summary"]["generation_state"] == "failed"
    assert restored_project["living_summary"]["current"] is None
    durable_current = json.loads(current_file.read_text(encoding="utf-8"))
    assert durable_current["revision"] == restored_project["living_summary"]["revision"]
    assert durable_current["sections"] == []


def test_source_move_rolls_back_files_and_sidecars_on_database_failure(
    client, project, service, monkeypatch,
):
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": (
            "rollback-source.txt",
            b"Fictional Atlas Modernization records ROLLBACK-MOVE-4421.",
            "text/plain",
        )},
    )
    source = response.json()["source"]
    _process(client, source["id"])
    original_package = Path(source["ingestion_path"])
    original_manifest = (original_package / "manifest.json").read_text(encoding="utf-8")
    original_summary = (
        Path(project["folder_path"]) / "_Assistant" / "living-summary" / "current.json"
    ).read_text(encoding="utf-8")

    def fail_lifecycle(*args, **kwargs):
        raise sqlite3.OperationalError("fictional lifecycle failure")

    monkeypatch.setattr(service, "_record_source_lifecycle", fail_lifecycle)
    with pytest.raises(sqlite3.OperationalError, match="fictional lifecycle failure"):
        service.remove_source_from_memory(project["id"], source["id"], "Rollback test")

    assert original_package.is_dir()
    assert (original_package / "manifest.json").read_text(encoding="utf-8") == original_manifest
    assert (
        Path(project["folder_path"]) / "_Assistant" / "living-summary" / "current.json"
    ).read_text(encoding="utf-8") == original_summary
    detail = service.source_detail(source["id"])
    assert detail["memory_state"] == "active"
    assert detail["ingestion_path"] == str(original_package)
    assert all(event["event_type"] != "removed_from_memory" for event in detail["lifecycle"])


def test_manifest_remains_authoritative_when_nonessential_refresh_fails(
    client, project, service, monkeypatch,
):
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": (
            "durable-removal.txt",
            b"Fictional Atlas Modernization records DURABLE-REMOVE-5521.",
            "text/plain",
        )},
    )
    source = response.json()["source"]
    _process(client, source["id"])
    monkeypatch.setattr(
        service, "_refresh_source_archive",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("fictional derived-sidecar failure")),
    )
    removed = service.remove_source_from_memory(project["id"], source["id"], "Durability test")
    assert removed["memory_state"] == "removed"
    manifest = json.loads((Path(removed["ingestion_path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["memory_state"] == "removed"
    assert manifest["database_project_id"] == project["id"]
    assert service.list_knowledge(project["id"]) == []


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
    knowledge = service.list_knowledge(project["id"])
    assert knowledge
    assert service.get_living_summary(project["id"])["generation_state"] == "failed"
    reviewed = service.review_knowledge(project["id"], knowledge[0]["id"], "approved")
    assert reviewed["review_status"] == "approved"
    assert service.get_living_summary(project["id"])["generation_state"] == "failed"


def test_routed_source_is_canonical_once_and_linked_to_project(client, settings, service, monkeypatch):
    first = client.post("/api/projects", json={"name": "Fictional Archive Alpha"}).json()
    second = client.post("/api/projects", json={"name": "Fictional Archive Beta"}).json()
    source = client.post(
        "/api/intake/multi-project",
        files={"file": ("portfolio.txt", b"Fictional Archive Alpha approved the plan.", "text/plain")},
        data={"meeting_name": "Portfolio Review", "meeting_date": "2026-08-13", "is_transcript": "true"},
    ).json()["source"]
    _process(client, source["id"])
    review = next(item for item in client.get("/api/reviews?status=open").json() if item["source_id"] == source["id"])
    monkeypatch.setattr(
        service.llm, "living_summary",
        lambda project_data, knowledge: (_ for _ in ()).throw(RuntimeError("fictional routing summary error")),
    )
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
