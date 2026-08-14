from __future__ import annotations

import logging
import sys
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
        self.calls.append({"url": url, **kwargs})
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
) -> tuple[InternalHttpLlmAdapter, SequenceClient, list[float]]:
    sequence = SequenceClient(responses)
    sleeps: list[float] = []
    sleep = sleeper or sleeps.append
    adapter = InternalHttpLlmAdapter(
        llm_settings or settings(), credential_store=store or MemoryCredentialStore(),
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


def test_environment_key_has_priority_over_encrypted_store(monkeypatch):
    monkeypatch.setenv("TEST_GENAI_KEY", "environment-key")
    adapter, sequence, _ = adapter_with([completion('{"ok":true}')], store=MemoryCredentialStore("stored-key"))
    adapter.test_connection()
    assert sequence.calls[0]["headers"]["Authorization"] == "Bearer environment-key"
    assert adapter.credential_status()["source"] == "environment"


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
    sequence = SequenceClient([completion('{"ok":true}')])
    injected = InternalHttpLlmAdapter(
        app_settings.llm, credential_store=store, client_factory=sequence.factory,
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
        removed = client.delete("/api/llm/credential")
        assert removed.json()["configured"] is False
        assert "api-secret-from-ui" not in removed.text
