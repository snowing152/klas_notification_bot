import os
import functools

from cryptography.fernet import Fernet

from app.config import settings

KEY_FILENAME = "encryption_key.key"


def _key_path() -> str:
    return os.path.join(settings.DATA_DIR, KEY_FILENAME)


def generate_key():
    key = Fernet.generate_key()
    with open(_key_path(), "wb") as key_file:
        key_file.write(key)
    return key


def load_key() -> bytes:
    """Return the Fernet key, preferring ENCRYPTION_KEY over the key file.

    The env var matters on hosts with an ephemeral filesystem: a regenerated key
    makes every password already in the database undecryptable, with no recovery.
    """
    if settings.ENCRYPTION_KEY:
        return settings.ENCRYPTION_KEY.encode()

    path = _key_path()
    if not os.path.exists(path):
        raise RuntimeError(
            f"No encryption key found. Set ENCRYPTION_KEY, or generate {path} with:\n"
            '  python -c "from app.utils.encryption import generate_key; generate_key()"'
        )
    with open(path, "rb") as key_file:
        return key_file.read()


@functools.lru_cache(maxsize=1)
def _cipher() -> Fernet:
    # Built once instead of re-reading the key file on every encrypt/decrypt
    return Fernet(load_key())


def encrypt_password(password):
    return _cipher().encrypt(password.encode())


def decrypt_password(encrypted_password):
    return _cipher().decrypt(encrypted_password).decode()
