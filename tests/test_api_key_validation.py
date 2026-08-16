from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio_assistant.credentials import (
    InvalidApiKeyError, WindowsDpapiCredentialStore, normalize_api_key,
)

GOOD = "sk-genai-AbC123_xyz-456"
HEAD, TAIL = "sk-genai-AbC123_xyz", "-456"

# Paste damage that is repairable: the key underneath is intact. Invisible
# characters are escaped so each case tests the character it names.
REPAIRED = [
    ("curly-quotes", "“" + GOOD + "”"),
    ("straight-quotes", '"' + GOOD + '"'),
    ("single-curly-quotes", "‘" + GOOD + "’"),
    ("guillemets", "«" + GOOD + "»"),
    ("trailing-nbsp", GOOD + " "),
    ("interior-nbsp", HEAD + " " + TAIL),
    ("zero-width-space", HEAD + "​" + TAIL),
    ("zero-width-joiner", HEAD + "‍" + TAIL),
    ("word-joiner", HEAD + "⁠" + TAIL),
    ("soft-hyphen", HEAD + "­" + TAIL),
    ("utf8-bom", "﻿" + GOOD),
    ("interior-tab", HEAD + "\t" + TAIL),
    ("surrounding-whitespace", "   " + GOOD + "\t "),
    ("quotes-and-padding", " “ " + GOOD + " ” "),
]

# Damage that cannot be repaired into a header-safe key.
REJECTED = [
    ("empty", ""),
    ("whitespace-only", "     "),
    ("nbsp-only", "  "),
    ("quotes-only", "“”"),
    ("accented-text", "sk-genai-café-456"),
    ("cyrillic-homoglyph", "sk-genai-АbC123"),
    ("emoji", GOOD + "\U0001f511"),
    ("carriage-return", GOOD + "\r"),
    ("newline-injection", GOOD + "\nX-Injected: 1"),
    ("crlf-injection", GOOD + "\r\nX-Injected: 1"),
    ("null-byte", GOOD + "\x00"),
]


@pytest.mark.parametrize("raw", [case[1] for case in REPAIRED], ids=[c[0] for c in REPAIRED])
def test_repairable_paste_damage_is_normalized(raw: str) -> None:
    assert normalize_api_key(raw) == GOOD


@pytest.mark.parametrize("raw", [case[1] for case in REJECTED], ids=[c[0] for c in REJECTED])
def test_unusable_keys_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidApiKeyError):
        normalize_api_key(raw)


@pytest.mark.parametrize("raw", [case[1] for case in REJECTED], ids=[c[0] for c in REJECTED])
def test_rejection_message_never_contains_the_key(raw: str) -> None:
    with pytest.raises(InvalidApiKeyError) as caught:
        normalize_api_key(raw)
    message = str(caught.value)
    assert GOOD not in message
    assert "AbC123" not in message


def test_already_clean_key_is_returned_unchanged() -> None:
    assert normalize_api_key(GOOD) == GOOD


def test_normalization_is_idempotent() -> None:
    for _, raw in REPAIRED:
        once = normalize_api_key(raw)
        assert normalize_api_key(once) == once


def test_normalized_keys_survive_http_header_encoding() -> None:
    # Latin-1 is what the HTTP stack encodes header values with. Anything that
    # escaped normalization would raise here instead of at request time.
    for _, raw in REPAIRED:
        ("Bearer " + normalize_api_key(raw)).encode("latin-1")


def test_store_writes_nothing_when_the_key_is_unusable(tmp_path: Path) -> None:
    store = WindowsDpapiCredentialStore(path=tmp_path / "creds" / "genai-api-key.bin")
    with pytest.raises(InvalidApiKeyError):
        store.set("sk-genai-café-456")
    assert not store.path.exists()
    assert not store.path.parent.exists()


def test_store_repairs_a_damaged_key_already_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keys saved before this validation existed are still in %LOCALAPPDATA%.
    store = WindowsDpapiCredentialStore(path=tmp_path / "genai-api-key.bin")
    store.path.write_bytes(b"ciphertext")
    monkeypatch.setattr(
        WindowsDpapiCredentialStore, "_unprotect",
        classmethod(lambda cls, blob: ("“" + GOOD + "”").encode("utf-8")),
    )
    assert store.get() == GOOD


def test_store_reports_an_unrepairable_key_already_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WindowsDpapiCredentialStore(path=tmp_path / "genai-api-key.bin")
    store.path.write_bytes(b"ciphertext")
    monkeypatch.setattr(
        WindowsDpapiCredentialStore, "_unprotect",
        classmethod(lambda cls, blob: "sk-genai-café".encode("utf-8")),
    )
    with pytest.raises(InvalidApiKeyError):
        store.get()


def test_oversize_key_is_rejected_without_echoing_it(client: TestClient) -> None:
    # Pydantic reports the rejected value in "input" and SecretStr does not
    # redact it, so FastAPI's default handler returns the key verbatim.
    oversize = "sk-" + "a" * 20_001
    response = client.put("/api/llm/credential", json={"api_key": oversize})
    assert response.status_code == 422
    assert oversize not in response.text
    assert "a" * 100 not in response.text


def test_empty_key_is_rejected(client: TestClient) -> None:
    assert client.put("/api/llm/credential", json={"api_key": ""}).status_code == 422


def test_validation_errors_keep_their_shape(client: TestClient) -> None:
    response = client.put("/api/llm/credential", json={"api_key": "x" * 20_001})
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail
    assert {"type", "loc", "msg"} <= set(detail[0])
    assert "input" not in detail[0]
    assert "ctx" not in detail[0]
