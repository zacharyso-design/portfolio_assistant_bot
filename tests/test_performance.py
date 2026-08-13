from __future__ import annotations

import time
import uuid
from datetime import date

from portfolio_assistant.db import utc_now


def test_required_scale_and_response_thresholds(service, settings):
    group_ids = [service.create_group("Fictional Enterprise", 10)["id"], service.create_group("Fictional Clinical", 20)["id"]]
    now = utc_now()
    projects = []
    for index in range(250):
        project_id = str(uuid.uuid4())
        folder = settings.app.one_drive_root / "Projects" / f"fictional-scale-{index:03d}--{project_id}"
        folder.mkdir(parents=True)
        (folder / "sources").mkdir()
        (folder / "attachments").mkdir()
        projects.append((
            project_id, f"Fictional Scale Project {index:03d}", group_ids[index % 2],
            "Complete" if index >= 150 else ("Red" if index % 20 == 0 else "Green"),
            ("Critical", "High", "Medium", "Low")[index % 4],
            f"Fictional Owner {index % 15}", f"Validate scale milestone {index}", date.today().isoformat(),
            f"Fictional change {index}", f"REQ{9000000 + index}",
            "Fictional Apps" if index % 2 == 0 else "Fictional Infrastructure",
            str(folder), f"Current fictional summary {index}", now, now,
        ))
    with service.db.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO projects(id, name, portfolio_group_id, status, priority, owner_text,
              next_action, next_action_due, latest_change, snow_number, assignment_group,
              folder_path, current_summary, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            projects,
        )
        source_rows = []
        for index, project in enumerate(projects):
            source_rows.append((
                project[0], "txt", f"scale:{index}", f"{index:064x}", f"scale-{index}.txt",
                str(settings.app.one_drive_root / "Projects" / f"scale-original-{index}.txt"),
                "{}", "complete", now, now,
            ))
        connection.executemany(
            """INSERT INTO sources(project_id, source_type, native_id, sha256, original_filename,
               original_path, metadata_json, processing_state, created_at, processed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            source_rows,
        )
        sources = connection.execute("SELECT id, project_id FROM sources ORDER BY id").fetchall()
        chunk_rows = []
        for source in sources:
            for sequence in range(200):
                chunk_rows.append((
                    source["id"], source["project_id"], sequence,
                    f"Fictional validation evidence item {sequence} for project {source['project_id']}.",
                    f"lines {sequence + 1}-{sequence + 1}", "complete", now,
                ))
        connection.executemany(
            """INSERT INTO source_chunks(source_id, project_id, sequence, text, locator, processing_state, processed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            chunk_rows,
        )
        update_rows = []
        action_rows = []
        for index, project in enumerate(projects):
            for update_index in range(20):
                update_rows.append((project[0], "scale", f"Fictional update {update_index} for {project[1]}", "[]", now))
            for action_index in range(8):
                action_rows.append((
                    project[0], f"Fictional action {action_index}", "team_office", "Fictional Office",
                    date.today().isoformat(), "open", "Scale fixture", "user", now, now,
                ))
        connection.executemany(
            """INSERT INTO project_updates(project_id, update_type, text, citations_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            update_rows,
        )
        connection.executemany(
            """INSERT INTO action_items(project_id, description, assignee_type, assignee_value,
               due_date, state, progress_text, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            action_rows,
        )

    start = time.perf_counter()
    board = service.list_projects(limit=250)
    board_ms = (time.perf_counter() - start) * 1000
    assert board["total"] == 250 and board["metrics"]["active"] == 150
    assert board_ms < 500, f"board response took {board_ms:.1f} ms"

    start = time.perf_counter()
    filtered = service.list_projects(query="Scale Project 042", assignment_group="Fictional Apps", limit=250)
    filtered_ms = (time.perf_counter() - start) * 1000
    assert filtered["total"] == 1
    assert filtered_ms < 250, f"filtered response took {filtered_ms:.1f} ms"

    start = time.perf_counter()
    detail = service.get_project(projects[0][0])
    project_ms = (time.perf_counter() - start) * 1000
    assert len(detail["updates"]) == 20 and len(detail["action_items"]) == 8
    assert project_ms < 300, f"project response took {project_ms:.1f} ms"

    start = time.perf_counter()
    chunks = service.db.search_chunks(projects[0][0], "validation evidence", limit=12)
    retrieval_ms = (time.perf_counter() - start) * 1000
    assert len(chunks) == 12
    assert retrieval_ms < 500, f"FTS retrieval took {retrieval_ms:.1f} ms"
    print(
        f"scale timings ms: board={board_ms:.1f}, filter={filtered_ms:.1f}, "
        f"project={project_ms:.1f}, retrieval={retrieval_ms:.1f}"
    )

    with service.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM source_chunks").fetchone()[0] == 50_000
        assert connection.execute("SELECT count(*) FROM project_updates").fetchone()[0] == 5_000
        assert connection.execute("SELECT count(*) FROM action_items").fetchone()[0] == 2_000
