"""Field-level encryption at rest for PII (claimant name, phone) stored in
claims.csv and result.json. Uses Fernet (symmetric, authenticated) from the
`cryptography` package — no system dependencies, runs fine on a laptop.

Graceful fallback: if ENCRYPTION_KEY is not set in .env, encrypt/decrypt are
no-ops so the app still runs (e.g. on a fresh checkout before key generation).
"""

from cryptography.fernet import Fernet, InvalidToken

from config import ENCRYPTION_KEY

_fernet = Fernet(ENCRYPTION_KEY.encode()) if ENCRYPTION_KEY else None

ENC_PREFIX = "enc:"  # marks a field as ciphertext so plaintext rows from before
                      # encryption was enabled (or when no key is set) still read fine


def encrypt_field(value: str) -> str:
    if not _fernet or not value:
        return value
    return ENC_PREFIX + _fernet.encrypt(value.encode()).decode()


def decrypt_field(value: str) -> str:
    if not value or not value.startswith(ENC_PREFIX):
        return value
    if not _fernet:
        return value  # can't decrypt without the key — return ciphertext as-is
    try:
        return _fernet.decrypt(value[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        return value
