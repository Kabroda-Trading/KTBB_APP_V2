"""
Unit coverage for executor_crypto.py -- the first reversible-encryption
module in this codebase (Stage 1 of the Bitunix executor bot). Pure
function tests, no DB -- straightforward with a real Fernet key generated
per-test via monkeypatch.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.fernet import Fernet, InvalidToken

import executor_crypto as ec


def test_encrypt_decrypt_round_trip(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    plaintext = "my-real-bitunix-api-key-abc123"
    ciphertext = ec.encrypt_secret(plaintext)
    assert ciphertext != plaintext
    assert ec.decrypt_secret(ciphertext) == plaintext


def test_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("EXECUTOR_CREDENTIAL_KEY", raising=False)
    with pytest.raises(RuntimeError, match="EXECUTOR_CREDENTIAL_KEY"):
        ec.encrypt_secret("anything")


def test_malformed_env_var_raises(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", "not-a-real-fernet-key")
    with pytest.raises(RuntimeError, match="not a valid Fernet key"):
        ec.encrypt_secret("anything")


def test_wrong_key_decrypt_fails_cleanly(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    ciphertext = ec.encrypt_secret("secret-value")

    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    with pytest.raises(InvalidToken):
        ec.decrypt_secret(ciphertext)


def test_validate_key_configured_passes_with_real_key(monkeypatch):
    monkeypatch.setenv("EXECUTOR_CREDENTIAL_KEY", Fernet.generate_key().decode("utf-8"))
    ec.validate_key_configured()  # must not raise


def test_validate_key_configured_raises_without_key(monkeypatch):
    monkeypatch.delenv("EXECUTOR_CREDENTIAL_KEY", raising=False)
    with pytest.raises(RuntimeError):
        ec.validate_key_configured()
