"""Symmetric encryption for connector secrets (e.g. ClickUp API tokens).

Tokens for self-serve connectors are stored per-knowledge in the DB, so they must
be encrypted at rest. We use Fernet (AES-128-CBC + HMAC, URL-safe base64) keyed by
the CONNECTOR_SECRET_KEY env var. Ciphertext is what lands in
connector_sync_config.encrypted_token; plaintext is only ever held transiently
inside the crawl task.

Generate a key once with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and set it as CONNECTOR_SECRET_KEY in the environment.
"""

from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from utils.logger import logger
from utils.setup_env import CONNECTOR_SECRET_KEY


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if not CONNECTOR_SECRET_KEY:
        raise RuntimeError(
            "CONNECTOR_SECRET_KEY is not set — cannot encrypt/decrypt connector tokens. "
            "Generate one with Fernet.generate_key() and set it in the environment."
        )
    # Fernet accepts a urlsafe-base64 32-byte key as str or bytes.
    key = CONNECTOR_SECRET_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret, returning URL-safe base64 ciphertext (str)."""
    if plaintext is None:
        raise ValueError("Cannot encrypt None")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt ciphertext produced by encrypt_secret back to plaintext."""
    if not ciphertext:
        raise ValueError("Cannot decrypt empty ciphertext")
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt connector secret — wrong/rotated CONNECTOR_SECRET_KEY?")
        raise


def mask_secret(plaintext: Optional[str], visible: int = 4) -> Optional[str]:
    """Return a masked representation (e.g. '••••wxyz') safe to send to clients.

    Never returns the full secret. None in -> None out.
    """
    if not plaintext:
        return None
    tail = plaintext[-visible:] if len(plaintext) > visible else ""
    return f"{'•' * 4}{tail}"
