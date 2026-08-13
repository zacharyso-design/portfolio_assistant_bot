from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio_assistant.api import create_app
from portfolio_assistant.config import AppSettings, LlmSettings, Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    return Settings(
        app=AppSettings(
            database_path=tmp_path / "local-data" / "portfolio.db",
            one_drive_root=one_drive,
            bind_host="127.0.0.1",
            bind_port=8765,
            max_file_mb=2,
            max_attachments=5,
            max_extracted_text_mb=1,
            worker_poll_seconds=0.05,
            automatic_ai_attempts=2,
            testing=True,
        ),
        llm=LlmSettings(adapter="fake", model="fake-llm-v1", max_evidence_chars=30_000),
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(
        app, base_url="http://127.0.0.1:8765",
        headers={"X-Requested-With": "CHIO-Portfolio-Assistant"},
    ) as test_client:
        yield test_client


@pytest.fixture
def service(app):
    return app.state.service


@pytest.fixture
def project(client: TestClient):
    response = client.post("/api/projects", json={"name": "Fictional Atlas Modernization"})
    assert response.status_code == 201
    return response.json()
