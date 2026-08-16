from __future__ import annotations

import re
from pathlib import Path

import pytest


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


def test_dist_references_resolve_to_files() -> None:
    # On a clean checkout an untracked bundle simply does not exist, so this
    # catches a rebuilt dist whose new asset never made it into the commit -
    # index.html would point at a 404 and the SPA would never start.
    dist = FRONTEND_SOURCE.parent / "dist"
    index_html = (dist / "index.html").read_text(encoding="utf-8")
    references = re.findall(r'(?:src|href)="/(assets/[^"]+)"', index_html)
    assert references, "dist/index.html must reference at least one built asset"
    missing = [ref for ref in references if not (dist / ref).is_file()]
    assert missing == []


def test_dist_references_are_tracked_by_git() -> None:
    # Existence in the working tree is not enough: a freshly built bundle can
    # sit untracked while the commit deletes its predecessor, which ships an
    # index.html pointing at nothing. git ls-files sees the index, so a staged
    # bundle passes and a forgotten one fails before it can reach CI.
    import subprocess

    dist = FRONTEND_SOURCE.parent / "dist"
    index_html = (dist / "index.html").read_text(encoding="utf-8")
    references = re.findall(r'(?:src|href)="/(assets/[^"]+)"', index_html)
    result = subprocess.run(
        ["git", "ls-files", "--", "frontend/dist"],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    if result.returncode != 0:
        pytest.skip("git unavailable; the existence check above still applies")
    tracked = set(result.stdout.split())
    untracked = [
        ref for ref in references if f"frontend/dist/{ref}" not in tracked
    ]
    assert untracked == []


def test_bulk_dismiss_allowlists_stay_in_sync() -> None:
    # The backend rejects kinds outside its allowlist with a 422; the frontend
    # only offers buttons for its own list. Drift in either direction is a
    # broken button or a hidden capability.
    from portfolio_assistant.services import PortfolioService

    bulk_dismiss = (FRONTEND_SOURCE / "features" / "review" / "bulkDismiss.ts").read_text(encoding="utf-8")
    match = re.search(r"BULK_DISMISSABLE_KINDS = new Set\(\[(.*?)\]\)", bulk_dismiss, re.DOTALL)
    assert match, "frontend allowlist must remain statically extractable for this contract test"
    frontend_kinds = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert frontend_kinds == set(PortfolioService.BULK_DISMISSABLE_REVIEW_KINDS)
