import pytest
from app.utils.encryption import encrypt_password, decrypt_password


@pytest.mark.asyncio
async def test_encryption():
    # Test data
    original_password = "test_password123"

    # Test encryption
    encrypted = encrypt_password(original_password)
    assert encrypted != original_password

    # Test decryption
    decrypted = decrypt_password(encrypted)
    assert decrypted == original_password

    # Test different passwords produce different encryptions
    another_encrypted = encrypt_password(original_password)
    assert encrypted != another_encrypted


@pytest.mark.asyncio
async def test_invalid_decryption():
    with pytest.raises(Exception):
        decrypt_password("invalid_encrypted_password")


def test_generate_key_creates_the_file_and_never_overwrites_it(tmp_path, monkeypatch):
    from app.config import settings
    from app.utils import encryption

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))

    key = encryption.generate_key()
    path = tmp_path / encryption.KEY_FILENAME
    assert path.read_bytes() == key

    with pytest.raises(FileExistsError):
        encryption.generate_key()
    assert path.read_bytes() == key, "the existing key must survive"
