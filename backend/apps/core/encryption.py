"""Symmetric encryption for secrets stored at rest (integration credentials).

Uses Fernet (AES-128-CBC + HMAC-SHA256) via MultiFernet so keys can be rotated:
the first key in FIELD_ENCRYPTION_KEYS encrypts, all keys can decrypt.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in settings.FIELD_ENCRYPTION_KEYS])


def encrypt_str(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_str(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - indicates key misconfiguration
        raise ValueError("Unable to decrypt value: invalid key or corrupted data") from exc


def rotate_token(token: str) -> str:
    return _fernet().rotate(token.encode()).decode()


class EncryptedJSONField(models.TextField):
    """Stores a JSON-serialisable dict encrypted at rest.

    The Python value is a dict; the DB value is a Fernet token. Values are never
    rendered by serializers — use ``mask_secrets`` for display.
    """

    description = "Encrypted JSON"

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return {}
        return json.loads(decrypt_str(value))

    def to_python(self, value):
        if isinstance(value, dict):
            return value
        if value in (None, ""):
            return {}
        try:
            return json.loads(decrypt_str(value))
        except ValueError:
            return json.loads(value)

    def get_prep_value(self, value):
        if value in (None, "", {}):
            return ""
        return encrypt_str(json.dumps(value, sort_keys=True))


def mask_secrets(data: dict[str, Any], secret_keys: set[str] | None = None) -> dict[str, Any]:
    """Return a display-safe version: secrets replaced by a 'configured' marker."""
    out: dict[str, Any] = {}
    for k, v in (data or {}).items():
        if secret_keys is None or k in secret_keys:
            out[k] = {"configured": bool(v), "last4": (str(v)[-4:] if v and len(str(v)) >= 12 else None)}
        else:
            out[k] = v
    return out
