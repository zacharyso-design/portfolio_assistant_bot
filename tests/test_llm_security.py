from __future__ import annotations

import httpx
import pytest

from portfolio_assistant.config import AppSettings, ConfigurationError, LlmSettings, _validate
from portfolio_assistant.llm import InternalHttpLlmAdapter, LlmUnavailable


def test_internal_adapter_keeps_tls_and_rejects_off_host_redirect(monkeypatch):
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return httpx.Response(302, headers={"location": "https://public.example/steal"})

    monkeypatch.setenv("FICTIONAL_INTERNAL_KEY", "secret-not-logged")
    monkeypatch.setattr(httpx, "Client", FakeClient)
    adapter = InternalHttpLlmAdapter(LlmSettings(
        adapter="internal",
        base_url="https://approved.internal.example",
        chat_path="/v1/chat/completions",
        model="fictional-approved-model",
        api_key_env="FICTIONAL_INTERNAL_KEY",
    ))
    with pytest.raises(LlmUnavailable, match="off-host redirect"):
        adapter.test_connection()
    assert captured["verify"] is True
    assert captured["follow_redirects"] is False
    assert captured["url"] == "https://approved.internal.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret-not-logged"


def test_internal_adapter_rejects_cross_host_chat_path():
    with pytest.raises(Exception, match="configured HTTPS host"):
        InternalHttpLlmAdapter(LlmSettings(
            adapter="internal",
            base_url="https://approved.internal.example",
            chat_path="https://public.example/v1/chat/completions",
            model="fictional-approved-model",
        ))


def test_fake_adapter_requires_explicit_non_test_opt_in(tmp_path, monkeypatch):
    app = AppSettings(database_path=tmp_path / "portfolio.db", one_drive_root=tmp_path, testing=False)
    fake = LlmSettings(adapter="fake", model="fake-llm-v1")
    monkeypatch.delenv("PORTFOLIO_ASSISTANT_ALLOW_FAKE_LLM", raising=False)
    with pytest.raises(ConfigurationError, match="test/demo-only"):
        _validate(app, fake)
    monkeypatch.setenv("PORTFOLIO_ASSISTANT_ALLOW_FAKE_LLM", "1")
    _validate(app, fake)
