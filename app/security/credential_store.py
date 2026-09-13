from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from pathlib import Path


CRYPTPROTECT_UI_FORBIDDEN = 0x01


class CredentialStoreError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class WindowsCredentialStore:
    """Encrypt secrets with Windows DPAPI for the current Windows user."""

    def __init__(self, path: str | Path | None = None):
        local_app_data = os.getenv("LOCALAPPDATA")
        default_root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        self.path = Path(path).expanduser().resolve() if path else default_root / "SportsbeamsPayrollAgent" / "credentials.json"

    def set(self, name: str, secret: str) -> None:
        if os.name != "nt":
            raise CredentialStoreError("Windows DPAPI credential storage is only available on Windows")
        if not name.strip() or not secret.strip():
            raise CredentialStoreError("Credential name and secret are required")
        values = self._read_file()
        values[name] = base64.b64encode(_protect(secret.encode("utf-8"))).decode("ascii")
        self._write_file(values)

    def get(self, name: str) -> str | None:
        encoded = self._read_file().get(name)
        if not encoded:
            return None
        try:
            return _unprotect(base64.b64decode(encoded)).decode("utf-8")
        except Exception as error:
            raise CredentialStoreError("Unable to decrypt the stored credential for this Windows user") from error

    def delete(self, name: str) -> bool:
        values = self._read_file()
        if name not in values:
            return False
        del values[name]
        self._write_file(values)
        return True

    def _read_file(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError) as error:
            raise CredentialStoreError(f"Credential file is invalid: {self.path}") from error

    def _write_file(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix="credentials-", suffix=".tmp", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(values, stream, ensure_ascii=False, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    result = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(ctypes.byref(source), "Sportsbeams Payroll Agent", None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _unprotect(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


DEEPSEEK_CREDENTIAL_NAME = "deepseek-api-key"


def get_deepseek_api_key() -> str | None:
    stored = WindowsCredentialStore().get(DEEPSEEK_CREDENTIAL_NAME)
    return stored or os.getenv("DEEPSEEK_API_KEY")


def save_deepseek_api_key(api_key: str) -> None:
    WindowsCredentialStore().set(DEEPSEEK_CREDENTIAL_NAME, api_key)
