from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .archive import ensure_archive_roots


class ConfigurationError(ValueError):
    pass


def _expanded_path(value: str) -> Path:
    expanded = os.path.expandvars(value)
    return Path(expanded).expanduser().resolve()


@dataclass(frozen=True)
class AppSettings:
    database_path: Path
    one_drive_root: Path
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    max_file_mb: int = 100
    max_attachments: int = 25
    max_extracted_text_mb: int = 5
    daily_run_time: str = "06:00"
    worker_poll_seconds: float = 2.0
    automatic_ai_attempts: int = 2
    testing: bool = False


@dataclass(frozen=True)
class LlmSettings:
    adapter: str = "internal"
    base_url: str = "https://api.genai.mil"
    chat_path: str = "/v1/chat/completions"
    model: str = "gemini-3.5-flash"
    judgment_model: str = "gemini-3.1-pro-preview"
    api_key_env: str = "GENAI_API_KEY"
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    ca_bundle: str = ""
    timeout_seconds: float = 240.0
    max_tokens: int = 32_000
    max_attempts: int = 3
    rate_limit_requests: int = 120
    rate_limit_window_seconds: float = 60.0
    max_evidence_chars: int = 30_000


@dataclass(frozen=True)
class Settings:
    app: AppSettings
    llm: LlmSettings
    config_path: Path | None = None

    @property
    def static_dir(self) -> Path:
        if getattr(sys, "_MEIPASS", None):
            return Path(sys._MEIPASS) / "frontend" / "dist"
        return Path(__file__).resolve().parents[1] / "frontend" / "dist"


def load_settings(path: str | Path | None = None, *, testing: bool = False) -> Settings:
    selected = Path(path or os.environ.get("PORTFOLIO_ASSISTANT_CONFIG", "config.toml"))
    if not selected.exists():
        raise ConfigurationError(
            f"Configuration file not found: {selected.resolve()}. Copy config.example.toml to config.toml and set one_drive_root."
        )
    with selected.open("rb") as handle:
        raw = tomllib.load(handle)
    app_raw = raw.get("app", {})
    llm_raw = raw.get("llm", {})
    try:
        app = AppSettings(
            database_path=_expanded_path(str(app_raw["database_path"])),
            one_drive_root=_expanded_path(str(app_raw["one_drive_root"])),
            bind_host=str(app_raw.get("bind_host", "127.0.0.1")),
            bind_port=int(app_raw.get("bind_port", 8765)),
            max_file_mb=int(app_raw.get("max_file_mb", 100)),
            max_attachments=int(app_raw.get("max_attachments", 25)),
            max_extracted_text_mb=int(app_raw.get("max_extracted_text_mb", 5)),
            daily_run_time=str(app_raw.get("daily_run_time", "06:00")),
            worker_poll_seconds=float(app_raw.get("worker_poll_seconds", 2)),
            automatic_ai_attempts=int(app_raw.get("automatic_ai_attempts", 2)),
            testing=testing,
        )
    except KeyError as exc:
        raise ConfigurationError(f"Missing required [app] key: {exc.args[0]}") from exc
    llm = LlmSettings(
        adapter=str(llm_raw.get("adapter", "internal")),
        base_url=str(llm_raw.get("base_url", "https://api.genai.mil")),
        chat_path=str(llm_raw.get("chat_path", "/v1/chat/completions")),
        model=str(llm_raw.get("model", "gemini-3.5-flash")),
        judgment_model=str(llm_raw.get("judgment_model", "gemini-3.1-pro-preview")),
        api_key_env=str(llm_raw.get("api_key_env", "GENAI_API_KEY")),
        auth_header=str(llm_raw.get("auth_header", "Authorization")),
        auth_scheme=str(llm_raw.get("auth_scheme", "Bearer")),
        ca_bundle=str(llm_raw.get("ca_bundle", "")),
        timeout_seconds=float(llm_raw.get("timeout_seconds", 240)),
        max_tokens=int(llm_raw.get("max_tokens", 32_000)),
        max_attempts=int(llm_raw.get("max_attempts", 3)),
        rate_limit_requests=int(llm_raw.get("rate_limit_requests", 120)),
        rate_limit_window_seconds=float(llm_raw.get("rate_limit_window_seconds", 60)),
        max_evidence_chars=int(llm_raw.get("max_evidence_chars", 30_000)),
    )
    _validate(app, llm)
    return Settings(app=app, llm=llm, config_path=selected.resolve())


def _validate(app: AppSettings, llm: LlmSettings) -> None:
    if app.bind_host != "127.0.0.1":
        raise ConfigurationError("bind_host must be exactly 127.0.0.1")
    if not 1 <= app.bind_port <= 65_535:
        raise ConfigurationError("bind_port must be between 1 and 65535")
    if app.max_file_mb < 1 or app.max_attachments < 0 or app.max_extracted_text_mb < 1:
        raise ConfigurationError("file, attachment, and extracted-text limits must be positive")
    if llm.adapter not in {"fake", "internal"}:
        raise ConfigurationError("llm.adapter must be fake or internal")
    if llm.adapter == "fake" and not app.testing and os.environ.get("PORTFOLIO_ASSISTANT_ALLOW_FAKE_LLM") != "1":
        raise ConfigurationError(
            "The fake LLM adapter is test/demo-only; set PORTFOLIO_ASSISTANT_ALLOW_FAKE_LLM=1 to opt in explicitly"
        )
    if llm.adapter == "internal":
        parsed = urlparse(llm.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ConfigurationError("internal llm.base_url must be an absolute HTTPS URL")
        if not llm.model or llm.model == "CONFIGURE_ME":
            raise ConfigurationError("internal llm.model must be configured")
        if not llm.judgment_model or llm.judgment_model == "CONFIGURE_ME":
            raise ConfigurationError("internal llm.judgment_model must be configured")
        if llm.ca_bundle and not _expanded_path(llm.ca_bundle).is_file():
            raise ConfigurationError("configured llm.ca_bundle does not exist")
        if llm.timeout_seconds <= 0 or llm.max_tokens < 1 or not 1 <= llm.max_attempts <= 3:
            raise ConfigurationError("LLM timeout/max_tokens must be positive and max_attempts must be between 1 and 3")
        if llm.rate_limit_requests < 0 or llm.rate_limit_window_seconds <= 0:
            raise ConfigurationError("LLM rate limit must be disabled with 0 requests or use a positive window")


def ensure_runtime_paths(settings: Settings) -> None:
    root = settings.app.one_drive_root
    if not root.exists() or not root.is_dir():
        raise ConfigurationError(f"Configured OneDrive root does not exist: {root}")
    probe = root / ".portfolio-assistant-write-test"
    try:
        probe.write_text("write-test", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ConfigurationError(f"Configured OneDrive root is not writable: {root}") from exc
    settings.app.database_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_archive_roots(root)
    (root / "_PortfolioAssistant" / "imports" / "snow").mkdir(parents=True, exist_ok=True)
    # Retain legacy locations so pre-archive installs can migrate their existing files in place.
    (root / "_PortfolioAssistant" / "intake" / "multi-project").mkdir(parents=True, exist_ok=True)
    (root / "Projects").mkdir(parents=True, exist_ok=True)
