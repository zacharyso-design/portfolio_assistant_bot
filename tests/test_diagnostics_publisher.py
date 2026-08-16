"""Publisher tests: fake HTTP client, no network, no real GitHub."""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

import httpx
import pytest

from portfolio_assistant.config import DiagnosticsSettings, diagnostic_log_path
from portfolio_assistant.diagnostics import DiagnosticLogPublisher, start_publisher


class FakeGitHub:
    def __init__(
        self, existing_sha: str | None = None, fail: Any = None, *,
        repo_private: bool = True,
    ):
        self.existing_sha = existing_sha
        self.fail = fail
        self.repo_private = repo_private
        self.puts: list[dict[str, Any]] = []
        self.gets: list[str] = []

    def factory(self, **_kwargs: Any) -> "FakeGitHub":
        return self

    def __enter__(self) -> "FakeGitHub":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.gets.append(url)
        if isinstance(self.fail, Exception):
            raise self.fail
        if "/contents/" not in url:
            # Repository metadata: the privacy check the publisher performs
            # before uploading anything.
            return httpx.Response(200, json={"private": self.repo_private})
        if self.existing_sha:
            return httpx.Response(200, json={"sha": self.existing_sha})
        return httpx.Response(404, json={"message": "Not Found"})

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        if isinstance(self.fail, Exception):
            raise self.fail
        self.puts.append({"url": url, **kwargs})
        return httpx.Response(201, json={"content": {"sha": "new-sha"}})


def _publisher(settings, github: FakeGitHub, monkeypatch, token: str = "fictional-token"):
    configured = replace(
        settings,
        diagnostics=DiagnosticsSettings(repo="fictional-owner/fictional-diagnostics"),
    )
    monkeypatch.setenv(configured.diagnostics.token_env, token)
    publisher = DiagnosticLogPublisher(
        configured, client_factory=github.factory, hostname="fictional-gfe"
    )
    return publisher


def _write_log(settings, text: str) -> None:
    path = diagnostic_log_path(settings.app)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_first_publish_creates_the_remote_file(settings, monkeypatch):
    github = FakeGitHub()
    publisher = _publisher(settings, github, monkeypatch)
    _write_log(settings, "fictional log line\n")
    assert publisher.publish_once() is True
    assert len(github.puts) == 1
    put = github.puts[0]
    assert "logs/fictional-gfe/assistant.log" in put["url"]
    assert "sha" not in put["json"]


def test_update_supplies_the_existing_sha(settings, monkeypatch):
    github = FakeGitHub(existing_sha="prior-sha")
    publisher = _publisher(settings, github, monkeypatch)
    _write_log(settings, "fictional log line\n")
    assert publisher.publish_once() is True
    assert github.puts[0]["json"]["sha"] == "prior-sha"


def test_unchanged_log_is_not_republished(settings, monkeypatch):
    github = FakeGitHub()
    publisher = _publisher(settings, github, monkeypatch)
    _write_log(settings, "fictional log line\n")
    assert publisher.publish_once() is True
    assert publisher.publish_once() is False
    assert len(github.puts) == 1


def test_missing_token_disables_publishing(settings, monkeypatch):
    github = FakeGitHub()
    publisher = _publisher(settings, github, monkeypatch, token="")
    _write_log(settings, "fictional log line\n")
    assert publisher.publish_once() is False
    assert github.puts == []


def test_network_failure_is_quiet_and_retryable(settings, monkeypatch):
    github = FakeGitHub(fail=httpx.ConnectError("fictional outage"))
    publisher = _publisher(settings, github, monkeypatch)
    _write_log(settings, "fictional log line\n")
    assert publisher.publish_once() is False
    github.fail = None
    assert publisher.publish_once() is True


def test_large_logs_publish_only_the_tail(settings, monkeypatch):
    import base64

    github = FakeGitHub()
    publisher = _publisher(settings, github, monkeypatch)
    _write_log(settings, "x" * (600 * 1024))
    assert publisher.publish_once() is True
    payload = base64.b64decode(github.puts[0]["json"]["content"])
    assert len(payload) <= 512 * 1024 + 64
    assert payload.startswith(b"[... earlier log truncated ...]")


def test_tail_starts_on_a_complete_line(settings, monkeypatch):
    import base64

    github = FakeGitHub()
    publisher = _publisher(settings, github, monkeypatch)
    # Multibyte characters ensure a byte-offset cut would split mid-character.
    _write_log(settings, ("café fictional line  padding\n" * 40_000))
    assert publisher.publish_once() is True
    payload = base64.b64decode(github.puts[0]["json"]["content"])
    body = payload.split(b"\n", 1)[1]
    # Decodable and starting at a record boundary, not mid-character.
    assert body.decode("utf-8").startswith("café fictional line ")


def test_public_repository_is_refused_permanently(settings, monkeypatch):
    github = FakeGitHub(repo_private=False)
    publisher = _publisher(settings, github, monkeypatch)
    _write_log(settings, "fictional log line\n")
    assert publisher.publish_once() is False
    assert github.puts == []
    # Refusal is permanent: no further network attempts for a misconfigured
    # destination.
    assert publisher.should_publish() is False


def test_hostname_is_url_encoded(settings, monkeypatch):
    github = FakeGitHub()
    configured = replace(
        settings,
        diagnostics=DiagnosticsSettings(repo="fictional-owner/fictional-diagnostics"),
    )
    monkeypatch.setenv(configured.diagnostics.token_env, "fictional-token")
    publisher = DiagnosticLogPublisher(
        configured, client_factory=github.factory, hostname="GFE HOST/7"
    )
    _write_log(settings, "fictional log line\n")
    assert publisher.publish_once() is True
    assert "logs/GFE%20HOST%2F7/assistant.log" in github.puts[0]["url"]


def test_run_publishes_immediately_and_flushes_on_stop(settings, monkeypatch):
    github = FakeGitHub()
    publisher = _publisher(settings, github, monkeypatch)
    _write_log(settings, "first line\n")
    stop = threading.Event()
    stop.set()  # loop body never runs; only the startup and final attempts
    publisher.run(stop)
    assert len(github.puts) == 1  # startup publish; final flush saw no change
    _write_log(settings, "first line\nsecond line\n")
    publisher.run(stop)
    assert len(github.puts) == 2  # the changed file flushed again


def test_start_publisher_stays_off_without_configuration(settings):
    assert start_publisher(settings, threading.Event()) is None


def test_start_publisher_stays_off_in_testing_mode(settings, monkeypatch):
    configured = replace(
        settings, diagnostics=DiagnosticsSettings(repo="fictional-owner/fictional-diagnostics")
    )
    monkeypatch.setenv(configured.diagnostics.token_env, "fictional-token")
    # conftest settings have testing=True; the publisher must respect that.
    assert start_publisher(configured, threading.Event()) is None


def test_repo_format_is_validated(tmp_path):
    from portfolio_assistant.config import ConfigurationError, load_settings

    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        "[app]\n"
        f'database_path = "{(tmp_path / "portfolio.db").as_posix()}"\n'
        f'one_drive_root = "{one_drive.as_posix()}"\n'
        "[diagnostics]\n"
        'repo = "not-a-repo-path"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="diagnostics.repo"):
        load_settings(config)


def test_interval_floor_is_validated(tmp_path):
    from portfolio_assistant.config import ConfigurationError, load_settings

    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        "[app]\n"
        f'database_path = "{(tmp_path / "portfolio.db").as_posix()}"\n'
        f'one_drive_root = "{one_drive.as_posix()}"\n'
        "[diagnostics]\n"
        'repo = "fictional-owner/fictional-diagnostics"\n'
        "publish_interval_seconds = 5\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="publish_interval_seconds"):
        load_settings(config)
