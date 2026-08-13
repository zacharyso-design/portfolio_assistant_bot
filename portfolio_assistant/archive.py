from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


ARCHIVE_DIRECTORY = "CHIO Portfolio Assistant"
SCHEMA_VERSION = 1


def archive_root(one_drive_root: Path) -> Path:
    return one_drive_root / ARCHIVE_DIRECTORY


def ensure_archive_roots(one_drive_root: Path) -> dict[str, Path]:
    root = archive_root(one_drive_root)
    paths = {
        "root": root,
        "projects": root / "Projects",
        "shared_intake": root / "Shared Intake",
        "archive": root / "Archive",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def stable_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def short_description(value: str, fallback: str = "source", limit: int = 54) -> str:
    name = Path(value or fallback).stem
    clean = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return (clean or fallback)[:limit].rstrip("-")


def project_folder_name(name: str, archive_id: str) -> str:
    readable = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .")
    readable = (readable[:80].rstrip(" .") or "Project")
    return f"{readable}__{archive_id}"


def ingestion_folder_name(created_at: datetime, source_type: str, title: str, ingestion_id: str) -> str:
    return (
        f"{created_at:%Y-%m-%d_%H%M%S}__{short_description(source_type, 'source', 28)}__"
        f"{short_description(title)}__{ingestion_id}"
    )


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def relative_to_root(path: Path, one_drive_root: Path) -> str:
    return path.resolve().relative_to(one_drive_root.resolve()).as_posix()


def read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return fallback


def write_project_files(
    project_folder: Path,
    *,
    project_id: str,
    archive_id: str,
    name: str,
    created_at: str,
) -> None:
    assistant = project_folder / "_Assistant"
    (assistant / "living-summary" / "versions").mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        assistant / "project.json",
        {
            "schema_version": SCHEMA_VERSION,
            "project_id": project_id,
            "archive_id": archive_id,
            "name": name,
            "created_at": created_at,
        },
    )
    history = assistant / "knowledge-history.jsonl"
    if not history.exists():
        atomic_write_text(history, "")


def update_manifest(package: Path, **updates: Any) -> dict[str, Any]:
    path = package / "manifest.json"
    manifest = read_json(path, {})
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.update(updates)
    atomic_write_json(path, manifest)
    return manifest
