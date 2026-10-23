"""Encryption utilities for BYOK provider credential storage, ported from
fs-agent-usage-limits' utils/encryption.py.

Uses this repo's own env loading (utils/setup_env.py) instead of duplicating a
second dotenv load — USAGE_CREDENTIALS_MASTER_KEY / USAGE_CREDENTIALS_HASH_SALT
must be set the same way CONNECTOR_SECRET_KEY already is.

- Fernet symmetric encryption for the API key itself (encrypt/decrypt).
- HMAC-SHA256 (server-side salt) for a duplicate-detection hash — never used
  to recover the key, only to compare.
- A masked display form (first 3 + last 4 chars).
"""

import hmac
import hashlib
from typing import Tuple

from cryptography.fernet import Fernet

from utils.logger import logger
from utils.setup_env import USAGE_CREDENTIALS_MASTER_KEY, USAGE_CREDENTIALS_HASH_SALT


def get_master_key() -> bytes:
    if not USAGE_CREDENTIALS_MASTER_KEY:
        raise ValueError("USAGE_CREDENTIALS_MASTER_KEY environment variable is not set")
    return USAGE_CREDENTIALS_MASTER_KEY.encode("utf-8")


def get_hash_salt() -> bytes:
    if not USAGE_CREDENTIALS_HASH_SALT:
        raise ValueError("USAGE_CREDENTIALS_HASH_SALT environment variable is not set")
    return USAGE_CREDENTIALS_HASH_SALT.encode("utf-8")


def encrypt_api_key(api_key: str) -> Tuple[str, str]:
    """Encrypt an API key and compute its duplicate-detection hash.

    Returns:
        (encrypted_key, key_hash) — encrypted_key is Fernet ciphertext (base64
        string), key_hash is a 64-char hex HMAC-SHA256 digest.
    """
    try:
        fernet = Fernet(get_master_key())
        encrypted_key = fernet.encrypt(api_key.encode("utf-8")).decode("utf-8")
        key_hash = hmac.new(get_hash_salt(), api_key.encode("utf-8"), hashlib.sha256).hexdigest()
        logger.info(f"Encrypted API key (hash prefix: {key_hash[:8]}...)")
        return encrypted_key, key_hash
    except Exception as e:
        logger.error(f"Encryption failed: {e}", exc_info=True)
        raise


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt a Fernet-encrypted API key. Raises on invalid/corrupted ciphertext."""
    try:
        fernet = Fernet(get_master_key())
        return fernet.decrypt(encrypted_key.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error(f"Decryption failed: {e}", exc_info=True)
        raise


def generate_key_hash(api_key: str) -> str:
    return hmac.new(get_hash_salt(), api_key.encode("utf-8"), hashlib.sha256).hexdigest()


def mask_api_key(api_key: str) -> str:
    """First 3 chars + '...' + last 4 chars, e.g. 'sk-...48sA'."""
    if len(api_key) <= 7:
        return api_key
    return f"{api_key[:3]}...{api_key[-4:]}"
