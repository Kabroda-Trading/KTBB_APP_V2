# executor_crypto.py
# ==============================================================================
# EXECUTOR CREDENTIAL ENCRYPTION -- Stage 1 of the Bitunix executor bot
# (Kabroda AI Brain repo AGENT_LOG.md, 2026-09-04 design conversation).
#
# The first reversible (decrypt-able) secret storage in this codebase.
# auth.py's password hashing is one-way (PBKDF2-HMAC-SHA256) and cannot be
# reused here -- placing a real exchange order requires the plaintext key
# back, not just a way to verify it matches.
#
# Keyed from EXECUTOR_CREDENTIAL_KEY, a required env var holding a real
# Fernet key (generate one with `Fernet.generate_key()` once, set it on
# Render, never rotate it without re-encrypting every stored credential
# first). Deliberately NOT main.py's SESSION_SECRET pattern
# (`os.getenv("SESSION_SECRET", "kabroda_prod_key_999")`), which falls
# back to a hardcoded default -- fine for signing session cookies, not
# acceptable for the key that protects real exchange credentials. This
# module hard-fails (raises) if the env var is missing or malformed,
# rather than silently operating insecurely.
# ==============================================================================

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_ENV_VAR = "EXECUTOR_CREDENTIAL_KEY"


def _get_fernet() -> Fernet:
    key = os.environ.get(_ENV_VAR)
    if not key:
        raise RuntimeError(
            f"{_ENV_VAR} is not set -- required to encrypt/decrypt exchange "
            f"credentials. Generate one with Fernet.generate_key() and set it "
            f"as an env var (Render dashboard for production). Refusing to "
            f"fall back to any default -- this key protects real exchange secrets."
        )
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception as e:
        raise RuntimeError(f"{_ENV_VAR} is not a valid Fernet key: {e}")


def validate_key_configured() -> None:
    """Called once at app startup (main.py) so a misconfigured deploy fails
    loud at boot -- not silently, the first time someone tries to save a
    credential in the admin UI."""
    _get_fernet()


def encrypt_secret(plaintext: str) -> str:
    """Returns a self-contained Fernet token (already includes a version
    byte, timestamp, and HMAC -- no extra wrapping/prefix needed, unlike
    auth.py's manual '$'-delimited hash format)."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Raises InvalidToken if the ciphertext doesn't match the current key
    (wrong key, corrupted value, or tampered data) -- never returns
    garbage silently."""
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise InvalidToken(
            "Could not decrypt -- wrong EXECUTOR_CREDENTIAL_KEY, corrupted "
            "value, or the credential was encrypted under a different key."
        )
