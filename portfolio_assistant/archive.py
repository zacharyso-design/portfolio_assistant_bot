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
LEGACY_WINDOWS_MAX_PATH = 259
ATOMIC_TEMP_COMPONENT_LENGTH = len(".tmp-") + 12


def windows_path_units(value: str | Path) -> int:
    """Measure a path the way legacy Win32 MAX_PATH does: UTF-16 code units."""
    return len(str(value).encode("utf-16-le")) // 2


def truncate_windows_units(value: str, maximum: int) -> str:
    if maximum < 1:
        return ""
    used = 0
    result: list[str] = []
    for character in value:
        units = windows_path_units(character)
        if used + units > maximum:
            break
        result.append(character)
        used += units
    return "".join(result)


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


def legacy_component_limit(
    parent: Path, *, reserved_tail: int, desired: int, minimum: int,
) -> int:
    """Return a safe component budget for Windows hosts without long-path support."""
    available = (
        LEGACY_WINDOWS_MAX_PATH - windows_path_units(parent.resolve()) - 1 - reserved_tail
    )
    if available < minimum:
        raise ValueError(
            "The configured OneDrive path is too long for this archive operation on Windows"
        )
    return min(desired, available)


def project_folder_name(name: str, archive_id: str, *, max_length: int = 48) -> str:
    readable = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .")
    suffix = f"__{archive_id}"
    readable_limit = max_length - len(suffix)
    if readable_limit < 1:
        if max_length >= len(archive_id):
            return archive_id
        raise ValueError("Project archive component budget is too small")
    readable = (truncate_windows_units(readable, readable_limit).rstrip(" .") or "P")
    return f"{readable}{suffix}"


def ingestion_folder_name(
    created_at: datetime, source_type: str, title: str, ingestion_id: str, *,
    max_length: int = 64,
) -> str:
    timestamp = f"{created_at:%Y%m%d-%H%M%S}"
    compact = f"{timestamp}__{ingestion_id}"
    if max_length < len(compact):
        raise ValueError("Source archive component budget is too small")
    readable_budget = max_length - len(compact) - 2
    if readable_budget < 1:
        return compact
    readable = short_description(f"{source_type}-{title}", "source", readable_budget)
    return f"{timestamp}__{readable}__{ingestion_id}"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".tmp-{uuid.uuid4().hex[:12]}"
    if (
        windows_path_units(path.resolve()) > LEGACY_WINDOWS_MAX_PATH
        or windows_path_units(temporary.resolve()) > LEGACY_WINDOWS_MAX_PATH
    ):
        raise OSError("Generated archive path exceeds the legacy Windows path limit")
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
