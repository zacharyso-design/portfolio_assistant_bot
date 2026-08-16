"""Regression tests for defects found during the debugging pass.

Each test fails against the code as it stood before the corresponding fix.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio_assistant.db import Database
from portfolio_assistant.extraction import (
    ExtractionFailure, decode_text_bytes, safe_filename,
)


def upload(client: TestClient, project_id: str, name: str, data: bytes, **fields):
    return client.post(
        f"/api/projects/{project_id}/sources",
        files={"file": (name, data, "application/octet-stream")},
        data={key: str(value).lower() if isinstance(value, bool) else value
              for key, value in fields.items()},
    )


class TestSafeFilename:
    """safe_filename used Path().name, whose separator set is host-OS dependent."""

    @pytest.mark.parametrize("raw", [
        "..\\..\\unsafe.txt",
        "../../unsafe.txt",
        "..\\../unsafe.txt",
        "C:\\Windows\\System32\\unsafe.txt",
        "\\\\server\\share\\unsafe.txt",
    ])
    def test_traversal_segments_are_removed_on_every_platform(self, raw):
        cleaned = safe_filename(raw)
        assert cleaned == "unsafe.txt"
        assert ".." not in cleaned
        assert "\\" not in cleaned and "/" not in cleaned

    @pytest.mark.parametrize("raw", ["NUL.txt", "con", "COM1", "lpt9.log", "AUX.dat"])
    def test_windows_device_names_are_defused(self, raw):
        # Writing to <package>\Original\NUL.txt opens the NUL device on Windows and
        # silently discards the archived bytes, so the stored source hashes empty.
        cleaned = safe_filename(raw)
        assert cleaned.startswith("_")
        assert Path(cleaned).stem.upper().lstrip("_") in {
            "NUL", "CON", "COM1", "LPT9", "AUX",
        }

    def test_ordinary_names_are_untouched(self):
        assert safe_filename("Quarterly Review 2026.docx") == "Quarterly Review 2026.docx"
        assert safe_filename("notes-v1.2.txt") == "notes-v1.2.txt"

    def test_empty_and_dot_only_names_fall_back(self):
        assert safe_filename("", "source") == "source"
        assert safe_filename("..", "source") == "source"
        assert safe_filename("...", "source") == "source"

    def test_truncation_does_not_leave_a_trailing_dot(self):
        cleaned = safe_filename("x" * 179 + "." + "y" * 40)
        assert not cleaned.endswith(".")
        assert len(cleaned) <= 180


class TestTextDecoding:
    """cp1252 accepts any byte sequence, so UTF-16 silently became mojibake."""

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
    def test_bom_marked_files_decode_to_real_text(self, tmp_path, encoding):
        # These are exactly what PowerShell 5.1 ">" redirection and Notepad
        # "Save as Unicode" produce, and what cp1252 used to swallow as mojibake.
        sentence = "Fictional meeting notes: decision approved."
        target = tmp_path / "notes.txt"
        target.write_bytes(sentence.encode(encoding))
        assert decode_text_bytes(target) == sentence
        assert "\x00" not in decode_text_bytes(target)

    @pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
    def test_explicit_bom_prefixes_decode(self, tmp_path, encoding):
        import codecs
        marks = {
            "utf-16-le": codecs.BOM_UTF16_LE, "utf-16-be": codecs.BOM_UTF16_BE,
            "utf-32-le": codecs.BOM_UTF32_LE, "utf-32-be": codecs.BOM_UTF32_BE,
        }
        sentence = "Fictional meeting notes: decision approved."
        target = tmp_path / "notes.txt"
        target.write_bytes(marks[encoding] + sentence.encode(encoding))
        assert decode_text_bytes(target) == sentence

    def test_utf8_and_cp1252_still_work(self, tmp_path):
        utf8 = tmp_path / "utf8.txt"
        utf8.write_text("Fictional caf\u00e9 notes", encoding="utf-8-sig")
        assert decode_text_bytes(utf8) == "Fictional caf\u00e9 notes"

        legacy = tmp_path / "legacy.txt"
        legacy.write_bytes("Fictional caf\u00e9 notes".encode("cp1252"))
        assert decode_text_bytes(legacy) == "Fictional caf\u00e9 notes"

    def test_truncated_utf16_is_reported_not_mangled(self, tmp_path):
        broken = tmp_path / "broken.txt"
        broken.write_bytes(b"\xff\xfeA\x00B")  # odd trailing byte
        with pytest.raises(ExtractionFailure):
            decode_text_bytes(broken)


class TestMigrationAtomicity:
    """executescript() committed each statement outside the version-row transaction."""

    def test_interrupted_migration_replays_instead_of_bricking_startup(self, tmp_path):
        database = Database(tmp_path / "portfolio.db")
        database.migrate()

        # Simulate a crash after the DDL committed but before the version was recorded.
        with database.connect() as connection:
            connection.execute(
                "DELETE FROM schema_migrations WHERE version = '003_source_lifecycle'"
            )
            connection.commit()

        # Previously raised sqlite3.OperationalError: duplicate column name.
        database.migrate()

        with database.connect() as connection:
            versions = {
                row["version"] for row in
                connection.execute("SELECT version FROM schema_migrations").fetchall()
            }
        assert "003_source_lifecycle" in versions

    def test_migration_is_atomic_when_a_statement_fails(self, tmp_path):
        database = Database(tmp_path / "portfolio.db")
        database.migrate()
        with database.connect() as connection:
            with pytest.raises(sqlite3.OperationalError):
                database._apply_migration(
                    connection,
                    "999_atomicity_probe",
                    """
                    CREATE TABLE atomicity_probe (id INTEGER PRIMARY KEY);
                    INSERT INTO atomicity_probe(id) VALUES (1);
                    INSERT INTO deliberately_missing_table(id) VALUES (1);
                    """,
                )
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='atomicity_probe'"
            ).fetchone()
            version_exists = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version='999_atomicity_probe'"
            ).fetchone()

        assert table_exists is None
        assert version_exists is None

    def test_statement_splitter_keeps_trigger_bodies_intact(self):
        script = """
        CREATE TABLE demo (id INTEGER PRIMARY KEY, value TEXT);
        CREATE TRIGGER demo_ai AFTER INSERT ON demo BEGIN
          UPDATE demo SET value = 'x' WHERE id = new.id;
          UPDATE demo SET value = 'y' WHERE id = new.id;
        END;
        CREATE INDEX ix_demo ON demo(value);
        """
        statements = list(Database._split_statements(script))
        assert len(statements) == 3
        assert statements[1].count("UPDATE demo") == 2
        assert statements[1].rstrip().endswith("END;")


class TestLifecycleMigrationReplay:
    """Replaying migration 003 reactivated sources the user had removed from memory."""

    def test_replay_preserves_a_removed_source(self, tmp_path):
        database = Database(tmp_path / "portfolio.db")
        database.migrate()

        with database.connect() as connection:
            group_id = connection.execute(
                "SELECT id FROM portfolio_groups ORDER BY id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO projects(id, name, portfolio_group_id, folder_path,"
                " created_at, updated_at)"
                " VALUES ('P-REMOVED', 'Fictional Removed Project', ?, 'P-REMOVED-folder',"
                " '2026-01-01', '2026-01-01')",
                (group_id,),
            )
            connection.execute(
                "INSERT INTO sources(id, project_id, source_type, sha256, original_filename,"
                " original_path, processing_state, created_at, processed_at, memory_state,"
                " memory_state_changed_at)"
                " VALUES (999, 'P-REMOVED', 'text', 'removed-sha', 'removed.txt',"
                " 'removed.txt', 'complete', '2026-01-01', '2026-01-02', 'removed',"
                " '2026-02-01')"
            )
            connection.execute(
                "DELETE FROM schema_migrations WHERE version = '003_source_lifecycle'"
            )
            connection.commit()

        database.migrate()

        with database.connect() as connection:
            source = connection.execute(
                "SELECT memory_state, memory_state_changed_at FROM sources WHERE id = 999"
            ).fetchone()
        assert dict(source) == {
            "memory_state": "removed",
            "memory_state_changed_at": "2026-02-01",
        }


class TestSearchIndex:
    """The FTS integrity check compared a table against itself and never fired."""

    def test_a_stale_fts_index_is_rebuilt_on_startup(self, tmp_path):
        path = tmp_path / "portfolio.db"
        database = Database(path)
        database.migrate()

        with database.connect() as connection:
            group_id = connection.execute(
                "SELECT id FROM portfolio_groups ORDER BY id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO projects(id, name, portfolio_group_id, folder_path,"
                " created_at, updated_at)"
                " VALUES ('P1', 'Fictional Atlas', ?, 'P1-folder', '2026-01-01', '2026-01-01')",
                (group_id,),
            )
            connection.execute(
                "INSERT INTO sources(id, project_id, source_type, sha256, original_filename,"
                " original_path, processing_state, created_at)"
                " VALUES (1, 'P1', 'text', 'abc', 'a.txt', 'a.txt', 'complete', '2026-01-01')"
            )
            connection.execute(
                "INSERT INTO source_chunks(id, project_id, source_id, sequence, locator, text)"
                " VALUES (1, 'P1', 1, 0, 'line 1', 'distinctive alpha evidence')"
            )
            # Drop the index content the way a lost/corrupted index would.
            connection.execute("INSERT INTO source_chunks_fts(source_chunks_fts) VALUES ('delete-all')")
            connection.commit()

        reopened = Database(path)
        reopened.migrate()
        assert reopened.fts_mode == "fts5"

        with reopened.connect() as connection:
            hits = connection.execute(
                "SELECT count(*) FROM source_chunks_fts WHERE source_chunks_fts MATCH 'alpha'"
            ).fetchone()[0]
        assert hits == 1, "a stale FTS index must be rebuilt, not silently left empty"


class TestSearchWildcards:
    """Unescaped LIKE patterns let a bare % or _ match every row."""

    def test_wildcard_queries_do_not_match_everything(self, client: TestClient, project):
        other = client.post("/api/projects", json={"name": "Fictional Beacon Rollout"})
        assert other.status_code == 201

        for query in ("%", "_", "%%"):
            results = client.get("/api/search", params={"q": query}).json()
            assert results == [], f"query {query!r} behaved as a wildcard"

    def test_literal_percent_still_matches(self, client: TestClient):
        created = client.post("/api/projects", json={"name": "Fictional 100% Complete Rollout"})
        assert created.status_code == 201
        results = client.get("/api/search", params={"q": "100%"}).json()
        assert any(row["result_type"] == "project" for row in results)

    def test_portfolio_filter_does_not_treat_underscore_as_wildcard(self, client: TestClient, project):
        payload = client.get("/api/projects", params={"q": "_"}).json()
        assert payload["items"] == [], "a bare _ behaved as a single-character wildcard"


class TestCitationIdentifiers:
    """Truncated 32-bit citation IDs collided and served another project's original."""

    def test_a_colliding_identifier_is_lengthened_not_reused(self, service, client: TestClient, project):
        with service.db.connect() as connection:
            group_id = connection.execute(
                "SELECT id FROM portfolio_groups ORDER BY id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO projects(id, name, portfolio_group_id, folder_path,"
                " created_at, updated_at)"
                " VALUES ('P-OTHER', 'Fictional Beacon', ?, 'P-OTHER-folder',"
                " '2026-01-01', '2026-01-01')",
                (group_id,),
            )
            connection.execute(
                "INSERT INTO sources(id, project_id, source_type, sha256, original_filename,"
                " original_path, processing_state, created_at)"
                " VALUES (900, 'P-OTHER', 'text', 'sha-other', 'beacon.txt',"
                " 'beacon.txt', 'complete', '2026-01-01')"
            )
            connection.execute(
                "INSERT INTO source_chunks(id, project_id, source_id, sequence, locator, text)"
                " VALUES (900, 'P-OTHER', 900, 0, 'line 1', 'beacon evidence')"
            )
            connection.commit()

        key = "901:901"
        digest = hashlib.sha256(key.encode()).hexdigest().upper()
        squatted = f"C-{digest[:8]}"

        with service.db.connect() as connection:
            connection.execute(
                """INSERT INTO citation_records(id, source_id, chunk_id, original_relative_path,
                   display_name, source_type, locator, excerpt, source_date, created_at)
                   VALUES (?, 900, 900, 'Original/beacon.txt', 'beacon.txt', 'text',
                   'line 1', 'beacon evidence', '2026-01-01', '2026-01-01')""",
                (squatted,),
            )
            connection.execute(
                "INSERT INTO sources(id, project_id, source_type, sha256, original_filename,"
                " original_path, processing_state, created_at)"
                " VALUES (901, ?, 'text', 'sha-atlas', 'atlas.txt', 'atlas.txt',"
                " 'complete', '2026-01-01')",
                (project["id"],),
            )
            connection.execute(
                "INSERT INTO source_chunks(id, project_id, source_id, sequence, locator, text)"
                " VALUES (901, ?, 901, 0, 'line 1', 'atlas evidence')",
                (project["id"],),
            )
            connection.commit()

        with service.db.transaction() as connection:
            root = connection.execute("SELECT * FROM sources WHERE id = 901").fetchone()
            result = service._ensure_citation_record(
                connection, root, {"source_id": 901, "chunk_id": 901}, "2026-01-02",
            )

        assert result["citation_id"] != squatted, "citation ID collided with another project's record"

        with service.db.connect() as connection:
            owner = connection.execute(
                "SELECT source_id, chunk_id FROM citation_records WHERE id = ?",
                (result["citation_id"],),
            ).fetchone()
        assert (owner["source_id"], owner["chunk_id"]) == (901, 901)

    def test_the_same_citation_is_stable_across_calls(self, service, client: TestClient, project):
        with service.db.connect() as connection:
            connection.execute(
                "INSERT INTO sources(id, project_id, source_type, sha256, original_filename,"
                " original_path, processing_state, created_at)"
                " VALUES (902, ?, 'text', 'sha-x', 'atlas.txt', 'atlas.txt',"
                " 'complete', '2026-01-01')",
                (project["id"],),
            )
            connection.execute(
                "INSERT INTO source_chunks(id, project_id, source_id, sequence, locator, text)"
                " VALUES (902, ?, 902, 0, 'line 1', 'atlas evidence')",
                (project["id"],),
            )
            connection.commit()

        ids = []
        for _ in range(2):
            with service.db.transaction() as connection:
                root = connection.execute("SELECT * FROM sources WHERE id = 902").fetchone()
                ids.append(service._ensure_citation_record(
                    connection, root, {"source_id": 902, "chunk_id": 902}, "2026-01-02",
                )["citation_id"])
        assert ids[0] == ids[1]


class TestRoutedCitationIdentifiers:
    """Routed reviews still used an unchecked 32-bit citation identifier."""

    def test_a_routed_citation_collision_is_lengthened_not_reused(self, client, service):
        client.post("/api/projects", json={"name": "Fictional Routing Alpha"})
        target = client.post(
            "/api/projects", json={"name": "Fictional Routing Beta"},
        ).json()
        captured = client.post(
            "/api/intake/multi-project",
            files={"file": (
                "routing-collision.txt",
                b"Fictional Routing Beta owns this cited segment.",
                "text/plain",
            )},
        ).json()["source"]
        processed = client.post(f"/api/sources/{captured['id']}/retry")
        assert processed.status_code == 200
        review = next(
            item for item in client.get("/api/reviews?status=open").json()
            if item["source_id"] == captured["id"]
        )

        with service.db.transaction() as connection:
            original_chunk = connection.execute(
                "SELECT * FROM source_chunks WHERE source_id = ? ORDER BY id LIMIT 1",
                (captured["id"],),
            ).fetchone()
            next_source_id = int(connection.execute(
                "SELECT seq + 1 FROM sqlite_sequence WHERE name = 'sources'"
            ).fetchone()[0])
            next_chunk_id = int(connection.execute(
                "SELECT seq + 1 FROM sqlite_sequence WHERE name = 'source_chunks'"
            ).fetchone()[0])
            digest = hashlib.sha256(
                f"{next_source_id}:{next_chunk_id}".encode()
            ).hexdigest().upper()
            squatted = f"C-{digest[:8]}"
            connection.execute(
                """INSERT INTO citation_records(
                   id, source_id, chunk_id, original_relative_path, display_name,
                   source_type, locator, excerpt, source_date, created_at
                   ) VALUES (?, ?, ?, 'Original/routing-collision.txt',
                             'routing-collision.txt', 'text', ?, ?, '2026-08-15',
                             '2026-08-15T12:00:00+00:00')""",
                (
                    squatted, captured["id"], original_chunk["id"],
                    original_chunk["locator"], original_chunk["text"],
                ),
            )

        resolved = client.post(f"/api/reviews/{review['id']}/resolve", json={
            "action": "apply",
            "target_project_id": target["id"],
            "rule": review["evidence"][0]["suggested_rule"],
        })
        assert resolved.status_code == 200, resolved.text
        derived_source_id = resolved.json()["resolution"]["derived_source_id"]
        citation = service.list_knowledge(target["id"])[0]["citations"][0]

        assert citation["citation_id"] != squatted
        with service.db.connect() as connection:
            owner = connection.execute(
                "SELECT source_id FROM citation_records WHERE id = ?",
                (citation["citation_id"],),
            ).fetchone()
        assert int(owner["source_id"]) == int(derived_source_id)


class TestSnowMigration:
    """migrate_archive copied the shared ServiceNow export into every project folder."""

    @staticmethod
    def _create_legacy_snow_package(service, project, source_id: int, export_bytes: bytes):
        digest = hashlib.sha256(export_bytes).hexdigest()
        package = Path(project["folder_path"]) / f"legacy-snow-package-{source_id}"
        filename = f"snow-export-{source_id}.csv"
        original = package / "Original" / filename
        original.parent.mkdir(parents=True)
        original.write_bytes(export_bytes)
        ingestion_id = f"I-SNOW{source_id}"
        (package / "manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "ingestion_id": ingestion_id,
            "database_project_id": project["id"],
            "source_type": "snow_comments",
            "capture_method": "legacy_migration",
            "original_files": [{
                "relative_path": f"Original/{filename}",
                "sha256": digest,
            }],
        }), encoding="utf-8")
        with service.db.transaction() as connection:
            connection.execute(
                """INSERT INTO sources(
                   id, project_id, source_type, native_id, sha256, original_filename,
                   original_path, processing_state, created_at, ingestion_id, ingestion_path,
                   source_title, capture_method, memory_state, project_fit_confirmed,
                   memory_state_changed_at
                   ) VALUES (?, ?, 'snow_comments', ?, ?, ?, ?, 'complete', '2026-08-15',
                             ?, ?, 'Fictional SNOW comments', 'legacy_migration',
                             'active', 1, '2026-08-15')""",
                (
                    source_id, project["id"], f"snow:INC{source_id}:legacy", digest,
                    filename, str(original), ingestion_id, str(package),
                ),
            )
            connection.execute(
                """INSERT INTO original_files(
                   source_id, relative_path, original_name, stored_name, size_bytes,
                   sha256, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, '2026-08-15')""",
                (
                    source_id, f"Original/{filename}", filename, filename,
                    len(export_bytes), digest,
                ),
            )
        return package, digest

    def test_snow_sources_are_not_materialized_into_project_folders(self, service, client, project, tmp_path):
        export = tmp_path / "snow-export.csv"
        export.write_text("number,comments\nINC001,fictional note\n", encoding="utf-8")

        with service.db.connect() as connection:
            connection.execute(
                "INSERT INTO sources(id, project_id, source_type, native_id, sha256,"
                " original_filename, original_path, processing_state, memory_state,"
                " project_fit_confirmed, created_at)"
                " VALUES (950, ?, 'snow_comments', 'snow:INC001:abc', 'sha-snow',"
                " ?, ?, 'captured', 'pending', 1, '2026-01-01')",
                (project["id"], export.name, str(export)),
            )
            connection.commit()

        service.migrate_archive()

        leaked = [
            path for path in Path(project["folder_path"]).rglob("*")
            if path.is_file() and path.name.endswith("snow-export.csv")
        ]
        assert leaked == [], f"the shared ServiceNow export was copied into a project folder: {leaked}"

        with service.db.connect() as connection:
            row = connection.execute(
                "SELECT capture_method, ingestion_path FROM sources WHERE id = 950"
            ).fetchone()
        assert row["capture_method"] != "legacy_migration"
        assert row["ingestion_path"] is None

    def test_an_already_migrated_snow_export_is_removed_from_the_project(
        self, service, project,
    ):
        export_bytes = b"number,comments\nINC001,fictional shared export\n"
        digest = hashlib.sha256(export_bytes).hexdigest()
        imports = service.settings.app.one_drive_root / "_PortfolioAssistant" / "imports" / "snow"
        imports.mkdir(parents=True, exist_ok=True)
        authoritative = imports / "20260815-120000-shared-snow-export.csv"
        authoritative.write_bytes(export_bytes)

        package = Path(project["folder_path"]) / "legacy-snow-package"
        original = package / "Original" / "shared-snow-export.csv"
        original.parent.mkdir(parents=True)
        original.write_bytes(export_bytes)
        (package / "manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "ingestion_id": "I-SNOWLEAK",
            "database_project_id": project["id"],
            "source_type": "snow_comments",
            "capture_method": "legacy_migration",
            "original_files": [{
                "relative_path": "Original/shared-snow-export.csv",
                "sha256": digest,
            }],
        }), encoding="utf-8")

        with service.db.transaction() as connection:
            connection.execute(
                """INSERT INTO sources(
                   id, project_id, source_type, native_id, sha256, original_filename,
                   original_path, processing_state, created_at, ingestion_id, ingestion_path,
                   source_title, capture_method, memory_state, project_fit_confirmed,
                   memory_state_changed_at
                   ) VALUES (951, ?, 'snow_comments', 'snow:INC001:leaked', ?,
                             'shared-snow-export.csv', ?, 'complete', '2026-08-15',
                             'I-SNOWLEAK', ?, 'INC001 comments', 'legacy_migration',
                             'active', 1, '2026-08-15')""",
                (project["id"], digest, str(original), str(package)),
            )
            connection.execute(
                """INSERT INTO original_files(
                   source_id, relative_path, original_name, stored_name, size_bytes,
                   sha256, created_at
                   ) VALUES (951, 'Original/shared-snow-export.csv',
                             'shared-snow-export.csv', 'shared-snow-export.csv', ?, ?,
                             '2026-08-15')""",
                (len(export_bytes), digest),
            )

        service.migrate_archive()

        assert not package.exists()
        with service.db.connect() as connection:
            row = connection.execute(
                """SELECT original_path, ingestion_id, ingestion_path, capture_method
                   FROM sources WHERE id = 951"""
            ).fetchone()
            originals = connection.execute(
                "SELECT count(*) FROM original_files WHERE source_id = 951"
            ).fetchone()[0]
        assert Path(row["original_path"]) == authoritative
        assert row["ingestion_id"] is None
        assert row["ingestion_path"] is None
        assert row["capture_method"] == "snow_import"
        assert originals == 0

    def test_locked_import_candidate_does_not_abort_cleanup(
        self, service, project, monkeypatch,
    ):
        import portfolio_assistant.services as services_module

        export_bytes = b"number,comments\nINC954,fictional recovery source\n"
        package, _ = self._create_legacy_snow_package(
            service, project, 954, export_bytes,
        )
        imports = service.settings.app.one_drive_root / "_PortfolioAssistant" / "imports" / "snow"
        locked = imports / "locked-placeholder.csv"
        locked.write_bytes(b"offline placeholder")
        real_sha256_file = services_module.sha256_file

        def sometimes_locked(path):
            if Path(path) == locked:
                raise OSError("fictional OneDrive placeholder is unavailable")
            return real_sha256_file(path)

        monkeypatch.setattr(services_module, "sha256_file", sometimes_locked)

        service.migrate_archive()

        assert not package.exists()
        with service.db.connect() as connection:
            source = connection.execute(
                "SELECT original_path, ingestion_path, capture_method FROM sources WHERE id = 954"
            ).fetchone()
        recovered = Path(source["original_path"])
        assert recovered.read_bytes() == export_bytes
        assert source["ingestion_path"] is None
        assert source["capture_method"] == "snow_import"

    def test_failed_quarantine_delete_is_retried_without_aborting_startup(
        self, service, project, monkeypatch,
    ):
        import portfolio_assistant.services as services_module

        export_bytes = b"number,comments\nINC955,fictional cleanup retry\n"
        package, digest = self._create_legacy_snow_package(
            service, project, 955, export_bytes,
        )
        imports = service.settings.app.one_drive_root / "_PortfolioAssistant" / "imports" / "snow"
        (imports / "authoritative-955.csv").write_bytes(export_bytes)
        quarantine = imports / ".legacy-snow-package-955"
        real_rmtree = services_module.shutil.rmtree
        fail_cleanup = True

        def transient_rmtree(path, *args, **kwargs):
            if fail_cleanup and Path(path) == quarantine:
                raise OSError("fictional OneDrive directory lock")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(services_module.shutil, "rmtree", transient_rmtree)

        service.migrate_archive()

        assert not package.exists()
        assert quarantine.is_dir()
        with service.db.connect() as connection:
            source = connection.execute(
                "SELECT sha256, ingestion_path, capture_method FROM sources WHERE id = 955"
            ).fetchone()
        assert source["sha256"] == digest
        assert source["ingestion_path"] is None
        assert source["capture_method"] == "snow_import"

        fail_cleanup = False
        service.migrate_archive()
        assert not quarantine.exists()

    def test_recovery_never_overwrites_a_mismatched_destination(
        self, service, project,
    ):
        export_bytes = b"number,comments\nINC956,fictional collision-safe recovery\n"
        package, digest = self._create_legacy_snow_package(
            service, project, 956, export_bytes,
        )
        imports = service.settings.app.one_drive_root / "_PortfolioAssistant" / "imports" / "snow"
        recovery = imports / f"recovered-956-{digest[:16]}.csv"
        mismatched = b"fictional unrelated existing bytes"
        recovery.write_bytes(mismatched)

        service.migrate_archive()

        assert recovery.read_bytes() == mismatched
        assert package.is_dir()
        with service.db.connect() as connection:
            source = connection.execute(
                "SELECT ingestion_path, capture_method FROM sources WHERE id = 956"
            ).fetchone()
        assert Path(source["ingestion_path"]) == package
        assert source["capture_method"] == "legacy_migration"


class TestSnowCitationOriginalResolution:
    """ServiceNow citations used a nullable package path and a stale package-relative file path."""

    def test_citation_download_survives_legacy_package_cleanup(
        self, client: TestClient, service, project,
    ):
        export_bytes = b"number,comments\nINC002,fictional citation evidence\n"
        digest = hashlib.sha256(export_bytes).hexdigest()
        imports = service.settings.app.one_drive_root / "_PortfolioAssistant" / "imports" / "snow"
        imports.mkdir(parents=True, exist_ok=True)
        authoritative = imports / "20260815-130000-citation-export.csv"
        authoritative.write_bytes(export_bytes)

        package = Path(project["folder_path"]) / "legacy-snow-citation-package"
        original = package / "Original" / "citation-export.csv"
        original.parent.mkdir(parents=True)
        original.write_bytes(export_bytes)
        (package / "manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "ingestion_id": "I-SNOWCITE",
            "database_project_id": project["id"],
            "source_type": "snow_comments",
            "capture_method": "legacy_migration",
            "original_files": [{
                "relative_path": "Original/citation-export.csv",
                "sha256": digest,
            }],
        }), encoding="utf-8")

        with service.db.transaction() as connection:
            connection.execute(
                """INSERT INTO sources(
                   id, project_id, source_type, native_id, sha256, original_filename,
                   original_path, processing_state, created_at, ingestion_id, ingestion_path,
                   source_title, capture_method, memory_state, project_fit_confirmed,
                   memory_state_changed_at
                   ) VALUES (952, ?, 'snow_comments', 'snow:INC002:citation', ?,
                             'citation-export.csv', ?, 'complete', '2026-08-15',
                             'I-SNOWCITE', ?, 'INC002 comments', 'legacy_migration',
                             'active', 1, '2026-08-15')""",
                (project["id"], digest, str(original), str(package)),
            )
            connection.execute(
                """INSERT INTO original_files(
                   source_id, relative_path, original_name, stored_name, size_bytes,
                   sha256, created_at
                   ) VALUES (952, 'Original/citation-export.csv', 'citation-export.csv',
                             'citation-export.csv', ?, ?, '2026-08-15')""",
                (len(export_bytes), digest),
            )
            connection.execute(
                """INSERT INTO source_chunks(
                   id, source_id, project_id, sequence, text, locator, processing_state
                   ) VALUES (952, 952, ?, 0, 'fictional citation evidence',
                             'SNOW INC002', 'complete')""",
                (project["id"],),
            )
            source = connection.execute("SELECT * FROM sources WHERE id = 952").fetchone()
            citation = service._ensure_citation_record(
                connection, source, {"source_id": 952, "chunk_id": 952}, "2026-08-15",
            )

        service.migrate_archive()

        response = client.get(f"/api/citations/{citation['citation_id']}/original")
        assert response.status_code == 200, response.text
        assert response.content == export_bytes
        assert "citation-export.csv" in response.headers["content-disposition"]


class TestCredentialRejectionDetail:
    """A 401 discarded the server's body, hiding the key-unlock URL it contains."""

    @staticmethod
    def _response(status: int, *, json_body=None, text: str = "") -> "httpx.Response":
        import httpx
        request = httpx.Request("GET", "https://api.genai.mil/v1/models")
        if json_body is not None:
            return httpx.Response(status, json=json_body, request=request)
        return httpx.Response(status, text=text, request=request)

    def test_openai_style_error_body_is_surfaced(self):
        from portfolio_assistant.llm import InternalHttpLlmAdapter as A
        response = self._response(401, json_body={"error": {
            "message": "API key is locked. Unlock at https://genai.mil/stark/user-ui/keys/unlock/abc",
            "type": "invalid_request_error",
        }})
        message = A._rejection_message("API key saved in Settings", 401, response)
        assert "HTTP 401" in message
        assert "locked" in message
        assert "genai.mil/stark/user-ui/keys/unlock/abc" in message

    @pytest.mark.parametrize("body,expected", [
        ({"error": "key locked"}, "key locked"),
        ({"message": "key locked"}, "key locked"),
        ({"detail": "key locked"}, "key locked"),
    ])
    def test_alternate_body_shapes(self, body, expected):
        from portfolio_assistant.llm import InternalHttpLlmAdapter as A
        assert expected in A._rejection_message("x", 401, self._response(401, json_body=body))

    def test_html_error_page_is_flattened_not_dumped(self):
        from portfolio_assistant.llm import InternalHttpLlmAdapter as A
        html = "<html><body><h1>401</h1>\n<p>Sign in to  the gateway</p></body></html>"
        message = A._rejection_message("x", 401, self._response(401, text=html))
        assert "<" not in message and ">" not in message
        assert "Sign in to the gateway" in message

    def test_empty_body_leaves_the_message_unchanged(self):
        from portfolio_assistant.llm import InternalHttpLlmAdapter as A
        message = A._rejection_message("API key saved in Settings", 401, self._response(401, text=""))
        assert message == "GenAI.mil rejected the API key saved in Settings (HTTP 401)"

    def test_detail_is_bounded(self):
        from portfolio_assistant.llm import InternalHttpLlmAdapter as A
        message = A._rejection_message("x", 401, self._response(401, text="y" * 5000))
        assert len(message) < 400


class TestCredentialRejectionSecretRedaction:
    """A gateway rejection could echo credentials into the UI and persisted source errors."""

    @staticmethod
    def _response(*, json_body=None, text: str = "") -> "httpx.Response":
        import httpx
        request = httpx.Request("POST", "https://api.genai.mil/v1/chat/completions")
        if json_body is not None:
            return httpx.Response(401, json=json_body, request=request)
        return httpx.Response(401, text=text, request=request)

    def test_active_key_is_redacted_without_hiding_unlock_url(self):
        from portfolio_assistant.llm import InternalHttpLlmAdapter as A
        active_key = "fictional-active-api-key-123456789"
        unlock_url = "https://genai.mil/stark/user-ui/keys/unlock/abc"
        response = self._response(json_body={
            "error": {"message": f"Credential {active_key} is locked. Unlock at {unlock_url}"},
        })

        message = A._rejection_message(
            "API key saved in Settings", 401, response, active_credential=active_key,
        )

        assert active_key not in message
        assert "[REDACTED]" in message
        assert unlock_url in message

    def test_common_bearer_and_named_token_values_are_redacted(self):
        from portfolio_assistant.llm import InternalHttpLlmAdapter as A
        response = self._response(
            text="Authorization: Bearer gateway-session-token; api_key=secondary-secret",
        )

        message = A._rejection_message("x", 401, response)

        assert "gateway-session-token" not in message
        assert "secondary-secret" not in message
        assert message.count("[REDACTED]") == 2

    @pytest.mark.parametrize("operation", ["list_models", "test_connection"])
    def test_live_adapter_paths_supply_the_active_key_to_redaction(self, operation):
        import httpx
        from portfolio_assistant.config import LlmSettings
        from portfolio_assistant.llm import InternalHttpLlmAdapter, LlmUnavailable

        active_key = "fictional-live-path-key-987654321"
        response = httpx.Response(401, json={
            "error": {"message": f"Credential {active_key} was rejected"},
        })

        class CredentialStore:
            @staticmethod
            def get():
                return active_key

        class ModelStore:
            @staticmethod
            def load():
                return None

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            @staticmethod
            def get(*_, **__):
                return response

            @staticmethod
            def post(*_, **__):
                return response

        adapter = InternalHttpLlmAdapter(
            LlmSettings(max_attempts=1, rate_limit_requests=0),
            credential_store=CredentialStore(),
            model_preference_store=ModelStore(),
            client_factory=lambda **_: Client(),
        )

        with pytest.raises(LlmUnavailable) as rejected:
            getattr(adapter, operation)()

        assert active_key not in str(rejected.value)
        assert "[REDACTED]" in str(rejected.value)


class TestBackgroundWorkerRecovery:
    """One unexpected batch failure permanently stopped the background ingestion worker."""

    def test_worker_retries_after_an_unexpected_batch_failure(self, tmp_path, monkeypatch):
        from portfolio_assistant.api import create_app
        from portfolio_assistant.config import AppSettings, LlmSettings, Settings
        from portfolio_assistant.services import PortfolioService

        one_drive = tmp_path / "one-drive"
        one_drive.mkdir()
        attempts = 0
        retried = threading.Event()

        def flaky_process_pending(self, *, manual=False, source_id=None, limit=20):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("fictional transient OneDrive failure")
            retried.set()
            return {
                "processed": 0, "pending_ai": 0, "needs_review": 0,
                "unsupported": 0, "error": 0,
            }

        monkeypatch.setattr(PortfolioService, "process_pending", flaky_process_pending)
        settings = Settings(
            app=AppSettings(
                database_path=tmp_path / "portfolio.db",
                one_drive_root=one_drive,
                worker_poll_seconds=0.01,
                testing=False,
            ),
            llm=LlmSettings(adapter="fake", model="fake-llm-v1"),
        )
        app = create_app(settings)

        with TestClient(
            app,
            base_url="http://127.0.0.1:8765",
            headers={"X-Requested-With": "CHIO-Portfolio-Assistant"},
        ):
            assert retried.wait(timeout=2), "worker did not run again after the first failure"

        assert attempts >= 2


class TestMultiProjectUnexpectedFailureContainment:
    """An unexpected multi-project failure left the source permanently in processing."""

    def test_unexpected_failure_transitions_the_source_to_error(
        self, client: TestClient, service, monkeypatch,
    ):
        captured = client.post(
            "/api/intake/multi-project",
            files={
                "file": (
                    "fictional-cross-project.txt",
                    b"Fictional evidence for more than one project.",
                    "text/plain",
                ),
            },
        ).json()["source"]

        def unexpected_failure(_source_id):
            raise RuntimeError("fictional unexpected implementation failure")

        monkeypatch.setattr(service, "_ensure_extracted", unexpected_failure)

        assert service.process_multi_source(captured["id"]) == "error"
        with service.db.connect() as connection:
            source = connection.execute(
                "SELECT processing_state, error_code, error_message FROM sources WHERE id = ?",
                (captured["id"],),
            ).fetchone()
        assert source["processing_state"] == "error"
        assert source["error_code"] == "multi_project_processing_failed"
        assert "fictional unexpected implementation failure" not in source["error_message"]


class TestSharedIntakeDuplicateAttachments:
    """A duplicate-byte attachment in shared intake was written to disk but omitted from the index."""

    def test_both_identical_attachments_are_indexed_and_manifested(
        self, client: TestClient, service,
    ):
        from email.message import EmailMessage

        client.post("/api/projects", json={"name": "Fictional Intake Destination"})
        message = EmailMessage()
        message["Subject"] = "Fictional shared-intake duplicate attachments"
        message["From"] = "sender@example.test"
        message["To"] = "team@example.test"
        message.set_content("Two separately named attachments intentionally contain identical text.")
        duplicate_bytes = b"Fictional identical attachment evidence."
        message.add_attachment(
            duplicate_bytes, maintype="text", subtype="plain", filename="first-evidence.txt",
        )
        message.add_attachment(
            duplicate_bytes, maintype="text", subtype="plain", filename="second-evidence.txt",
        )
        captured = client.post(
            "/api/intake/multi-project",
            files={"file": ("duplicate-attachments.eml", message.as_bytes(), "message/rfc822")},
        ).json()["source"]

        processed = client.post(f"/api/sources/{captured['id']}/retry")
        assert processed.status_code == 200, processed.text

        with service.db.connect() as connection:
            children = connection.execute(
                """SELECT id, original_filename, sha256 FROM sources
                   WHERE parent_source_id = ? ORDER BY id""",
                (captured["id"],),
            ).fetchall()
            originals = connection.execute(
                """SELECT original_name, relative_path, sha256 FROM original_files
                   WHERE source_id = ? AND is_attachment = 1 ORDER BY id""",
                (captured["id"],),
            ).fetchall()
            chunks = connection.execute(
                """SELECT source_id, text FROM source_chunks
                   WHERE source_id IN (SELECT id FROM sources WHERE parent_source_id = ?)
                   ORDER BY source_id""",
                (captured["id"],),
            ).fetchall()
        manifest = json.loads(
            (Path(captured["ingestion_path"]) / "manifest.json").read_text(encoding="utf-8")
        )
        manifested_attachments = [
            item for item in manifest["original_files"] if item.get("is_attachment")
        ]

        assert [row["original_filename"] for row in children] == [
            "first-evidence.txt", "second-evidence.txt",
        ]
        assert len({row["sha256"] for row in children}) == 1
        assert len(originals) == 2
        assert len(manifested_attachments) == 2
        assert len(chunks) == 2
        assert all("Fictional identical attachment evidence" in row["text"] for row in chunks)

    def test_attachment_bytes_do_not_deduplicate_a_later_root_upload(
        self, client: TestClient, service,
    ):
        from email.message import EmailMessage

        client.post("/api/projects", json={"name": "Fictional Root Dedupe Destination"})
        attachment_bytes = b"Fictional bytes shared by an attachment and later root."
        message = EmailMessage()
        message["Subject"] = "Fictional attachment before standalone upload"
        message.set_content("The attachment is archived before the standalone root arrives.")
        message.add_attachment(
            attachment_bytes, maintype="text", subtype="plain", filename="nested-first.txt",
        )
        email_source = client.post(
            "/api/intake/multi-project",
            files={"file": ("attachment-first.eml", message.as_bytes(), "message/rfc822")},
        ).json()["source"]
        assert client.post(f"/api/sources/{email_source['id']}/retry").status_code == 200
        with service.db.connect() as connection:
            child = connection.execute(
                "SELECT id FROM sources WHERE parent_source_id = ?",
                (email_source["id"],),
            ).fetchone()

        standalone = client.post(
            "/api/intake/multi-project",
            files={"file": ("standalone-later.txt", attachment_bytes, "text/plain")},
        )

        assert standalone.status_code == 202, standalone.text
        payload = standalone.json()
        assert payload["duplicate"] is False
        assert payload["source"]["id"] != child["id"]
        with service.db.connect() as connection:
            root = connection.execute(
                "SELECT parent_source_id FROM sources WHERE id = ?",
                (payload["source"]["id"],),
            ).fetchone()
        assert root["parent_source_id"] is None

        repeated = client.post(
            "/api/intake/multi-project",
            files={"file": ("standalone-repeated.txt", attachment_bytes, "text/plain")},
        )
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["duplicate"] is True
        assert repeated.json()["source"]["id"] == payload["source"]["id"]

    def test_upgrade_installs_root_and_child_identity_constraints(self, tmp_path):
        database = Database(tmp_path / "portfolio.db")
        database.migrate()
        with database.connect() as connection:
            connection.execute("DROP INDEX ux_sources_intake_sha")
            connection.execute("DROP INDEX IF EXISTS ux_sources_intake_child_native")
            connection.execute(
                "CREATE UNIQUE INDEX ux_sources_intake_sha"
                " ON sources(sha256) WHERE project_id IS NULL"
            )
            connection.execute(
                """INSERT INTO sources(
                   id, project_id, source_type, sha256, original_filename, original_path,
                   processing_state, created_at
                   ) VALUES (1900, NULL, 'eml', 'legacy-root-sha', 'legacy.eml',
                             'legacy.eml', 'captured', '2026-08-15')"""
            )
            for source_id, digest in ((1901, "legacy-child-a"), (1902, "legacy-child-b")):
                connection.execute(
                    """INSERT INTO sources(
                       id, project_id, parent_source_id, source_type, native_id, sha256,
                       original_filename, original_path, processing_state, created_at
                       ) VALUES (?, NULL, 1900, 'txt', 'attachment:1900:0', ?,
                                 'legacy.txt', 'legacy.txt', 'processing', '2026-08-15')""",
                    (source_id, digest),
                )
            connection.execute(
                "DELETE FROM schema_migrations"
                " WHERE version = '005_shared_intake_attachment_identity'"
            )
            connection.commit()

        database.migrate()

        with database.connect() as connection:
            indexes = {
                row["name"]: row["sql"] for row in connection.execute(
                    """SELECT name, sql FROM sqlite_master
                       WHERE type = 'index' AND name IN (
                         'ux_sources_intake_sha', 'ux_sources_intake_child_native'
                       )"""
                ).fetchall()
            }
            legacy_native_ids = [
                row["native_id"] for row in connection.execute(
                    "SELECT native_id FROM sources WHERE parent_source_id = 1900 ORDER BY id"
                ).fetchall()
            ]
            connection.execute(
                """INSERT INTO sources(
                   id, project_id, source_type, sha256, original_filename, original_path,
                   processing_state, created_at
                   ) VALUES (2000, NULL, 'eml', 'root-sha', 'root.eml', 'root.eml',
                             'captured', '2026-08-15')"""
            )
            connection.execute(
                """INSERT INTO sources(
                   id, project_id, parent_source_id, source_type, native_id, sha256,
                   original_filename, original_path, processing_state, created_at
                   ) VALUES (2001, NULL, 2000, 'txt', 'attachment:2000:0', 'same-bytes',
                             'first.txt', 'first.txt', 'processing', '2026-08-15')"""
            )
            connection.execute(
                """INSERT INTO sources(
                   id, project_id, parent_source_id, source_type, native_id, sha256,
                   original_filename, original_path, processing_state, created_at
                   ) VALUES (2002, NULL, 2000, 'txt', 'attachment:2000:1', 'same-bytes',
                             'second.txt', 'second.txt', 'processing', '2026-08-15')"""
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO sources(
                       id, project_id, parent_source_id, source_type, native_id, sha256,
                       original_filename, original_path, processing_state, created_at
                       ) VALUES (2003, NULL, 2000, 'txt', 'attachment:2000:1', 'changed-bytes',
                                 'retry.txt', 'retry.txt', 'processing', '2026-08-15')"""
                )

        assert "parent_source_id IS NULL" in indexes["ux_sources_intake_sha"]
        assert "parent_source_id" in indexes["ux_sources_intake_child_native"]
        assert legacy_native_ids[0] == "attachment:1900:0"
        assert legacy_native_ids[1] == "attachment:1900:0:legacy-duplicate:1902"

    def test_retry_reclaims_an_attachment_file_left_before_database_insert(
        self, client: TestClient, service,
    ):
        from portfolio_assistant.extraction import AttachmentData

        captured = client.post(
            "/api/intake/multi-project",
            files={"file": (
                "interrupted-parent.txt",
                b"Fictional parent for an interrupted attachment write.",
                "text/plain",
            )},
        ).json()["source"]
        attachment_bytes = b"Fictional attachment left before its database insert."
        attachment_dir = (
            Path(captured["ingestion_path"]) / "Original" / "Attachments"
        )
        attachment_dir.mkdir(parents=True)
        orphan = attachment_dir / "interrupted-child.txt"
        orphan.write_bytes(attachment_bytes)

        service._preserve_attachments(captured["id"], [
            AttachmentData("interrupted-child.txt", attachment_bytes, "text/plain"),
        ])

        assert [path.name for path in attachment_dir.iterdir()] == ["interrupted-child.txt"]
        with service.db.connect() as connection:
            child_count = connection.execute(
                "SELECT count(*) FROM sources WHERE parent_source_id = ?",
                (captured["id"],),
            ).fetchone()[0]
            original = connection.execute(
                """SELECT relative_path FROM original_files
                   WHERE source_id = ? AND is_attachment = 1""",
                (captured["id"],),
            ).fetchone()
        assert child_count == 1
        assert original["relative_path"].endswith("/interrupted-child.txt")


class TestRejectedRoutingRuleArchiveAtomicity:
    """A rejected routing rule left an active orphan package that rescan imported as confirmed."""

    @staticmethod
    def _open_routing_review(client: TestClient):
        client.post("/api/projects", json={"name": "Fictional Routing Source Project"})
        target = client.post(
            "/api/projects", json={"name": "Fictional Routing Target Project"},
        ).json()
        source = client.post(
            "/api/intake/multi-project",
            files={"file": (
                "routing-atomicity.txt",
                b"Fictional Routing Target Project owns this confirmed evidence.",
                "text/plain",
            )},
        ).json()["source"]
        processed = client.post(f"/api/sources/{source['id']}/retry")
        assert processed.status_code == 200, processed.text
        review = next(
            item for item in client.get("/api/reviews?status=open").json()
            if item["source_id"] == source["id"]
        )
        return target, source, review

    @staticmethod
    def _invalid_resolution(target, review):
        return {
            "action": "apply",
            "target_project_id": target["id"],
            "rule": {"rule_type": "filename_phrase", "pattern": "x", "context": {}},
        }

    def test_rejected_rule_creates_no_package_or_rescan_source(
        self, client: TestClient, service,
    ):
        target, source, review = self._open_routing_review(client)
        target_folder = Path(target["folder_path"])
        before = {path.name for path in target_folder.iterdir()}

        rejected = client.post(
            f"/api/reviews/{review['id']}/resolve",
            json=self._invalid_resolution(target, review),
        )

        assert rejected.status_code == 422, rejected.text
        assert {path.name for path in target_folder.iterdir()} == before
        with service.db.connect() as connection:
            current_review = connection.execute(
                "SELECT status FROM review_items WHERE id = ?", (review["id"],)
            ).fetchone()
            derived_before = connection.execute(
                "SELECT count(*) FROM sources WHERE parent_source_id = ? AND project_id = ?",
                (source["id"], target["id"]),
            ).fetchone()[0]
        assert current_review["status"] == "open"
        assert derived_before == 0

        rescanned = client.post("/api/archive/rescan")
        assert rescanned.status_code == 200, rescanned.text
        with service.db.connect() as connection:
            derived_after = connection.execute(
                "SELECT count(*) FROM sources WHERE parent_source_id = ? AND project_id = ?",
                (source["id"], target["id"]),
            ).fetchone()[0]
        assert derived_after == 0

    def test_retry_after_rejection_writes_one_linked_segment(
        self, client: TestClient,
    ):
        target, _, review = self._open_routing_review(client)
        rejected = client.post(
            f"/api/reviews/{review['id']}/resolve",
            json=self._invalid_resolution(target, review),
        )
        assert rejected.status_code == 422

        accepted = client.post(f"/api/reviews/{review['id']}/resolve", json={
            "action": "apply",
            "target_project_id": target["id"],
            "rule": review["evidence"][0]["suggested_rule"],
        })

        assert accepted.status_code == 200, accepted.text
        derived_source_id = accepted.json()["resolution"]["derived_source_id"]
        target_detail = client.get(f"/api/projects/{target['id']}").json()
        derived = next(
            item for item in target_detail["sources"] if item["id"] == derived_source_id
        )
        segments = json.loads(
            (Path(derived["ingestion_path"]) / "Assistant" / "linked-segments.json").read_text(
                encoding="utf-8"
            )
        )
        assert [item["review_id"] for item in segments] == [review["id"]]

    def test_database_failure_after_package_preparation_is_rescan_safe_and_retryable(
        self, client: TestClient, service, monkeypatch,
    ):
        target, source, review = self._open_routing_review(client)
        target_folder = Path(target["folder_path"])
        before = {path.name for path in target_folder.iterdir()}
        original_resolver = service._resolve_routing_review

        def fail_after_preparation(*args, **kwargs):
            original_resolver(*args, **kwargs)
            raise sqlite3.OperationalError("fictional failure before routing transaction commit")

        monkeypatch.setattr(service, "_resolve_routing_review", fail_after_preparation)
        with pytest.raises(sqlite3.OperationalError):
            service.resolve_review(review["id"], {
                "action": "apply",
                "target_project_id": target["id"],
                "rule": review["evidence"][0]["suggested_rule"],
            })

        assert {path.name for path in target_folder.iterdir()} == before
        with service.db.connect() as connection:
            current_review = connection.execute(
                "SELECT status FROM review_items WHERE id = ?", (review["id"],)
            ).fetchone()
            derived = connection.execute(
                "SELECT count(*) FROM sources WHERE parent_source_id = ? AND project_id = ?",
                (source["id"], target["id"]),
            ).fetchone()[0]
        assert current_review["status"] == "open"
        assert derived == 0
        assert client.post("/api/archive/rescan").json()["errors"] == 0

        monkeypatch.setattr(service, "_resolve_routing_review", original_resolver)
        retried = service.resolve_review(review["id"], {
            "action": "apply",
            "target_project_id": target["id"],
            "rule": review["evidence"][0]["suggested_rule"],
        })
        assert retried["status"] == "resolved"
        assert retried["resolution"]["derived_source_id"]

    def test_retry_cleans_a_legacy_incomplete_link_package(
        self, client: TestClient, service,
    ):
        target, source, review = self._open_routing_review(client)
        stale = Path(target["folder_path"]) / f"_INCOMPLETE_LINK_{review['id']}"
        stale.mkdir()
        with service.db.connect() as connection:
            ingestion_id = connection.execute(
                "SELECT ingestion_id FROM sources WHERE id = ?", (source["id"],)
            ).fetchone()["ingestion_id"]
        (stale / "manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "ingestion_id": ingestion_id,
            "project_id": target["archive_id"],
            "database_project_id": target["id"],
            "source_type": "linked-source",
            "canonical_source": False,
            "linked_ingestion_id": ingestion_id,
            "processing_status": "complete",
            "memory_state": "active",
            "original_files": [],
        }), encoding="utf-8")
        (stale / "preserved-marker.txt").write_text("recoverable", encoding="utf-8")

        resolved = client.post(f"/api/reviews/{review['id']}/resolve", json={
            "action": "apply",
            "target_project_id": target["id"],
            "rule": review["evidence"][0]["suggested_rule"],
        })

        assert resolved.status_code == 200, resolved.text
        assert not stale.exists()
        quarantines = list((
            service.settings.app.one_drive_root / "_PortfolioAssistant" / "quarantine" / "routing"
        ).glob(f"legacy-incomplete-review-{review['id']}-*"))
        assert len(quarantines) == 1
        assert (quarantines[0] / "preserved-marker.txt").read_text(encoding="utf-8") == "recoverable"

    def test_unverified_legacy_incomplete_folder_is_never_deleted(
        self, client: TestClient,
    ):
        target, _, review = self._open_routing_review(client)
        unrelated = Path(target["folder_path"]) / f"_INCOMPLETE_LINK_{review['id']}"
        unrelated.mkdir()
        (unrelated / "manifest.json").write_text(
            json.dumps({"source_type": "personal-user-folder"}), encoding="utf-8"
        )
        marker = unrelated / "do-not-delete.txt"
        marker.write_text("user-owned", encoding="utf-8")

        rejected = client.post(f"/api/reviews/{review['id']}/resolve", json={
            "action": "apply",
            "target_project_id": target["id"],
            "rule": review["evidence"][0]["suggested_rule"],
        })

        assert rejected.status_code == 409, rejected.text
        assert marker.read_text(encoding="utf-8") == "user-owned"

    def test_unrelated_existing_destination_is_preserved_and_rejected(
        self, client: TestClient, service,
    ):
        target, source, first_review = self._open_routing_review(client)
        first = client.post(f"/api/reviews/{first_review['id']}/resolve", json={
            "action": "apply",
            "target_project_id": target["id"],
            "rule": first_review["evidence"][0]["suggested_rule"],
        })
        assert first.status_code == 200, first.text
        first_source_id = first.json()["resolution"]["derived_source_id"]
        with service.db.connect() as connection:
            final = Path(connection.execute(
                "SELECT ingestion_path FROM sources WHERE id = ?", (first_source_id,)
            ).fetchone()["ingestion_path"])
        second_review_id = service._create_review(
            kind="multi_project_route",
            source_id=source["id"],
            project_id=target["id"],
            question=first_review["question"],
            reason=first_review["reason"],
            evidence=first_review["evidence"],
            options=first_review["options"],
            memory_preview=first_review["memory_preview"],
        )
        unrelated_manifest = {
            "source_type": "linked-source",
            "canonical_source": False,
            "ingestion_id": "I-UNRELATED",
            "linked_ingestion_id": "I-UNRELATED",
            "database_project_id": target["id"],
            "project_id": target["archive_id"],
        }
        (final / "manifest.json").write_text(
            json.dumps(unrelated_manifest, indent=2), encoding="utf-8"
        )
        segments_path = final / "Assistant" / "linked-segments.json"
        segments_before = segments_path.read_bytes()

        rejected = client.post(f"/api/reviews/{second_review_id}/resolve", json={
            "action": "apply",
            "target_project_id": target["id"],
            "rule": first_review["evidence"][0]["suggested_rule"],
        })

        assert rejected.status_code == 409, rejected.text
        assert json.loads((final / "manifest.json").read_text(encoding="utf-8")) == unrelated_manifest
        assert segments_path.read_bytes() == segments_before
        with service.db.connect() as connection:
            assert connection.execute(
                "SELECT status FROM review_items WHERE id = ?", (second_review_id,)
            ).fetchone()["status"] == "open"

    def test_non_list_existing_segment_sidecar_is_preserved_and_rejected(
        self, client: TestClient, service,
    ):
        target, source, first_review = self._open_routing_review(client)
        first = client.post(f"/api/reviews/{first_review['id']}/resolve", json={
            "action": "apply",
            "target_project_id": target["id"],
            "rule": first_review["evidence"][0]["suggested_rule"],
        })
        assert first.status_code == 200, first.text
        with service.db.connect() as connection:
            final = Path(connection.execute(
                "SELECT ingestion_path FROM sources WHERE id = ?",
                (first.json()["resolution"]["derived_source_id"],),
            ).fetchone()["ingestion_path"])
        second_review_id = service._create_review(
            kind="multi_project_route",
            source_id=source["id"],
            project_id=target["id"],
            question=first_review["question"],
            reason=first_review["reason"],
            evidence=first_review["evidence"],
            options=first_review["options"],
            memory_preview=first_review["memory_preview"],
        )
        malformed = b'{"unexpected":"object instead of list"}'
        segments_path = final / "Assistant" / "linked-segments.json"
        segments_path.write_bytes(malformed)

        rejected = client.post(f"/api/reviews/{second_review_id}/resolve", json={
            "action": "apply",
            "target_project_id": target["id"],
            "rule": first_review["evidence"][0]["suggested_rule"],
        })

        assert rejected.status_code == 409, rejected.text
        assert segments_path.read_bytes() == malformed
        with service.db.connect() as connection:
            assert connection.execute(
                "SELECT status FROM review_items WHERE id = ?", (second_review_id,)
            ).fetchone()["status"] == "open"

    def test_startup_recovery_retains_staging_when_local_database_has_no_review(
        self, service,
    ):
        staging = service._routing_staging_root() / "routing-review-999999"
        (staging / "package").mkdir(parents=True)
        (staging / "publication.json").write_text(json.dumps({
            "review_id": 999999,
            "final_relative_path": "Projects/Fictional/linked-source",
            "mode": "package",
            "package_identity": {
                "source_type": "linked-source",
                "ingestion_id": "I-MISSINGDB",
                "linked_ingestion_id": "I-MISSINGDB",
                "database_project_id": "P-MISSINGDB",
                "project_id": "P-MISSINGDB",
            },
        }), encoding="utf-8")

        service._recover_routing_publications()

        assert staging.is_dir()

    def test_startup_recovery_contains_an_unavailable_staging_root(
        self, service, monkeypatch,
    ):
        def unavailable_root():
            raise OSError("fictional transient OneDrive staging-root conflict")

        monkeypatch.setattr(service, "_routing_staging_root", unavailable_root)

        service._recover_routing_publications()

    def test_post_commit_publication_failure_recovers_from_safe_staging(
        self, client: TestClient, service, monkeypatch,
    ):
        import portfolio_assistant.services as services_module

        target, _, review = self._open_routing_review(client)
        real_replace = services_module.os.replace

        def block_package_publication(source, destination):
            source_path = Path(source)
            if (
                source_path.name == "package"
                and source_path.parent.name == f"routing-review-{review['id']}"
            ):
                raise OSError("fictional transient OneDrive publication lock")
            return real_replace(source, destination)

        monkeypatch.setattr(services_module.os, "replace", block_package_publication)
        resolved = service.resolve_review(review["id"], {
            "action": "apply",
            "target_project_id": target["id"],
            "rule": review["evidence"][0]["suggested_rule"],
        })
        derived_source_id = resolved["resolution"]["derived_source_id"]
        with service.db.connect() as connection:
            final = Path(connection.execute(
                "SELECT ingestion_path FROM sources WHERE id = ?", (derived_source_id,)
            ).fetchone()["ingestion_path"])
        staging = (
            service.settings.app.one_drive_root / "_PortfolioAssistant" / "staging" /
            "routing" / f"routing-review-{review['id']}"
        )
        assert resolved["status"] == "resolved"
        assert not final.exists()
        assert staging.is_dir()
        assert client.post("/api/archive/rescan").json()["errors"] == 0

        monkeypatch.setattr(services_module.os, "replace", real_replace)
        service._recover_routing_publications()
        assert final.is_dir()
        assert not staging.exists()

    def test_recovery_is_idempotent_after_package_move_but_before_staging_cleanup(
        self, client: TestClient, service, monkeypatch,
    ):
        import portfolio_assistant.services as services_module

        target, _, review = self._open_routing_review(client)
        real_rmtree = services_module.shutil.rmtree
        blocked = True

        def block_cleanup_after_move(path, *args, **kwargs):
            nonlocal blocked
            candidate = Path(path)
            if (
                blocked
                and candidate.name == f"routing-review-{review['id']}"
                and not (candidate / "package").exists()
            ):
                blocked = False
                raise OSError("fictional crash after package move")
            return real_rmtree(candidate, *args, **kwargs)

        monkeypatch.setattr(services_module.shutil, "rmtree", block_cleanup_after_move)
        resolved = service.resolve_review(review["id"], {
            "action": "apply",
            "target_project_id": target["id"],
            "rule": review["evidence"][0]["suggested_rule"],
        })
        derived_source_id = resolved["resolution"]["derived_source_id"]
        with service.db.connect() as connection:
            final = Path(connection.execute(
                "SELECT ingestion_path FROM sources WHERE id = ?", (derived_source_id,)
            ).fetchone()["ingestion_path"])
        staging = service._routing_staging_root() / f"routing-review-{review['id']}"
        assert final.is_dir()
        assert staging.is_dir()
        assert not (staging / "package").exists()

        monkeypatch.setattr(services_module.shutil, "rmtree", real_rmtree)
        service._recover_routing_publications()

        assert final.is_dir()
        assert not staging.exists()

    def test_two_pending_reviews_merge_when_recovered_in_reverse_order(
        self, client: TestClient, service, monkeypatch,
    ):
        import portfolio_assistant.services as services_module

        target, source, first_review = self._open_routing_review(client)
        second_review_id = service._create_review(
            kind="multi_project_route",
            source_id=source["id"],
            project_id=target["id"],
            question=first_review["question"],
            reason=first_review["reason"],
            evidence=first_review["evidence"],
            options=first_review["options"],
            memory_preview=first_review["memory_preview"],
        )
        real_replace = services_module.os.replace

        def block_package_publication(source_path, destination):
            source_path = Path(source_path)
            if source_path.name == "package" and source_path.parent.name.startswith("routing-review-"):
                raise OSError("fictional lock holding both committed routing publications")
            return real_replace(source_path, destination)

        monkeypatch.setattr(services_module.os, "replace", block_package_publication)
        resolution = {
            "action": "apply",
            "target_project_id": target["id"],
            "rule": first_review["evidence"][0]["suggested_rule"],
        }
        first = service.resolve_review(first_review["id"], resolution)
        second = service.resolve_review(second_review_id, resolution)
        first_id = first["resolution"]["derived_source_id"]
        second_id = second["resolution"]["derived_source_id"]
        with service.db.connect() as connection:
            paths = {
                Path(row["ingestion_path"])
                for row in connection.execute(
                    "SELECT ingestion_path FROM sources WHERE id IN (?, ?)",
                    (first_id, second_id),
                )
            }
        assert len(paths) == 1
        final = paths.pop()
        staging_root = service._routing_staging_root()
        first_staging = staging_root / f"routing-review-{first_review['id']}"
        second_staging = staging_root / f"routing-review-{second_review_id}"
        assert not final.exists()
        assert first_staging.is_dir() and second_staging.is_dir()

        monkeypatch.setattr(services_module.os, "replace", real_replace)
        assert service._publish_routing_staging(second_staging)
        assert service._publish_routing_staging(first_staging)

        segments = json.loads(
            (final / "Assistant" / "linked-segments.json").read_text(encoding="utf-8")
        )
        assert sorted(item["review_id"] for item in segments) == sorted([
            first_review["id"], second_review_id,
        ])
        assert not first_staging.exists()
        assert not second_staging.exists()


class TestMalformedSidecarRebuildIsolation:
    """A scalar email_metadata sidecar value aborted the entire archive rebuild."""

    def test_bad_package_is_reported_and_good_package_still_rebuilds(
        self, client: TestClient, project, settings,
    ):
        from dataclasses import replace

        from portfolio_assistant.llm import FakeLlmAdapter
        from portfolio_assistant.services import PortfolioService

        good = upload(
            client, project["id"], "rebuild-good.txt", b"Fictional good rebuild evidence."
        ).json()["source"]
        bad = upload(
            client, project["id"], "rebuild-bad.txt", b"Fictional malformed-sidecar evidence."
        ).json()["source"]
        bad_index_path = Path(bad["ingestion_path"]) / "Assistant" / "index.json"
        bad_index = json.loads(bad_index_path.read_text(encoding="utf-8"))
        bad_index["email_metadata"] = "not-an-object"
        bad_index_path.write_text(json.dumps(bad_index), encoding="utf-8")

        rebuilt_settings = replace(
            settings,
            app=replace(
                settings.app,
                database_path=settings.app.database_path.with_name("malformed-sidecar-rebuild.db"),
            ),
        )
        rebuilt_db = Database(rebuilt_settings.app.database_path)
        rebuilt_db.migrate()
        rebuilt = PortfolioService(rebuilt_settings, rebuilt_db, FakeLlmAdapter())

        counts = rebuilt.rebuild_index()

        assert counts["errors"] == 1
        rebuilt_sources = rebuilt.get_project(project["id"])["sources"]
        assert {item["ingestion_id"] for item in rebuilt_sources} == {good["ingestion_id"]}
        assert bad["ingestion_id"] not in {
            item["ingestion_id"] for item in rebuilt_sources
        }


class TestRemovedSourceReupload:
    """Re-uploading a removed file returned a silent duplicate that stayed removed."""

    def test_same_file_upload_restores_removed_source_instead_of_discarding_it(
        self, client: TestClient, project,
    ):
        payload = b"Fictional source that the user intentionally uploads again."
        first = upload(client, project["id"], "restore-on-upload.txt", payload).json()
        source = first["source"]
        removed = client.post(
            f"/api/projects/{project['id']}/sources/{source['id']}/remove",
            json={"reason": "Fictional removal before a deliberate re-upload"},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["memory_state"] == "removed"

        reuploaded = upload(
            client, project["id"], "restore-on-upload.txt", payload
        )

        assert reuploaded.status_code == 202, reuploaded.text
        result = reuploaded.json()
        assert result["duplicate"] is False
        assert result["source"]["id"] == source["id"]
        assert result["source"]["memory_state"] != "removed"
        assert Path(result["source"]["ingestion_path"]).parent == Path(project["folder_path"])
        detail = client.get(f"/api/sources/{source['id']}").json()
        assert detail["original_files"]
        assert any(
            event["event_type"] == "restored_to_memory" for event in detail["lifecycle"]
        )


class TestWindowsLegacyPathBudget:
    """Generated archive and atomic-temp paths exceeded Windows MAX_PATH."""

    def test_long_project_and_source_names_stay_within_legacy_windows_limit(
        self, client: TestClient,
    ):
        project_response = client.post("/api/projects", json={
            "name": "Fictional Long Path Project " + "P" * 200,
        })
        assert project_response.status_code == 201, project_response.text
        project = project_response.json()
        filename = "very-long-source-name-" + "S" * 150 + ".txt"
        captured = upload(
            client,
            project["id"],
            filename,
            b"Fictional content used to verify legacy Windows path budgeting.",
        )
        assert captured.status_code == 202, captured.text
        source = captured.json()["source"]
        retried = client.post(f"/api/sources/{source['id']}/retry")
        assert retried.status_code == 200, retried.text

        package = Path(source["ingestion_path"])
        generated = [package, *package.rglob("*")]
        assert generated
        assert max(len(str(path.resolve())) for path in generated) <= 259
        atomic_directories = {
            path.parent for path in generated
            if path.is_file() and "Assistant" in path.parts
        }
        assert atomic_directories
        assert max(
            len(str((directory / ".tmp-000000000000").resolve()))
            for directory in atomic_directories
        ) <= 259
        assert not list(Path(project["folder_path"]).glob("_INCOMPLETE_*"))

    def test_unusable_deep_destination_is_rejected_before_incomplete_creation(
        self, client: TestClient, project, service, settings,
    ):
        root = settings.app.one_drive_root
        padding = max(1, 205 - len(str(root.resolve())) - 1)
        deep_destination = root / ("D" * padding)
        deep_destination.mkdir()
        assert len(str(deep_destination.resolve())) >= 200
        assert len(str((deep_destination / "_INCOMPLETE_I-12345678").resolve())) <= 259
        with service.db.transaction() as connection:
            connection.execute(
                "UPDATE projects SET folder_path = ? WHERE id = ?",
                (str(deep_destination), project["id"]),
            )

        rejected = upload(
            client,
            project["id"],
            "deep-path.txt",
            b"Fictional content that must be rejected without partial folders.",
        )

        assert rejected.status_code == 422, rejected.text
        assert not list(deep_destination.glob("_INCOMPLETE_*"))

    def test_non_bmp_names_are_budgeted_as_utf16_code_units(
        self, client: TestClient,
    ):
        windows_units = lambda value: len(str(value).encode("utf-16-le")) // 2
        project_response = client.post("/api/projects", json={
            "name": "Fictional Emoji Project " + "😀" * 100,
        })
        assert project_response.status_code == 201, project_response.text
        project = project_response.json()
        captured = upload(
            client,
            project["id"],
            "📁" * 80 + ".txt",
            b"Fictional non-BMP Windows path evidence.",
        )
        assert captured.status_code == 202, captured.text
        source = captured.json()["source"]
        retried = client.post(f"/api/sources/{source['id']}/retry")
        assert retried.status_code == 200, retried.text

        package = Path(source["ingestion_path"])
        generated = [package, *package.rglob("*")]
        assert max(windows_units(path.resolve()) for path in generated) <= 259
        assistant_directories = {
            path.parent for path in generated
            if path.is_file() and "Assistant" in path.parts
        }
        assert max(
            windows_units((directory / ".tmp-000000000000").resolve())
            for directory in assistant_directories
        ) <= 259

    def test_legacy_migration_copy_failure_cleans_incomplete_package(
        self, project, service, settings, monkeypatch,
    ):
        import portfolio_assistant.services as services_module

        legacy_original = settings.app.one_drive_root / "legacy-path-source.txt"
        legacy_original.write_bytes(b"Fictional legacy migration content.")
        with service.db.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO sources(
                   project_id, source_type, sha256, original_filename, original_path,
                   metadata_json, processing_state, created_at
                   ) VALUES (?, 'txt', ?, ?, ?, '{}', 'captured', ?)""",
                (
                    project["id"], hashlib.sha256(legacy_original.read_bytes()).hexdigest(),
                    "legacy-path-source.txt", str(legacy_original),
                    "2026-08-15T12:00:00+00:00",
                ),
            )
            source_id = int(cursor.lastrowid)

        def locked_copy(source, destination, *args, **kwargs):
            if Path(source) == legacy_original:
                raise OSError("fictional locked legacy original")
            return real_copy(source, destination, *args, **kwargs)

        real_copy = services_module.shutil.copy2
        monkeypatch.setattr(services_module.shutil, "copy2", locked_copy)

        service.migrate_archive()

        with service.db.connect() as connection:
            migrated = connection.execute(
                "SELECT ingestion_path FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        assert migrated["ingestion_path"] is None
        assert not list(Path(project["folder_path"]).glob("_INCOMPLETE_*"))

    def test_project_move_rebudgets_package_for_deep_legacy_target(
        self, client: TestClient, project, service, settings,
    ):
        windows_units = lambda value: len(str(value).encode("utf-16-le")) // 2
        target = client.post("/api/projects", json={"name": "Fictional Move Target"}).json()
        root = settings.app.one_drive_root
        padding = max(1, 180 - len(str(root.resolve())) - 1)
        deep_target = root / ("T" * padding)
        deep_target.mkdir()
        with service.db.transaction() as connection:
            connection.execute(
                "UPDATE projects SET folder_path = ? WHERE id = ?",
                (str(deep_target), target["id"]),
            )
        source = upload(
            client,
            project["id"],
            "move-path-" + "M" * 100 + ".txt",
            b"Fictional pending source moved to a deep legacy project.",
        ).json()["source"]
        old_package = Path(source["ingestion_path"])

        moved = service._move_source_package(
            source["id"],
            deep_target / old_package.name,
            memory_state="pending",
            event_type="moved_before_processing",
            reason="Regression test for destination rebudgeting.",
            to_project_id=target["id"],
            project_fit_confirmed=True,
        )

        new_package = Path(moved["ingestion_path"])
        assert new_package.parent == deep_target
        assert new_package.name != old_package.name
        assert not old_package.exists()
        generated = [new_package, *new_package.rglob("*")]
        assert max(windows_units(path.resolve()) for path in generated) <= 259

    def test_project_move_reserves_actual_long_nested_original_tail(
        self, client: TestClient, project, service, settings,
    ):
        windows_units = lambda value: len(str(value).encode("utf-16-le")) // 2
        root = settings.app.one_drive_root
        shallow_source = root / "s"
        shallow_source.mkdir()
        with service.db.transaction() as connection:
            connection.execute(
                "UPDATE projects SET folder_path = ? WHERE id = ?",
                (str(shallow_source), project["id"]),
            )
        nested_relative = (
            "nested-folder-with-a-long-name/"
            "another-folder/long-original-document-name.txt"
        )
        captured = client.post(
            f"/api/projects/{project['id']}/sources",
            files=[("files", (
                "long-original-document-name.txt",
                b"Fictional nested original preserved across a deep project move.",
                "text/plain",
            ))],
            data={"relative_paths": json.dumps([nested_relative])},
        )
        assert captured.status_code == 202, captured.text
        source = captured.json()["source"]
        detail = client.get(f"/api/sources/{source['id']}").json()
        original_relative = detail["original_files"][0]["relative_path"]
        assert windows_units(original_relative) > 45

        target = client.post(
            "/api/projects", json={"name": "Fictional Nested Move Target"}
        ).json()
        descendant_reserve = service._package_descendant_reserve(
            Path(source["ingestion_path"])
        )
        compact_component_units = len("20260815-120000__I-12345678")
        target_parent_units = (
            259 - 1 - descendant_reserve - compact_component_units
        )
        padding = max(1, target_parent_units - windows_units(root.resolve()) - 1)
        deep_target = root / ("N" * padding)
        deep_target.mkdir()
        with service.db.transaction() as connection:
            connection.execute(
                "UPDATE projects SET folder_path = ? WHERE id = ?",
                (str(deep_target), target["id"]),
            )

        moved = service._move_source_package(
            source["id"],
            deep_target / Path(source["ingestion_path"]).name,
            memory_state="pending",
            event_type="moved_before_processing",
            reason="Regression test for descendant-aware move budgeting.",
            to_project_id=target["id"],
            project_fit_confirmed=True,
        )

        new_package = Path(moved["ingestion_path"])
        generated = [new_package, *new_package.rglob("*")]
        assert max(windows_units(path.resolve()) for path in generated) <= 259
        assert (new_package / original_relative).is_file()

    def test_nested_attachment_keeps_user_facing_original_filename(
        self, client: TestClient, project, service,
    ):
        from portfolio_assistant.extraction import AttachmentData

        root = upload(
            client,
            project["id"],
            "attachment-root.txt",
            b"Fictional root for nested attachment provenance.",
        ).json()["source"]
        with service.db.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO sources(
                   project_id, parent_source_id, source_type, native_id, sha256,
                   original_filename, original_path, metadata_json, processing_state, created_at,
                   ingestion_path, canonical_source, memory_state, project_fit_confirmed,
                   memory_state_changed_at
                   ) VALUES (?, ?, 'msg', ?, ?, ?, ?, '{}', 'processing', ?, ?, 0, 'pending', 0, ?)""",
                (
                    project["id"], root["id"], "nested-email:test",
                    hashlib.sha256(b"nested-message").hexdigest(),
                    "forwarded-message.msg", root["original_path"],
                    "2026-08-15T12:00:00+00:00", root["ingestion_path"],
                    "2026-08-15T12:00:00+00:00",
                ),
            )
            nested_id = int(cursor.lastrowid)

        service._preserve_attachments(nested_id, [
            AttachmentData(
                "user-visible-attachment.txt",
                b"Fictional nested attachment text.",
                "text/plain",
            ),
        ])

        with service.db.connect() as connection:
            child = connection.execute(
                "SELECT original_filename, original_path FROM sources WHERE parent_source_id = ?",
                (nested_id,),
            ).fetchone()
        assert child["original_filename"] == "user-visible-attachment.txt"
        assert Path(child["original_path"]).name != child["original_filename"]

    def test_legacy_migration_path_preflight_does_not_abort_startup(
        self, project, service, settings,
    ):
        from portfolio_assistant.llm import FakeLlmAdapter
        from portfolio_assistant.services import PortfolioService

        windows_units = lambda value: len(str(value).encode("utf-16-le")) // 2
        root = settings.app.one_drive_root
        padding = max(1, 195 - windows_units(root.resolve()) - 1)
        deep_project = root / ("L" * padding)
        assistant = deep_project / "_Assistant"
        assistant.mkdir(parents=True)
        descriptor = {
            "schema_version": 1,
            "project_id": project["id"],
            "archive_id": project["archive_id"],
            "name": project["name"],
            "created_at": project["created_at"],
        }
        (assistant / "project.json").write_text(
            json.dumps(descriptor), encoding="utf-8"
        )
        assert windows_units(deep_project.resolve()) >= 190
        with service.db.transaction() as connection:
            connection.execute(
                "UPDATE projects SET folder_path = ? WHERE id = ?",
                (str(deep_project), project["id"]),
            )

        legacy_original = root / "legacy-deep-preflight.txt"
        legacy_original.write_bytes(b"Fictional legacy startup containment evidence.")
        with service.db.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO sources(
                   project_id, source_type, sha256, original_filename, original_path,
                   metadata_json, processing_state, created_at
                   ) VALUES (?, 'txt', ?, ?, ?, '{}', 'captured', ?)""",
                (
                    project["id"], hashlib.sha256(legacy_original.read_bytes()).hexdigest(),
                    "legacy-deep-preflight.txt", str(legacy_original),
                    "2026-08-15T12:00:00+00:00",
                ),
            )
            source_id = int(cursor.lastrowid)

        PortfolioService(settings, service.db, FakeLlmAdapter())

        with service.db.connect() as connection:
            pending = connection.execute(
                "SELECT ingestion_path FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        assert pending["ingestion_path"] is None
        assert not list(deep_project.glob("_INCOMPLETE_*"))

    def test_deep_routing_quarantine_keeps_every_descendant_within_max_path(
        self, settings,
    ):
        from dataclasses import replace

        from portfolio_assistant.llm import FakeLlmAdapter
        from portfolio_assistant.services import PortfolioService

        windows_units = lambda value: len(str(value).encode("utf-16-le")) // 2
        root_parent = settings.app.one_drive_root.parent
        padding = max(1, 175 - windows_units(root_parent.resolve()) - 1)
        deep_root = root_parent / ("Q" * padding)
        deep_root.mkdir()
        deep_settings = replace(
            settings,
            app=replace(
                settings.app,
                database_path=settings.app.database_path.with_name("quarantine-path.db"),
                one_drive_root=deep_root,
            ),
        )
        deep_db = Database(deep_settings.app.database_path)
        deep_db.migrate()
        deep_service = PortfolioService(deep_settings, deep_db, FakeLlmAdapter())
        staging = (
            deep_root / "_PortfolioAssistant" / "staging" / "routing" / "review-1"
        )
        sidecar = staging / "Assistant" / "linked-segments.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text("[]\n", encoding="utf-8")
        assert max(
            windows_units(path.resolve()) for path in [staging, *staging.rglob("*")]
        ) <= 259

        quarantined = deep_service._quarantine_routing_path(
            staging, "legacy-migration-with-a-long-recovery-label"
        )

        assert not staging.exists()
        assert (quarantined / "Assistant" / "linked-segments.json").is_file()
        assert max(
            windows_units(path.resolve())
            for path in [quarantined, *quarantined.rglob("*")]
        ) <= 259


class TestEmbeddedMsgAttachments:
    """_extract_msg silently dropped embedded, broken, and unsupported attachments."""

    def test_embedded_message_is_exported_as_a_preserved_msg(
        self, tmp_path, monkeypatch,
    ):
        from msgforge import Message as MsgForgeMessage

        import portfolio_assistant.extraction as extraction_module

        real_message = extraction_module.extract_msg.Message
        inner_path = tmp_path / "inner.msg"
        MsgForgeMessage(
            subject="Fictional embedded message",
            text_body="Fictional forwarded-email evidence.",
        ).save(inner_path)
        embedded = real_message(str(inner_path), delayAttachments=True)

        class EmbeddedAttachment:
            data = embedded
            longFilename = None
            shortFilename = None
            name = "forwarded-evidence"
            mimetype = "application/vnd.ms-outlook"

        class OuterMessage:
            body = "Fictional outer message."
            htmlBody = None
            attachments = [EmbeddedAttachment()]
            headerDict = {}
            subject = "Fictional outer message"
            sender = "sender@example.invalid"
            to = "recipient@example.invalid"
            cc = ""
            date = "2026-08-15T12:00:00+00:00"

            def close(self):
                pass

        monkeypatch.setattr(
            extraction_module.extract_msg, "Message", lambda *args, **kwargs: OuterMessage()
        )
        outer_path = tmp_path / "outer.msg"
        outer_path.write_bytes(b"Fictional parser input replaced by controlled outer message.")

        result = extraction_module._extract_msg(outer_path, max_attachments=5)

        inner_path.unlink()
        assert not inner_path.exists()
        assert len(result.attachments) == 1
        preserved = result.attachments[0]
        assert preserved.filename == "forwarded-evidence.msg"
        assert preserved.content_type == "application/vnd.ms-outlook"
        exported_path = tmp_path / preserved.filename
        exported_path.write_bytes(preserved.data)
        exported = real_message(str(exported_path), delayAttachments=True)
        try:
            assert exported.subject == "Fictional embedded message"
        finally:
            exported.close()

    @pytest.mark.parametrize("attachment_kind", ["broken", "unsupported"])
    def test_unreadable_attachment_is_reported_as_extraction_failure(
        self, tmp_path, monkeypatch, attachment_kind,
    ):
        import portfolio_assistant.extraction as extraction_module

        class UnreadableAttachment:
            data = None
            type = attachment_kind
            longFilename = None
            shortFilename = None
            name = None
            mimetype = None

        class MessageWithUnreadableAttachment:
            body = "Fictional message with an unreadable attachment."
            htmlBody = None
            attachments = [UnreadableAttachment()]
            headerDict = {}
            subject = "Fictional unreadable attachment"
            sender = "sender@example.invalid"
            to = "recipient@example.invalid"
            cc = ""
            date = "2026-08-15T12:00:00+00:00"

            def close(self):
                pass

        monkeypatch.setattr(
            extraction_module.extract_msg,
            "Message",
            lambda *args, **kwargs: MessageWithUnreadableAttachment(),
        )
        path = tmp_path / f"{attachment_kind}.msg"
        path.write_bytes(b"Fictional parser input replaced by controlled message.")

        with pytest.raises(ExtractionFailure, match="broken or unsupported attachment"):
            extraction_module._extract_msg(path, max_attachments=5)
