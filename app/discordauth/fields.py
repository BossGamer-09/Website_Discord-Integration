"""
app/discordauth/fields.py

SEC-06: transparently Fernet-encrypts a TextField at rest.
- In Python the value is always plaintext (set/get behave normally).
- In the database it is always ciphertext (Fernet token, starts with "gAAAAA").
- On read, legacy plaintext (rows written before encryption was enabled) is tolerated and
  returned unchanged, so existing rows can be migrated in place with no flag-day.

Once 0004_encrypt_tokens has run and every row is confirmed ciphertext, you may tighten
from_db_value to re-raise instead of passing plaintext through.

Requires: `cryptography` and settings.TOKEN_ENC_KEY (a Fernet key).
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet() -> Fernet:
    key = settings.TOKEN_ENC_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


class EncryptedTextField(models.TextField):
    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            # Legacy plaintext written before encryption was enabled — pass through so the
            # backfill migration can read and re-save (encrypt) it. Tighten after migration.
            return value
