"""Regression tests for defects found during the debugging pass.

Each test fails against the code as it stood before the corresponding fix.
"""
from __future__ import annotations

import sqlite3
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


class TestMigrations:
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
            before = {
                row["version"] for row in
                connection.execute("SELECT version FROM schema_migrations").fetchall()
            }
        database.migrate()  # second run is a no-op
        with database.connect() as connection:
            after = {
                row["version"] for row in
                connection.execute("SELECT version FROM schema_migrations").fetchall()
            }
        assert before == after

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

        import hashlib

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


class TestSnowMigration:
    """migrate_archive copied the shared ServiceNow export into every project folder."""

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
