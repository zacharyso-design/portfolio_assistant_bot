from __future__ import annotations

import importlib
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import portfolio_assistant.api as api_module
from portfolio_assistant.config import AppSettings, LlmSettings, Settings
from portfolio_assistant.credentials import CredentialError, WindowsDpapiCredentialStore
from portfolio_assistant.llm import (
    InternalHttpLlmAdapter, LlmContractError, LlmUnavailable,
    SlidingWindowRateLimiter, _json_object_from_text,
)
from portfolio_assistant.preferences import ModelPreferenceError


class MemoryCredentialStore:
    def __init__(self, value: str | None = "test-key"):
        self.value = value

    def get(self) -> str | None:
        return self.value

    def set(self, value: str) -> None:
        if not value.strip():
            raise AssertionError("test store received an empty credential")
        self.value = value.strip()

    def delete(self) -> bool:
        existed = self.value is not None
        self.value = None
        return existed


class MemoryModelPreferenceStore:
    def __init__(self, value: dict[str, str] | None = None):
        self.value = value

    def load(self) -> dict[str, str] | None:
        return self.value

    def save(self, routine_model: str, judgment_model: str) -> None:
        self.value = {
            "routine_model": routine_model,
            "judgment_model": judgment_model,
        }


def completion(
    content: str, status: int = 200, headers: dict[str, str] | None = None,
    model: str | None = None,
) -> httpx.Response:
    payload: dict[str, Any] = {"choices": [{"message": {"content": content}}]}
    if model:
        payload["model"] = model
    return httpx.Response(
        status,
        headers=headers,
        json=payload,
    )


def model_catalog(*model_ids: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "object": "list",
            "data": [{"id": model_id, "object": "model"} for model_id in model_ids],
        },
    )


class SequenceClient:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.options: list[dict[str, Any]] = []

    def factory(self, **kwargs: Any) -> "SequenceClient":
        self.options.append(kwargs)
        return self

    def __enter__(self) -> "SequenceClient":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def settings(**overrides: Any) -> LlmSettings:
    values = {
        "adapter": "internal",
        "base_url": "https://api.genai.mil",
        "chat_path": "/v1/chat/completions",
        "model": "gemini-3.5-flash",
        "judgment_model": "gemini-3.1-pro-preview",
        "api_key_env": "TEST_GENAI_KEY",
        "max_attempts": 3,
        "rate_limit_requests": 0,
    }
    values.update(overrides)
    return LlmSettings(**values)


def adapter_with(
    responses: list[Any], *, llm_settings: LlmSettings | None = None,
    store: MemoryCredentialStore | None = None, sleeper: Any = None,
    model_store: MemoryModelPreferenceStore | None = None,
) -> tuple[InternalHttpLlmAdapter, SequenceClient, list[float]]:
    sequence = SequenceClient(responses)
    sleeps: list[float] = []
    sleep = sleeper or sleeps.append
    adapter = InternalHttpLlmAdapter(
        llm_settings or settings(), credential_store=store or MemoryCredentialStore(),
        model_preference_store=model_store or MemoryModelPreferenceStore(),
        client_factory=sequence.factory, sleeper=sleep,
    )
    return adapter, sequence, sleeps


def test_genai_payload_auth_tls_contract_and_success(monkeypatch):
    monkeypatch.delenv("TEST_GENAI_KEY", raising=False)
    adapter, sequence, _ = adapter_with([completion('{"ok":true}')])
    result = adapter.test_connection()
    assert result["ok"] is True
    call = sequence.calls[0]
    assert call["url"] == "https://api.genai.mil/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["json"]["model"] == "gemini-3.5-flash"
    assert call["json"]["temperature"] == 0.0
    assert call["json"]["max_tokens"] == 32_000
    assert call["json"]["response_format"]["type"] == "json_schema"
    assert "OUTPUT CONTRACT (non-negotiable)" in call["json"]["messages"][1]["content"]
    assert sequence.options[0]["verify"] is True
    assert sequence.options[0]["follow_redirects"] is False


@pytest.mark.parametrize("content", [
    r'{"path":"C:\\Users\\Public","ok":true}',
    '```json\n{"ok": true}\n```',
    'Result follows: {"ok": true} and no more data.',
    r'{"path":"C:\Users\Public","ok":true}',
])
def test_json_parser_accepts_supported_response_variants(content):
    parsed = _json_object_from_text(content)
    assert parsed["ok"] is True
    if "path" in parsed:
        assert parsed["path"] == r"C:\Users\Public"


def test_json_parser_preserves_valid_escapes_and_unicode():
    parsed = _json_object_from_text(r'{"line":"a\nb","snowman":"\u2603","slash":"a\\b"}')
    assert parsed == {"line": "a\nb", "snowman": "☃", "slash": "a\\b"}


def test_json_repair_preserves_valid_backslashes_while_fixing_invalid_path_escape():
    parsed = _json_object_from_text(r'{"valid":"a\\b","invalid":"C:\Users\Public"}')
    assert parsed == {"valid": "a\\b", "invalid": r"C:\Users\Public"}


def test_corrective_retry_includes_invalid_reply_and_exact_correction():
    adapter, sequence, _ = adapter_with([completion("I can help with that."), completion('{"ok":true}')])
    assert adapter.test_connection()["ok"] is True
    second_messages = sequence.calls[1]["json"]["messages"]
    assert len(second_messages) == 4
    assert second_messages[-2] == {"role": "assistant", "content": "I can help with that."}
    assert second_messages[-1]["content"].startswith("Your previous reply was not valid JSON")


def test_invalid_json_stops_after_three_total_attempts():
    adapter, sequence, _ = adapter_with([completion("no") for _ in range(3)])
    with pytest.raises(LlmContractError, match="corrective retries"):
        adapter.test_connection()
    assert len(sequence.calls) == 3


def test_malformed_200_is_protocol_error_not_success():
    malformed = httpx.Response(200, json={"result": {"ok": True}})
    adapter, sequence, _ = adapter_with([malformed])
    with pytest.raises(LlmContractError, match="malformed OpenAI-compatible response"):
        adapter.test_connection()
    assert len(sequence.calls) == 1


def test_json_schema_400_falls_back_and_is_cached():
    adapter, sequence, _ = adapter_with([
        httpx.Response(400, json={"error": "json_schema response_format unsupported"}),
        completion('{"ok":true}'), completion('{"ok":true}'),
    ])
    assert adapter.test_connection()["ok"] is True
    assert adapter.test_connection()["ok"] is True
    assert [call["json"]["response_format"]["type"] for call in sequence.calls] == [
        "json_schema", "json_object", "json_object",
    ]


def test_non_schema_400_fails_immediately_without_disabling_schema_mode():
    adapter, sequence, _ = adapter_with([httpx.Response(400, json={"error": "invalid model input"})])
    with pytest.raises(LlmUnavailable, match="HTTP 400"):
        adapter.test_connection()
    assert len(sequence.calls) == 1
    assert adapter._schema_supported is True


def test_429_uses_retry_after_then_succeeds():
    adapter, sequence, sleeps = adapter_with([
        httpx.Response(429, headers={"retry-after": "2"}), completion('{"ok":true}'),
    ])
    assert adapter.test_connection()["ok"] is True
    assert sleeps == [2.0]
    assert len(sequence.calls) == 2


def test_retry_after_is_capped_to_keep_worker_responsive():
    adapter, _, sleeps = adapter_with([
        httpx.Response(429, headers={"retry-after": "3600"}), completion('{"ok":true}'),
    ])
    assert adapter.test_connection()["ok"] is True
    assert sleeps == [60.0]


def test_5xx_retries_with_bounded_backoff():
    adapter, _, sleeps = adapter_with([
        httpx.Response(503), httpx.Response(502), completion('{"ok":true}'),
    ])
    assert adapter.test_connection()["ok"] is True
    assert sleeps == [5.0, 10.0]


@pytest.mark.parametrize("status", [401, 403, 404])
def test_permanent_http_errors_fail_without_retry(status):
    adapter, sequence, sleeps = adapter_with([httpx.Response(status)])
    with pytest.raises(LlmUnavailable, match=f"HTTP {status}"):
        adapter.test_connection()
    assert len(sequence.calls) == 1
    assert sleeps == []


def test_network_failure_retries_and_does_not_log_key(caplog):
    key = "highly-sensitive-test-key"
    adapter, sequence, sleeps = adapter_with([
        httpx.ConnectError("offline"), completion('{"ok":true}'),
    ], store=MemoryCredentialStore(key))
    with caplog.at_level(logging.WARNING):
        assert adapter.test_connection()["ok"] is True
    assert len(sequence.calls) == 2
    assert sleeps == [5.0]
    assert key not in caplog.text


def test_encrypted_store_has_priority_over_environment_fallback(monkeypatch):
    monkeypatch.setenv("TEST_GENAI_KEY", "environment-key")
    adapter, sequence, _ = adapter_with([completion('{"ok":true}')], store=MemoryCredentialStore("stored-key"))
    adapter.test_connection()
    assert sequence.calls[0]["headers"]["Authorization"] == "Bearer stored-key"
    assert adapter.credential_status() == {
        "configured": True, "source": "encrypted_local", "environment_override": False,
        "local_key_present": True,
    }


def test_environment_key_remains_a_fallback_when_no_local_key_exists(monkeypatch):
    monkeypatch.setenv("TEST_GENAI_KEY", "environment-key")
    adapter, sequence, _ = adapter_with(
        [completion('{"ok":true}')], store=MemoryCredentialStore(None),
    )
    adapter.test_connection()
    assert sequence.calls[0]["headers"]["Authorization"] == "Bearer environment-key"
    assert adapter.credential_status()["source"] == "environment"


@pytest.mark.parametrize(
    ("stored_key", "environment_key", "expected_source"),
    [
        ("stored-secret", "stale-environment-secret", "API key saved in Settings"),
        (None, "environment-secret", "TEST_GENAI_KEY environment fallback"),
    ],
)
def test_401_identifies_the_active_credential_source_without_exposing_keys(
    monkeypatch, stored_key, environment_key, expected_source,
):
    monkeypatch.setenv("TEST_GENAI_KEY", environment_key)
    adapter, _, _ = adapter_with([httpx.Response(401)], store=MemoryCredentialStore(stored_key))
    with pytest.raises(LlmUnavailable) as rejected:
        adapter.test_connection()
    message = str(rejected.value)
    assert expected_source in message
    assert "HTTP 401" in message
    assert stored_key is None or stored_key not in message
    assert environment_key not in message


def test_judgment_tasks_use_pro_model():
    adapter, sequence, _ = adapter_with([completion('{"segments":[]}')])
    adapter.route([], [])
    assert sequence.calls[0]["json"]["model"] == "gemini-3.1-pro-preview"


def test_project_fit_uses_pro_model():
    adapter, sequence, _ = adapter_with([completion('{"selected_project_confidence":1,"recommended_project_id":"p1","confidence":1,"needs_review":false,"reason":"match","citations":[]}')])
    adapter.project_fit({"id": "p1"}, [], [{"id": "p1"}])
    assert sequence.calls[0]["json"]["model"] == "gemini-3.1-pro-preview"


def test_health_reports_endpoint_model_when_present():
    adapter, _, _ = adapter_with([completion('{"ok":true}', model="served-model-version")])
    health = adapter.test_connection()
    assert health["model_id"] == "served-model-version"
    assert health["configured_model"] == "gemini-3.5-flash"


def test_json_model_preferences_round_trip_atomically(tmp_path: Path):
    try:
        preferences = importlib.import_module("portfolio_assistant.preferences")
    except ModuleNotFoundError:
        pytest.fail("model preference storage is not implemented")
    path = tmp_path / "settings" / "llm-models.json"
    store = preferences.JsonModelPreferenceStore(path)
    assert store.load() is None
    store.save("routine-model", "judgment-model")
    assert store.load() == {
        "routine_model": "routine-model",
        "judgment_model": "judgment-model",
    }
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "routine_model": "routine-model",
        "judgment_model": "judgment-model",
    }
    assert list(path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("routine_model", ["bad\nmodel", "x" * 257])
def test_json_model_preferences_reject_invalid_persisted_ids(tmp_path: Path, routine_model: str):
    preferences = importlib.import_module("portfolio_assistant.preferences")
    path = tmp_path / "settings" / "llm-models.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "routine_model": routine_model,
        "judgment_model": "valid-judgment-model",
    }), encoding="utf-8")
    with pytest.raises(ModelPreferenceError, match="could not be read"):
        preferences.JsonModelPreferenceStore(path).load()


def test_saved_model_preferences_override_toml_and_apply_without_restart():
    model_store = MemoryModelPreferenceStore({
        "routine_model": "saved-routine",
        "judgment_model": "saved-judgment",
    })
    adapter, _, _ = adapter_with([], model_store=model_store)
    assert adapter.model_id == "saved-routine"
    assert adapter.model_for("multi_project_routing") == "saved-judgment"
    saved = adapter.save_models("new-routine", "new-judgment")
    assert saved == {
        "routine_model": "new-routine",
        "judgment_model": "new-judgment",
    }
    assert model_store.value == saved
    assert adapter.model_id == "new-routine"
    assert adapter.model_for("project_fit_check") == "new-judgment"


def test_saved_routine_model_applies_to_image_analysis():
    model_store = MemoryModelPreferenceStore({
        "routine_model": "saved-routine",
        "judgment_model": "saved-judgment",
    })
    adapter, sequence, _ = adapter_with(
        [completion('{"description":"image"}')], model_store=model_store,
    )
    adapter.analyze_image(b"image-bytes", "image/png", "Describe the image")
    assert sequence.calls[0]["json"]["model"] == "saved-routine"


def test_model_catalog_uses_active_key_and_same_host_models_endpoint(monkeypatch):
    monkeypatch.setenv("TEST_GENAI_KEY", "stale-environment-key")
    adapter, sequence, _ = adapter_with(
        [model_catalog("z-model", "a-model", "z-model")],
        store=MemoryCredentialStore("saved-key"),
    )
    assert adapter.list_models() == ["a-model", "z-model"]
    call = sequence.calls[0]
    assert call == {
        "method": "GET",
        "url": "https://api.genai.mil/v1/models",
        "headers": {"Authorization": "Bearer saved-key", "Accept": "application/json"},
    }
    assert sequence.options[0]["verify"] is True
    assert sequence.options[0]["follow_redirects"] is False
    assert sequence.options[0]["timeout"] == 15.0


def test_model_catalog_api_fetches_once_and_reuses_the_session_cache(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    app_settings = Settings(
        app=AppSettings(
            database_path=tmp_path / "portfolio.db", one_drive_root=one_drive,
            testing=True,
        ),
        llm=settings(),
    )
    sequence = SequenceClient([model_catalog("new-model", "gemini-3.5-flash")])
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=MemoryCredentialStore("saved-key"),
        model_preference_store=MemoryModelPreferenceStore(),
        client_factory=sequence.factory, sleeper=lambda _: None,
    )
    monkeypatch.setattr(api_module, "build_adapter", lambda _: injected)
    with TestClient(
        api_module.create_app(app_settings), base_url="http://127.0.0.1:8765",
        headers={"X-Requested-With": "CHIO-Portfolio-Assistant"},
    ) as client:
        first = client.get("/api/llm/models")
        second = client.get("/api/llm/models")

    assert first.status_code == 200
    assert first.json() == {
        "ok": True,
        "configured": True,
        "available_models": ["gemini-3.5-flash", "new-model"],
    }
    assert second.json() == first.json()
    assert [call["method"] for call in sequence.calls] == ["GET"]


def test_app_start_refreshes_model_catalog_without_a_chat_probe(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    app_settings = Settings(
        app=AppSettings(
            database_path=tmp_path / "portfolio.db", one_drive_root=one_drive,
            worker_poll_seconds=0.05, testing=False,
        ),
        llm=settings(),
    )
    catalog_received = threading.Event()

    class SignalingSequenceClient(SequenceClient):
        def get(self, url: str, **kwargs: Any) -> httpx.Response:
            response = super().get(url, **kwargs)
            catalog_received.set()
            return response

    sequence = SignalingSequenceClient([model_catalog("startup-model")])
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=MemoryCredentialStore("saved-key"),
        model_preference_store=MemoryModelPreferenceStore(),
        client_factory=sequence.factory, sleeper=lambda _: None,
    )
    monkeypatch.setattr(api_module, "build_adapter", lambda _: injected)
    with TestClient(
        api_module.create_app(app_settings), base_url="http://127.0.0.1:8765",
        headers={"X-Requested-With": "CHIO-Portfolio-Assistant"},
    ) as client:
        assert client.get("/api/health").status_code == 200
        assert catalog_received.wait(timeout=2)

    assert [call["method"] for call in sequence.calls] == ["GET"]


def test_saving_a_replacement_key_invalidates_the_model_catalog_cache(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    app_settings = Settings(
        app=AppSettings(
            database_path=tmp_path / "portfolio.db", one_drive_root=one_drive,
            testing=True,
        ),
        llm=settings(),
    )
    sequence = SequenceClient([
        model_catalog("old-entitlement"),
        model_catalog("new-entitlement"),
    ])
    store = MemoryCredentialStore("old-key")
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=store,
        model_preference_store=MemoryModelPreferenceStore(),
        client_factory=sequence.factory, sleeper=lambda _: None,
    )
    monkeypatch.setattr(api_module, "build_adapter", lambda _: injected)
    with TestClient(
        api_module.create_app(app_settings), base_url="http://127.0.0.1:8765",
        headers={"X-Requested-With": "CHIO-Portfolio-Assistant"},
    ) as client:
        assert client.get("/api/llm/models").json()["available_models"] == ["old-entitlement"]
        assert client.put("/api/llm/credential", json={"api_key": "new-key"}).status_code == 200
        assert client.get("/api/llm/models").json()["available_models"] == ["new-entitlement"]

    assert store.value == "new-key"
    assert [call["method"] for call in sequence.calls] == ["GET", "GET"]


def test_credential_replacement_does_not_wait_for_or_cache_an_old_key_refresh(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    app_settings = Settings(
        app=AppSettings(
            database_path=tmp_path / "portfolio.db", one_drive_root=one_drive,
            testing=True,
        ),
        llm=settings(),
    )
    catalog_started = threading.Event()
    release_catalog = threading.Event()

    class BlockingCatalogClient(SequenceClient):
        def get(self, url: str, **kwargs: Any) -> httpx.Response:
            self.calls.append({"method": "GET", "url": url, **kwargs})
            if len(self.calls) == 1:
                catalog_started.set()
                assert release_catalog.wait(timeout=2)
                return model_catalog("old-key-model")
            return model_catalog("new-key-model")

    sequence = BlockingCatalogClient([])
    store = MemoryCredentialStore("old-key")
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=store,
        model_preference_store=MemoryModelPreferenceStore(),
        client_factory=sequence.factory, sleeper=lambda _: None,
    )
    monkeypatch.setattr(api_module, "build_adapter", lambda _: injected)
    service = api_module.create_app(app_settings).state.service
    refresh_result: dict[str, Any] = {}

    def refresh_catalog() -> None:
        refresh_result.update(service.refresh_llm_model_catalog())

    refresh_thread = threading.Thread(target=refresh_catalog, daemon=True)
    refresh_thread.start()
    assert catalog_started.wait(timeout=1)

    replacement_finished = threading.Event()

    def replace_credential() -> None:
        service.save_llm_api_key("new-key")
        replacement_finished.set()

    replacement_thread = threading.Thread(target=replace_credential, daemon=True)
    replacement_thread.start()
    replacement_did_not_wait = replacement_finished.wait(timeout=0.25)
    release_catalog.set()
    refresh_thread.join(timeout=2)
    replacement_thread.join(timeout=2)

    assert replacement_did_not_wait is True
    assert store.value == "new-key"
    assert refresh_result["available_models"] == ["new-key-model"]
    assert service.refresh_llm_model_catalog()["available_models"] == ["new-key-model"]
    assert [call["method"] for call in sequence.calls] == ["GET", "GET"]


def test_failed_startup_catalog_result_is_reused_until_a_forced_refresh(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    app_settings = Settings(
        app=AppSettings(
            database_path=tmp_path / "portfolio.db", one_drive_root=one_drive,
            testing=True,
        ),
        llm=settings(),
    )
    sequence = SequenceClient([httpx.Response(503), httpx.Response(503)])
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=MemoryCredentialStore("saved-key"),
        model_preference_store=MemoryModelPreferenceStore(),
        client_factory=sequence.factory, sleeper=lambda _: None,
    )
    monkeypatch.setattr(api_module, "build_adapter", lambda _: injected)
    service = api_module.create_app(app_settings).state.service

    first = service.refresh_llm_model_catalog()
    second = service.refresh_llm_model_catalog()

    assert first == second
    assert first["ok"] is False
    assert len(sequence.calls) == 1
    forced = service.refresh_llm_model_catalog(force=True)
    assert forced["ok"] is False
    assert len(sequence.calls) == 2


def test_shutdown_does_not_wait_for_an_inflight_startup_catalog_request(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    app_settings = Settings(
        app=AppSettings(
            database_path=tmp_path / "portfolio.db", one_drive_root=one_drive,
            worker_poll_seconds=0.05, testing=False,
        ),
        llm=settings(),
    )
    catalog_started = threading.Event()
    release_catalog = threading.Event()

    class BlockingStartupClient(SequenceClient):
        def get(self, url: str, **kwargs: Any) -> httpx.Response:
            self.calls.append({"method": "GET", "url": url, **kwargs})
            catalog_started.set()
            assert release_catalog.wait(timeout=2)
            return model_catalog("startup-model")

    sequence = BlockingStartupClient([])
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=MemoryCredentialStore("saved-key"),
        model_preference_store=MemoryModelPreferenceStore(),
        client_factory=sequence.factory, sleeper=lambda _: None,
    )
    monkeypatch.setattr(api_module, "build_adapter", lambda _: injected)
    app = api_module.create_app(app_settings)
    context_exited = threading.Event()

    def run_app_lifespan() -> None:
        with TestClient(
            app, base_url="http://127.0.0.1:8765",
            headers={"X-Requested-With": "CHIO-Portfolio-Assistant"},
        ):
            assert catalog_started.wait(timeout=1)
        context_exited.set()

    app_thread = threading.Thread(target=run_app_lifespan, daemon=True)
    app_thread.start()
    assert catalog_started.wait(timeout=1)
    shutdown_did_not_wait = context_exited.wait(timeout=0.25)
    release_catalog.set()
    app_thread.join(timeout=2)

    assert shutdown_did_not_wait is True


@pytest.mark.parametrize(
    ("routine_model", "judgment_model"),
    [
        ("", "judgment-model"),
        ("routine-model", "bad\nmodel"),
        ("x" * 257, "judgment-model"),
    ],
)
def test_invalid_model_choices_are_not_persisted_or_applied(routine_model, judgment_model):
    model_store = MemoryModelPreferenceStore({
        "routine_model": "original-routine",
        "judgment_model": "original-judgment",
    })
    adapter, _, _ = adapter_with([], model_store=model_store)
    with pytest.raises(LlmUnavailable, match="valid model"):
        adapter.save_models(routine_model, judgment_model)
    assert model_store.value == {
        "routine_model": "original-routine",
        "judgment_model": "original-judgment",
    }
    assert adapter.model_id == "original-routine"
    assert adapter.model_for("project_fit_check") == "original-judgment"


def test_corrupt_saved_model_preferences_fall_back_to_toml_defaults():
    class BrokenModelStore(MemoryModelPreferenceStore):
        def load(self) -> dict[str, str] | None:
            raise ModelPreferenceError("corrupt test preferences")

    adapter, _, _ = adapter_with([], model_store=BrokenModelStore())
    assert adapter.model_id == "gemini-3.5-flash"
    assert adapter.model_for("multi_project_routing") == "gemini-3.1-pro-preview"
    assert adapter.model_preference_error is True
    adapter.save_models("fixed-routine", "fixed-judgment")
    assert adapter.model_preference_error is False
    assert adapter.model_id == "fixed-routine"
    assert adapter.model_for("multi_project_routing") == "fixed-judgment"


def test_model_catalog_redirects_are_rejected_without_following():
    adapter, _, _ = adapter_with([
        httpx.Response(302, headers={"location": "https://attacker.invalid/v1/models"}),
    ])
    with pytest.raises(LlmUnavailable, match="off-host redirect"):
        adapter.list_models()


def test_model_catalog_401_identifies_saved_gui_key_without_exposing_it():
    adapter, _, _ = adapter_with(
        [httpx.Response(401)], store=MemoryCredentialStore("saved-gui-secret"),
    )
    with pytest.raises(LlmUnavailable) as rejected:
        adapter.list_models()
    message = str(rejected.value)
    assert "API key saved in Settings" in message
    assert "HTTP 401" in message
    assert "saved-gui-secret" not in message


def test_undecryptable_local_credential_can_still_be_removed():
    class BrokenStore(MemoryCredentialStore):
        def get(self) -> str | None:
            raise CredentialError("cannot decrypt")

    adapter, _, _ = adapter_with([], store=BrokenStore("encrypted"))
    status = adapter.credential_status()
    assert status == {
        "configured": False, "source": "none", "environment_override": False,
        "local_key_present": True, "credential_error": True,
    }
    removed = adapter.remove_api_key()
    assert removed["removed"] is True


def test_multimodal_payload_uses_actual_mime_flash_and_4000_tokens():
    adapter, sequence, _ = adapter_with([completion('{"text":"ok"}')])
    assert adapter.analyze_image(b"\x89PNG\r\n", "image/png", "Extract text")["text"] == "ok"
    body = sequence.calls[0]["json"]
    assert body["model"] == "gemini-3.5-flash"
    assert body["max_tokens"] == 4_000
    image_url = body["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")


def test_rate_limiter_waits_outside_critical_section_with_injected_clock():
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = SlidingWindowRateLimiter(2, 10, clock=lambda: now[0], sleeper=sleep)
    assert limiter.acquire() == 0
    assert limiter.acquire() == 0
    assert limiter.acquire() == 10
    assert sleeps == [10]


def test_default_adapters_share_one_process_rate_limiter():
    first = InternalHttpLlmAdapter(settings(rate_limit_requests=17), credential_store=MemoryCredentialStore())
    second = InternalHttpLlmAdapter(settings(rate_limit_requests=17), credential_store=MemoryCredentialStore())
    assert first._rate_limiter is second._rate_limiter


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_store_round_trip_is_not_plaintext(tmp_path: Path):
    path = tmp_path / "credential.bin"
    store = WindowsDpapiCredentialStore(path)
    secret = "genai-test-secret-value"
    store.set(secret)
    assert secret.encode() not in path.read_bytes()
    assert store.get() == secret
    assert store.delete() is True
    assert store.get() is None


def test_credential_and_health_api_never_returns_key(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    app_settings = Settings(
        app=AppSettings(
            database_path=tmp_path / "portfolio.db", one_drive_root=one_drive,
            testing=True,
        ),
        llm=settings(),
    )
    store = MemoryCredentialStore(None)
    model_store = MemoryModelPreferenceStore()
    sequence = SequenceClient([
        model_catalog(
            "new-judgment", "gemini-3.1-pro-preview",
            "new-routine", "gemini-3.5-flash",
        ),
        completion('{"ok":true}', model="gemini-3.5-flash"),
        completion('{"ok":true}', model="gemini-3.1-pro-preview"),
    ])
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=store, model_preference_store=model_store,
        client_factory=sequence.factory,
        sleeper=lambda _: None,
    )
    monkeypatch.setattr(api_module, "build_adapter", lambda _: injected)
    with TestClient(
        api_module.create_app(app_settings), base_url="http://127.0.0.1:8765",
        headers={"X-Requested-With": "CHIO-Portfolio-Assistant"},
    ) as client:
        saved = client.put("/api/llm/credential", json={"api_key": "api-secret-from-ui"})
        assert saved.status_code == 200
        assert "api-secret-from-ui" not in saved.text
        configuration = client.get("/api/configuration").json()
        assert configuration["api_key_present"] is True
        assert "api-secret-from-ui" not in str(configuration)
        health = client.post("/api/llm/health", json={}).json()
        assert health["ok"] is True
        assert health["available_models"] == [
            "gemini-3.1-pro-preview", "gemini-3.5-flash", "new-judgment", "new-routine",
        ]
        assert health["routine"]["ok"] is True
        assert health["routine"]["configured_model"] == "gemini-3.5-flash"
        assert health["judgment"]["ok"] is True
        assert health["judgment"]["configured_model"] == "gemini-3.1-pro-preview"
        assert [call["method"] for call in sequence.calls] == ["GET", "POST", "POST"]
        assert [call["json"]["model"] for call in sequence.calls[1:]] == [
            "gemini-3.5-flash", "gemini-3.1-pro-preview",
        ]
        selected = client.put("/api/llm/models", json={
            "routine_model": "new-routine",
            "judgment_model": "new-judgment",
        })
        assert selected.status_code == 200
        assert selected.json() == {
            "routine_model": "new-routine", "judgment_model": "new-judgment",
        }
        refreshed = client.get("/api/configuration").json()
        assert refreshed["llm_model"] == "new-routine"
        assert refreshed["llm_judgment_model"] == "new-judgment"
        assert model_store.value == selected.json()
        removed = client.delete("/api/llm/credential")
        assert removed.json()["configured"] is False
        assert "api-secret-from-ui" not in removed.text


def test_health_refresh_returns_models_without_probing_missing_selections(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    app_settings = Settings(
        app=AppSettings(
            database_path=tmp_path / "portfolio.db", one_drive_root=one_drive,
            testing=True,
        ),
        llm=settings(),
    )
    sequence = SequenceClient([model_catalog("available-routine", "available-judgment")])
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=MemoryCredentialStore("saved-key"),
        model_preference_store=MemoryModelPreferenceStore(),
        client_factory=sequence.factory, sleeper=lambda _: None,
    )
    monkeypatch.setattr(api_module, "build_adapter", lambda _: injected)
    with TestClient(
        api_module.create_app(app_settings), base_url="http://127.0.0.1:8765",
        headers={"X-Requested-With": "CHIO-Portfolio-Assistant"},
    ) as client:
        health = client.post("/api/llm/health", json={}).json()
    assert health["ok"] is False
    assert health["available_models"] == ["available-judgment", "available-routine"]
    assert health["unavailable_configured_models"] == [
        "gemini-3.1-pro-preview", "gemini-3.5-flash",
    ]
    assert "Choose available models" in health["error"]
    assert [call["method"] for call in sequence.calls] == ["GET"]


def test_health_keeps_refreshed_models_when_a_probe_is_rejected(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "one-drive"
    one_drive.mkdir()
    app_settings = Settings(
        app=AppSettings(
            database_path=tmp_path / "portfolio.db", one_drive_root=one_drive,
            testing=True,
        ),
        llm=settings(),
    )
    sequence = SequenceClient([
        model_catalog("gemini-3.1-pro-preview", "gemini-3.5-flash"),
        httpx.Response(401),
        completion('{"ok":true}', model="gemini-3.1-pro-preview"),
    ])
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=MemoryCredentialStore("saved-key"),
        model_preference_store=MemoryModelPreferenceStore(),
        client_factory=sequence.factory, sleeper=lambda _: None,
    )
    monkeypatch.setattr(api_module, "build_adapter", lambda _: injected)
    with TestClient(
        api_module.create_app(app_settings), base_url="http://127.0.0.1:8765",
        headers={"X-Requested-With": "CHIO-Portfolio-Assistant"},
    ) as client:
        health = client.post("/api/llm/health", json={}).json()
    assert health["ok"] is False
    assert health["available_models"] == [
        "gemini-3.1-pro-preview", "gemini-3.5-flash",
    ]
    assert health["routine"]["ok"] is False
    assert health["routine"]["configured_model"] == "gemini-3.5-flash"
    assert health["judgment"]["ok"] is True
    assert health["judgment"]["configured_model"] == "gemini-3.1-pro-preview"
    assert "API key saved in Settings" in health["error"]
    assert "saved-key" not in str(health)
    assert [call["method"] for call in sequence.calls] == ["GET", "POST", "POST"]
