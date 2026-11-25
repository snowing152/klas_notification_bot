import asyncio
import logging

import pytest

from app.services import qr as qr_service


class FakeQrResponse:
    """Minimal stand-in for aiohttp's response context manager, at the level
    library_login/get_qr_code actually touch it."""

    def __init__(self, status=200, text=""):
        self.status = status
        self._text = text
        self.text_encoding = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def text(self, encoding=None):
        self.text_encoding = encoding
        return self._text


class FakeQrSession:
    """Hands back one canned response and counts how many times it's used."""

    def __init__(self, response):
        self._response = response
        self.post_calls = 0

    def post(self, *args, **kwargs):
        self.post_calls += 1
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _stub_get_session(monkeypatch, session):
    monkeypatch.setattr(qr_service, "new_session", lambda: session)


@pytest.fixture
def fake_qr_backend(monkeypatch):
    """Stub out the mobileid.kw.ac.kr calls so only local behavior is exercised."""

    async def fake_get_secret_key(real_id, session=None):
        return "sixteencharkeyxx"

    async def fake_library_login(std_number, phone, password, secret, session=None):
        return f"auth-{std_number}"

    async def fake_get_qr_code(real_id, auth_key, session=None):
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
    async def boom(real_id, session=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(qr_service, "get_secret_key", boom)

    assert await qr_service.get_qr("1", "2", "3", str(tmp_path / "x.png")) is None


# The XML shape mobileid.kw.ac.kr actually sends back on a rejected login
# (see logs.1789530965721.log, 2026-09-15 08:04): result_code=1 means "this
# isn't the phone number registered to this library account", and the
# service embeds that phone number into its own result_msg text.
REJECTED_LOGIN_XML = (
    "<root><item>"
    "<user_id><![CDATA[02026323541]]></user_id>"
    "<result_code><![CDATA[1]]></result_code>"
    "<result_msg><![CDATA[도서관에 등록된 휴대폰으로만 로그인 가능합니다."
    "(도용 및 중복사용 방지)01080681718]]></result_msg>"
    "<auth_key><![CDATA[]]></auth_key>"
    "</item></root>"
)


@pytest.mark.asyncio
async def test_library_login_raises_rejected_with_reason(monkeypatch, caplog):
    """Regression: a rejected login used to come back as a bare None, so the
    caller couldn't tell a wrong phone number apart from any other failure."""
    session = FakeQrSession(FakeQrResponse(status=200, text=REJECTED_LOGIN_XML))
    _stub_get_session(monkeypatch, session)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(qr_service.LibraryLoginRejected) as exc_info:
            await qr_service.library_login("2021123456", "01000000000", "pw", "sixteencharkeyxx")

    err = exc_info.value
    assert err.result_code == "1"
    # The student's own phone number is preserved for display back to them...
    assert "01080681718" in err.result_msg
    # ...but never lands in the exception's own string form, since that's
    # what a bare `logging.error(e)` would print.
    assert "01080681718" not in str(err)
    assert all("01080681718" not in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_library_login_decodes_response_as_utf8(monkeypatch):
    """Regression: this used to hardcode iso-8859-1, turning every non-ASCII
    byte in the response (e.g. result_msg's Korean) into mojibake."""
    response = FakeQrResponse(status=200, text=REJECTED_LOGIN_XML)
    session = FakeQrSession(response)
    _stub_get_session(monkeypatch, session)

    with pytest.raises(qr_service.LibraryLoginRejected):
        await qr_service.library_login("2021123456", "01000000000", "pw", "sixteencharkeyxx")

    assert response.text_encoding == "utf-8"


@pytest.mark.asyncio
async def test_library_login_raises_on_bad_status(monkeypatch):
    session = FakeQrSession(FakeQrResponse(status=503, text=""))
    _stub_get_session(monkeypatch, session)

    with pytest.raises(qr_service.LibraryLoginError):
        await qr_service.library_login("2021123456", "01000000000", "pw", "sixteencharkeyxx")


@pytest.mark.asyncio
async def test_library_login_raises_on_invalid_xml(monkeypatch):
    session = FakeQrSession(FakeQrResponse(status=200, text="not xml at all"))
    _stub_get_session(monkeypatch, session)

    with pytest.raises(qr_service.LibraryLoginError):
        await qr_service.library_login("2021123456", "01000000000", "pw", "sixteencharkeyxx")


@pytest.mark.asyncio
async def test_get_qr_returns_none_when_login_is_rejected(tmp_path, monkeypatch):
    """get_qr's blanket except still needs to catch the new exception type,
    not just plain RuntimeError, so a rejected login doesn't crash a cycle."""

    async def fake_get_secret_key(real_id, session=None):
        return "k"

    async def rejected(std_number, phone, password, secret, session=None):
        raise qr_service.LibraryLoginRejected("1", "wrong phone number")

    monkeypatch.setattr(qr_service, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(qr_service, "library_login", rejected)

    assert await qr_service.get_qr("1", "2", "3", str(tmp_path / "x.png")) is None


@pytest.mark.asyncio
async def test_get_qr_code_uses_the_session_it_is_given(monkeypatch):
    session = FakeQrSession(
        FakeQrResponse(status=200, text="<root><qr_code><![CDATA[abc]]></qr_code></root>")
    )

    def must_not_be_called():
        raise AssertionError("get_qr_code must not open its own ClientSession")

    monkeypatch.setattr(qr_service, "new_session", must_not_be_called)

    result = await qr_service.get_qr_code("002021123456", "authkey", session=session)

    assert result["qr_code"] == "abc"
    assert session.post_calls == 1


@pytest.mark.asyncio
async def test_each_get_qr_call_gets_its_own_session(tmp_path, monkeypatch):
    """Users must never share a cookie jar."""
    opened = []

    class Session:
        async def __aenter__(self):
            opened.append(self)
            return self

        async def __aexit__(self, *exc):
            return False

    async def fake_get_secret_key(real_id, session=None):
        return "k"

    async def fake_library_login(std_number, phone, password, secret, session=None):
        return "auth"

    async def fake_get_qr_code(real_id, auth_key, session=None):
        return {"qr_code": "payload"}

    monkeypatch.setattr(qr_service, "new_session", Session)
    monkeypatch.setattr(qr_service, "get_secret_key", fake_get_secret_key)
    monkeypatch.setattr(qr_service, "library_login", fake_library_login)
    monkeypatch.setattr(qr_service, "get_qr_code", fake_get_qr_code)

    await qr_service.get_qr("1111", "010", "pw", str(tmp_path / "a.png"))
    await qr_service.get_qr("2222", "010", "pw", str(tmp_path / "b.png"))

    assert len(opened) == 2 and opened[0] is not opened[1]


def test_new_session_has_a_timeout():
    import asyncio as _asyncio

    async def check():
        async with qr_service.new_session() as session:
            return session.timeout.total

    assert _asyncio.run(check()) == 30
