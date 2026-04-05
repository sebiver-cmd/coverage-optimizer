"""Symmetric encryption helpers for the credential vault (Task 5.1).

Provides :func:`encrypt_str` and :func:`decrypt_str` backed by
:class:`cryptography.fernet.Fernet`.

Security invariants
-------------------
- The ``ENCRYPTION_KEY`` must be a valid URL-safe base64-encoded
  32-byte key (the format produced by ``Fernet.generate_key()``).
- Plaintext secrets and ciphertext are **never** logged.
- Callers must not decrypt credentials except when making SOAP calls.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from backend.config import get_settings

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    """Return a :class:`Fernet` instance using the configured key.

    Raises :class:`ValueError` when ``ENCRYPTION_KEY`` is missing or
    invalid.
    """
    settings = get_settings()
    key = settings.encryption_key
    if not key:
        raise ValueError(
            "ENCRYPTION_KEY is not configured. "
            "Set it to a valid Fernet key (use Fernet.generate_key())."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise ValueError(
            "ENCRYPTION_KEY is not a valid Fernet key. "
            "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        ) from exc


def encrypt_str(plain: str) -> str:
    """Encrypt *plain* and return a URL-safe base64 token string.

    The returned token includes a timestamp and can only be decrypted
    with the same ``ENCRYPTION_KEY``.
    """
    f = _get_fernet()
    return f.encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_str(token: str) -> str:
    """Decrypt a Fernet *token* (produced by :func:`encrypt_str`).

    Raises :class:`cryptography.fernet.InvalidToken` on tampering,
    wrong key, or malformed input.
    """
    f = _get_fernet()
    return f.decrypt(token.encode("ascii")).decode("utf-8")
