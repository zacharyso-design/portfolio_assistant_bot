from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SOURCE = ROOT / "frontend" / "src"


def _typescript_sources() -> list[Path]:
    return sorted((*FRONTEND_SOURCE.rglob("*.ts"), *FRONTEND_SOURCE.rglob("*.tsx")))


def test_frontend_has_replaceable_module_boundaries() -> None:
    expected = {"api", "app", "components", "features", "lib", "styles"}
    assert expected.issubset({path.name for path in FRONTEND_SOURCE.iterdir() if path.is_dir()})
    assert not (FRONTEND_SOURCE / "App.tsx").exists()
    assert (FRONTEND_SOURCE.parent / "README.md").is_file()


def test_api_urls_stay_inside_the_api_boundary() -> None:
    api_root = FRONTEND_SOURCE / "api"
    endpoint_literal = re.compile(r'''["'`]\/api\/''')
    violations = []
    for source in _typescript_sources():
        if source.is_relative_to(api_root):
            continue
        if endpoint_literal.search(source.read_text(encoding="utf-8")):
            violations.append(source.relative_to(ROOT).as_posix())
    assert violations == []


def test_raw_http_client_is_not_imported_by_ui_modules() -> None:
    allowed = {FRONTEND_SOURCE / "api" / "backend.ts", FRONTEND_SOURCE / "api" / "client.ts"}
    violations = []
    for source in _typescript_sources():
        if source in allowed:
            continue
        if "apiClient" in source.read_text(encoding="utf-8"):
            violations.append(source.relative_to(ROOT).as_posix())
    assert violations == []


def test_feature_modules_remain_reviewable() -> None:
    oversized = {}
    for source in (FRONTEND_SOURCE / "features").rglob("*.tsx"):
        line_count = len(source.read_text(encoding="utf-8").splitlines())
        if line_count > 250:
            oversized[source.relative_to(ROOT).as_posix()] = line_count
    assert oversized == {}
