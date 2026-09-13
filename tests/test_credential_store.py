from __future__ import annotations

import json

from app.security.credential_store import WindowsCredentialStore


def test_windows_dpapi_roundtrip_does_not_store_plaintext(tmp_path):
    path = tmp_path / "credentials.json"
    store = WindowsCredentialStore(path)
    secret = "sk-test-only-not-a-real-key-123456"

    store.set("deepseek-api-key", secret)

    assert store.get("deepseek-api-key") == secret
    assert secret not in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["deepseek-api-key"]


def test_delete_stored_credential(tmp_path):
    store = WindowsCredentialStore(tmp_path / "credentials.json")
    store.set("deepseek-api-key", "sk-test-only-not-a-real-key-123456")

    assert store.delete("deepseek-api-key") is True
    assert store.get("deepseek-api-key") is None
    assert store.delete("deepseek-api-key") is False
