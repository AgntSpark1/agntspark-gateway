"""Encryption for secret-flagged agent environment variables.

This is the gateway's "secret store" for the MVP: values with
``EnvVar.secret=True`` are Fernet-encrypted before being persisted in the
``agents`` table and only decrypted in-process right before being injected
into a container's environment at deploy time. A dedicated secrets service
(e.g. Vault) is future work — see the README follow-ups.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings
from ..exceptions import AgntSparkError

_fernet = Fernet(settings.secret_encryption_key.encode("utf-8"))


class SecretDecryptionError(AgntSparkError):
    def __init__(self) -> None:
        super().__init__(
            "Failed to decrypt a stored secret — the encryption key may have changed.",
            code="SECRET_DECRYPTION_FAILED",
        )


def encrypt_secret(raw_value: str) -> str:
    return _fernet.encrypt(raw_value.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted_value: str) -> str:
    try:
        return _fernet.decrypt(encrypted_value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretDecryptionError() from exc


__all__ = ["encrypt_secret", "decrypt_secret", "SecretDecryptionError"]
