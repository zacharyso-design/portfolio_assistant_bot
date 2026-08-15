from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ALREADY_APPLIED_MESSAGE = re.compile(r"duplicate column name|already exists", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.fts_mode = "fts5"

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._open_connection()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _split_statements(script: str) -> Iterator[str]:
        """Split a migration into statements, keeping CREATE TRIGGER bodies intact.

        ``sqlite3.complete_statement`` understands that a trigger is not finished
        until its ``END;``, so nested semicolons do not split the body.
        """
        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                if statement:
                    yield statement
                buffer = ""
        remainder = buffer.strip()
        if remainder:
            yield remainder

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migration_dir = Path(__file__).with_name("migrations")
        with self.connect() as connection:
            for migration in sorted(migration_dir.glob("*.sql")):
                version = migration.stem
                exists = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone() if self._table_exists(connection, "schema_migrations") else None
                if exists:
                    continue
                # executescript() commits after every statement, which would leave a
                # half-applied schema with no recorded version if the process died
                # mid-migration; the next start then fails on "duplicate column name"
                # and the application never opens again. Applying the statements and
                # the version row in one transaction makes an interrupted migration
                # roll back cleanly and simply replay on the next start.
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in self._split_statements(migration.read_text(encoding="utf-8")):
                        try:
                            connection.execute(statement)
                        except sqlite3.OperationalError as exc:
                            # Databases left half-migrated by an earlier build still
                            # need to start: replay the migration and skip the parts
                            # that are demonstrably already in place.
                            if not ALREADY_APPLIED_MESSAGE.search(str(exc)):
                                raise
                    connection.execute(
                        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (version, utc_now()),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            self._install_search(connection)
            connection.commit()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def _install_search(self, connection: sqlite3.Connection) -> None:
        try:
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS source_chunks_fts USING fts5(
                  text, project_id UNINDEXED, source_id UNINDEXED,
                  content='source_chunks', content_rowid='id'
                );
                CREATE TRIGGER IF NOT EXISTS source_chunks_ai AFTER INSERT ON source_chunks BEGIN
                  INSERT INTO source_chunks_fts(rowid, text, project_id, source_id)
                  VALUES (new.id, new.text, new.project_id, new.source_id);
                END;
                CREATE TRIGGER IF NOT EXISTS source_chunks_ad AFTER DELETE ON source_chunks BEGIN
                  INSERT INTO source_chunks_fts(source_chunks_fts, rowid, text, project_id, source_id)
                  VALUES ('delete', old.id, old.text, old.project_id, old.source_id);
                END;
                CREATE TRIGGER IF NOT EXISTS source_chunks_au AFTER UPDATE ON source_chunks BEGIN
                  INSERT INTO source_chunks_fts(source_chunks_fts, rowid, text, project_id, source_id)
                  VALUES ('delete', old.id, old.text, old.project_id, old.source_id);
                  INSERT INTO source_chunks_fts(rowid, text, project_id, source_id)
                  VALUES (new.id, new.text, new.project_id, new.source_id);
                END;
                """
            )
            # source_chunks_fts is an external-content table, so counting its rows
            # reads source_chunks and can never detect a stale or empty index. Ask
            # FTS5 instead. rank = 1 is required: the bare integrity-check only
            # verifies the index is internally consistent, and an index that is
            # simply empty is perfectly consistent with itself.
            try:
                connection.execute(
                    "INSERT INTO source_chunks_fts(source_chunks_fts, rank)"
                    " VALUES ('integrity-check', 1)"
                )
            except sqlite3.OperationalError:
                # SQLite older than 3.37 cannot compare against the content table.
                connection.execute(
                    "INSERT INTO source_chunks_fts(source_chunks_fts) VALUES ('integrity-check')"
                )
            except sqlite3.DatabaseError:
                connection.execute("INSERT INTO source_chunks_fts(source_chunks_fts) VALUES ('rebuild')")
            self.fts_mode = "fts5"
        except sqlite3.OperationalError:
            self.fts_mode = "like-fallback"
        connection.execute(
            "INSERT INTO app_settings(key, value) VALUES ('retrieval_mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (self.fts_mode,),
        )

    def search_chunks(self, project_id: str, query: str, limit: int = 12) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 30))
        if not query.strip():
            return []
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}", query)[:12]
        with self.connect() as connection:
            if self.fts_mode == "fts5" and tokens:
                expression = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
                rows = connection.execute(
                    """
                    SELECT c.*, s.original_filename, s.meeting_name, s.meeting_date,
                           bm25(source_chunks_fts) AS rank
                    FROM source_chunks_fts
                    JOIN source_chunks c ON c.id = source_chunks_fts.rowid
                    JOIN sources s ON s.id = c.source_id
                    WHERE source_chunks_fts MATCH ? AND c.project_id = ? AND s.memory_state = 'active'
                    ORDER BY rank LIMIT ?
                    """,
                    (expression, project_id, limit),
                ).fetchall()
            else:
                escaped = query[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                rows = connection.execute(
                    """
                    SELECT c.*, s.original_filename, s.meeting_name, s.meeting_date, 0 AS rank
                    FROM source_chunks c JOIN sources s ON s.id = c.source_id
                    WHERE c.project_id = ? AND c.text LIKE ? ESCAPE '\\' AND s.memory_state = 'active'
                    ORDER BY c.id DESC LIMIT ?
                    """,
                    (project_id, pattern, limit),
                ).fetchall()
        return [dict(row) for row in rows]
