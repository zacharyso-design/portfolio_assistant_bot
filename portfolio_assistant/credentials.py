from __future__ import annotations

import ctypes
import functools
import os
import re
import sys
import threading
import unicodedata
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


class CredentialError(RuntimeError):
    """A safe, non-secret-bearing credential storage failure."""


class InvalidApiKeyError(CredentialError):
    """The supplied key cannot be used. The message never contains the key."""


# Matched wrapper pairs that documents put around pasted values. Only a
# matched pair is unwrapped; a key that merely starts or ends with a quote
# character is left alone rather than guessed at.
_QUOTE_PAIRS = frozenset({
    ('"', '"'), ("'", "'"), ("`", "`"), ("´", "´"),
    ("“", "”"), ("‘", "’"), ("„", "“"), ("„", "”"),
    ("‚", "‘"), ("‚", "’"), ("‟", "”"), ("‛", "’"),
    ("«", "»"), ("‹", "›"), ("＂", "＂"), ("＇", "＇"),
})
# Printable ASCII without space: what a freshly saved key must reduce to.
_HEADER_SAFE = re.compile(r"[\x21-\x7e]+")
# Printable ASCII including space: every key the pre-validation releases
# could store or serve successfully. Lets legacy values keep working verbatim.
_LEGACY_SENDABLE = re.compile(r"[\x20-\x7e]+")
# Characters that mark a second line. str.strip() removes them at the ends;
# in the interior they mean the paste was never a single key.
_LINE_BREAKS = frozenset("\r\n\x0b\x0c\x1c\x1d\x1e\x85  ")


def sendable_api_key(value: str) -> bool:
    """True when a key can be sent in an HTTP header exactly as given."""
    return bool(_LEGACY_SENDABLE.fullmatch(value))


def _unwrap_quotes(text: str) -> str:
    while len(text) >= 2 and (text[0], text[-1]) in _QUOTE_PAIRS:
        text = text[1:-1].strip()
    return text


def normalize_api_key(raw: str) -> str:
    """Repair paste damage, then require a header-safe key.

    Keys arrive pasted out of documents far more often than they are typed.
    Damage that leaves the key intact is repaired: surrounding whitespace and
    line breaks, matched wrapping quotes, and zero-width format characters.
    Damage that makes the key ambiguous is rejected rather than guessed at —
    joining the pieces of a multi-line or space-split paste would store a
    different wrong key that fails with a bare 401 at request time.

    Raises InvalidApiKeyError, whose message never contains the key.
    """
    if _HEADER_SAFE.fullmatch(raw) and (len(raw) < 2 or (raw[0], raw[-1]) not in _QUOTE_PAIRS):
        return raw
    if "\x00" in raw:
        raise InvalidApiKeyError("The API key contains unusable control characters.")
    # Trim end padding (str.strip removes every _LINE_BREAKS member too) and
    # unwrap quotes before NFKC: U+00B4 decomposes under NFKC into a space
    # plus a combining accent and would stop matching its pair.
    text = _unwrap_quotes(raw.strip())
    if any(ch in _LINE_BREAKS for ch in text):
        raise InvalidApiKeyError(
            "The API key must be a single line. Paste only the key itself."
        )
    text = unicodedata.normalize("NFKC", text)
    # Zero-width and joiner characters (category Cf: ZWSP, BOM, soft hyphen,
    # word joiner) are invisible — removing them restores what the user saw.
    # Other C categories (controls, surrogates, private use) are NOT removed:
    # deleting visible-in-effect garbage would fabricate a different key.
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = _unwrap_quotes(text.strip())
    if not text:
        raise InvalidApiKeyError("Enter a GenAI.mil API key before saving")
    if any(ch.isspace() for ch in text):
        # An interior space is a wrap artifact or a two-part paste; joining
        # the parts silently would store a different wrong key.
        raise InvalidApiKeyError(
            "The API key contains spaces. Paste only the key itself, with nothing around it."
        )
    if not _HEADER_SAFE.fullmatch(text):
        raise InvalidApiKeyError(
            "The API key contains characters that cannot be sent in an HTTP header. "
            "Retype the key instead of pasting it from a document."
        )
    return text


class CredentialStore(Protocol):
    """Stores one secret verbatim.

    Callers own validation: InternalHttpLlmAdapter.save_api_key normalizes
    before calling set(), so every implementation receives a header-safe key.
    """

    def get(self) -> str | None: ...
    def set(self, value: str) -> None: ...
    def delete(self) -> bool: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


@functools.lru_cache(maxsize=1)
def _windows_libraries() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    if sys.platform != "win32":
        raise CredentialError("Encrypted API-key storage is available only on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    return crypt32, kernel32


class WindowsDpapiCredentialStore:
    """Stores one secret encrypted to the current Windows user with DPAPI."""

    _ENTROPY = b"CHIO Portfolio Assistant GenAI.mil credential v1"
    _UI_FORBIDDEN = 0x01

    def __init__(self, path: Path | None = None):
        local_app_data = os.environ.get("LOCALAPPDATA")
        default_root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        self.path = path or default_root / "PortfolioAssistant" / "credentials" / "genai-api-key.bin"

    @staticmethod
    def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(value)
        blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer

    @classmethod
    def _protect(cls, plaintext: bytes) -> bytes:
        source, source_buffer = cls._blob(plaintext)
        entropy, entropy_buffer = cls._blob(cls._ENTROPY)
        output = _DataBlob()
        crypt32, kernel32 = _windows_libraries()
        try:
            if not crypt32.CryptProtectData(
                ctypes.byref(source), "CHIO Portfolio Assistant GenAI.mil API key",
                ctypes.byref(entropy), None, None, cls._UI_FORBIDDEN, ctypes.byref(output),
            ):
                raise CredentialError("Windows could not encrypt the GenAI.mil API key")
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.memset(source.pbData, 0, source.cbData)
            if output.pbData:
                kernel32.LocalFree(output.pbData)
            # Keep the ctypes owners alive until after the native call and zeroing.
            _ = source_buffer, entropy_buffer

    @classmethod
    def _unprotect(cls, ciphertext: bytes) -> bytes:
        source, source_buffer = cls._blob(ciphertext)
        entropy, entropy_buffer = cls._blob(cls._ENTROPY)
        output = _DataBlob()
        crypt32, kernel32 = _windows_libraries()
        description = wintypes.LPWSTR()
        try:
            if not crypt32.CryptUnprotectData(
                ctypes.byref(source), ctypes.byref(description), ctypes.byref(entropy),
                None, None, cls._UI_FORBIDDEN, ctypes.byref(output),
            ):
                raise CredentialError("The saved GenAI.mil API key cannot be decrypted for this Windows user")
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            if output.pbData:
                ctypes.memset(output.pbData, 0, output.cbData)
                kernel32.LocalFree(output.pbData)
            if description.value is not None:
                kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
            # Keep the ctypes owners alive until after the native call and cleanup.
            _ = source_buffer, entropy_buffer

    def get(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            ciphertext = self.path.read_bytes()
            value = self._unprotect(ciphertext).decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise CredentialError("The saved GenAI.mil API key could not be read") from exc
        if not value:
            return None
        if sendable_api_key(value):
            # Every key this store ever accepted was sendable; legacy values
            # (which may hold interior spaces) keep working verbatim instead
            # of being silently rewritten into a different key.
            return value
        try:
            # A pre-validation save may hold repairable paste damage on disk.
            return normalize_api_key(value)
        except InvalidApiKeyError as exc:
            raise InvalidApiKeyError(
                "The saved GenAI.mil API key cannot be used. Remove it and save it again in Settings."
            ) from exc

    def set(self, value: str) -> None:
        key = value.strip()
        if not key:
            raise CredentialError("Enter a GenAI.mil API key before saving")
        try:
            encoded = key.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CredentialError("The GenAI.mil API key could not be encoded for storage") from exc
        ciphertext = self._protect(encoded)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(ciphertext)
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CredentialError("The encrypted GenAI.mil API key could not be saved") from exc

    def delete(self) -> bool:
        try:
            if not self.path.exists():
                return False
            self.path.unlink()
            return True
        except OSError as exc:
            raise CredentialError("The encrypted GenAI.mil API key could not be removed") from exc
