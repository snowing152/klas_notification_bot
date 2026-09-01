import asyncio

import pytest

from app.services import qr as qr_service


@pytest.fixture
def fake_qr_backend(monkeypatch):
    """Stub out the mobileid.kw.ac.kr calls so only local behavior is exercised."""

    async def fake_get_secret_key(real_id):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret):
        return f"auth-{std_number}"

    async def fake_get_qr_code(real_id, auth_key):
        return {"qr_code": f"payload-for-{real_id}"}

    monkeypatch.setattr(qr_service, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(qr_service, "library_login", fake_library_login)
    monkeypatch.setattr(qr_service, "get_qr_code", fake_get_qr_code)


@pytest.mark.asyncio
async def test_get_qr_writes_to_the_requested_path(tmp_path, fake_qr_backend):
    target = tmp_path / "mine.png"

    result = await qr_service.get_qr("2021123456", "01000000000", "pw", str(target))

    assert result == str(target)
    assert target.exists() and target.stat().st_size > 0


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_share_a_file(tmp_path, fake_qr_backend):
    """Regression: every user used to render into the same images/qr.png,
    so one student could be sent another student's library QR code."""
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"

    result_a, result_b = await asyncio.gather(
        qr_service.get_qr("1111", "010", "pw", str(path_a)),
        qr_service.get_qr("2222", "010", "pw", str(path_b)),
    )

    assert result_a != result_b
    assert path_a.exists() and path_b.exists()
    assert path_a.read_bytes() != path_b.read_bytes()


@pytest.mark.asyncio
async def test_get_qr_returns_none_on_failure(tmp_path, monkeypatch):
    async def boom(real_id):
        raise RuntimeError("network down")

    monkeypatch.setattr(qr_service, "get_secret_key", boom)

    assert await qr_service.get_qr("1", "2", "3", str(tmp_path / "x.png")) is None
