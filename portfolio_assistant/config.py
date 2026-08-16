from __future__ import annotations

import math
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .archive import ensure_archive_roots


class ConfigurationError(ValueError):
    pass


def _expanded_path(value: str) -> Path:
    expanded = os.path.expandvars(value)
    return Path(expanded).expanduser().resolve()


def diagnostic_log_path(app: "AppSettings") -> Path:
    """Single source of truth for the diagnostic log location.

    Both the writer (api._configure_diagnostic_log) and the reporter
    (services.configuration_status) derive the path from here, so the
    status endpoint can never name a file the app does not write.
    """
    return app.database_path.parent / "logs" / "assistant.log"


def _coerced(section: str, key: str, value: Any, converter: type):
    """Convert one setting, turning a bad scalar into an actionable error.

    int("8765x") and float("fast") raise bare ValueError, which nothing in
    cli.main translates, so a config typo printed a traceback instead of the
    "Error: ..." line naming the offending key. Booleans and non-finite
    floats convert without raising (int(True) == 1, float("nan")) yet are
    equally wrong, so they are rejected explicitly.
    """
    invalid = ConfigurationError(
        f"Invalid [{section}] value for {key}: {value!r} is not a valid {converter.__name__}"
    )
    if isinstance(value, bool) and converter in (int, float):
        raise invalid
    try:
        result = converter(value)
    except (TypeError, ValueError) as exc:
        raise invalid from exc
    if converter is float and not math.isfinite(result):
        raise invalid
    return result


# Defaults live on the dataclasses alone; only keys present in the TOML are
# passed through, so a default can never drift between two statements.
_APP_FIELDS: dict[str, type] = {
    "bind_host": str, "bind_port": int, "max_file_mb": int, "max_attachments": int,
    "max_extracted_text_mb": int, "daily_run_time": str, "worker_poll_seconds": float,
    "automatic_ai_attempts": int,
}
_LLM_FIELDS: dict[str, type] = {
    "adapter": str, "base_url": str, "chat_path": str, "model": str,
    "judgment_model": str, "api_key_env": str, "auth_header": str, "auth_scheme": str,
    "ca_bundle": str, "timeout_seconds": float, "max_tokens": int, "max_attempts": int,
    "rate_limit_requests": int, "rate_limit_window_seconds": float,
    "max_evidence_chars": int,
}


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
    model: str = "gemini-3.7-flash"
    judgment_model: str = "gemini-3.7-flash"
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
    app_values: dict[str, Any] = {"testing": testing}
    try:
        app_values["database_path"] = _expanded_path(str(app_raw["database_path"]))
        app_values["one_drive_root"] = _expanded_path(str(app_raw["one_drive_root"]))
    except KeyError as exc:
        raise ConfigurationError(f"Missing required [app] key: {exc.args[0]}") from exc
    for key, converter in _APP_FIELDS.items():
        if key in app_raw:
            app_values[key] = _coerced("app", key, app_raw[key], converter)
    app = AppSettings(**app_values)
    llm = LlmSettings(**{
        key: _coerced("llm", key, llm_raw[key], converter)
        for key, converter in _LLM_FIELDS.items() if key in llm_raw
    })
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
