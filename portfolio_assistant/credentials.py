from __future__ import annotations

import ctypes
import functools
import os
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


class CredentialError(RuntimeError):
    """A safe, non-secret-bearing credential storage failure."""


class CredentialStore(Protocol):
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
        return value or None

    def set(self, value: str) -> None:
        key = value.strip()
        if not key:
            raise CredentialError("Enter a GenAI.mil API key before saving")
        ciphertext = self._protect(key.encode("utf-8"))
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
