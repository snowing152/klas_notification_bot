import functools
import logging
import os

from cryptography.fernet import Fernet

from app.config import settings

KEY_FILENAME = "encryption_key.key"


def _key_path() -> str:
    return os.path.join(settings.DATA_DIR, KEY_FILENAME)


def generate_key() -> bytes:
    """Create <DATA_DIR>/encryption_key.key with a new Fernet key and return it.

    Refuses to touch an existing file: replacing the key makes every stored
    password permanently undecryptable. O_EXCL makes the check and the create
    one atomic step. On POSIX 0o600 keeps the key unreadable to other local
    users; on Windows the mode only toggles the read-only bit, so protect the
    data directory there. Raises FileExistsError when the file is already there.
    """
    key = Fernet.generate_key()
    # O_BINARY exists only on Windows, where text mode would rewrite newlines.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(_key_path(), flags, 0o600)
    with os.fdopen(fd, "wb") as key_file:
        key_file.write(key)
    return key


def _warn_if_key_file_is_open(path: str) -> None:
    """Log a warning when other local users can read the key file (POSIX only)."""
    if os.name != "posix":
        return
    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:
        logging.warning(
            f"{path} is readable by other users (mode {mode:o}); run: chmod 600 {path}"
        )


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
    _warn_if_key_file_is_open(path)
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


def encrypt_secret(plaintext: str) -> str:
    """Encrypt any secret text with the same key as the passwords.

    Returns the Fernet token as text; it is ASCII, so it fits a String column.
    """
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Reverse encrypt_secret(). Raises cryptography's InvalidToken on a bad token."""
    return _cipher().decrypt(token).decode()
