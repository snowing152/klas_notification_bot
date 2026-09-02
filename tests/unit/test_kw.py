import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from app.services import kw as kw_service
from app.services.kw import KwangwoonUniversityApi

URL_UNDER_TEST = "https://klas.kw.ac.kr/x.do"


def _content_type_error(url):
    """Build the error aiohttp raises when a 200 body is not JSON.

    request_info has to be real: ContentTypeError.__str__ dereferences it, so a
    None placeholder would blow up inside the logging call under test.
    """
    request_info = aiohttp.RequestInfo(
        URL(url), "POST", CIMultiDictProxy(CIMultiDict()), URL(url)
    )
    return aiohttp.ContentTypeError(
        request_info, (), message="Attempt to decode JSON with unexpected mimetype: "
    )


class FakeResponse:
    """Minimal stand-in for aiohttp's response context manager."""

    def __init__(self, status=200, payload=None, raises=None):
        self.status = status
        self._payload = payload
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def json(self):
        if self._raises:
            raise self._raises
        return self._payload


class ConnectionFailure:
    """A request that dies on the wire, the way a dropped keep-alive does."""

    def __init__(self, error=None):
        self._error = error or aiohttp.ServerDisconnectedError()

    async def __aenter__(self):
        raise self._error

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


class FakeSession:
    """Hands out the given outcomes in order, repeating the last one."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def post(self, *args, **kwargs):
        outcome = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        return outcome


@pytest.fixture(autouse=True)
def instant_retries(monkeypatch):
    """The retry delay is real seconds in production; don't sleep them here."""
    monkeypatch.setattr(kw_service, "RETRY_DELAY_SECONDS", 0)


def _api(*outcomes):
    api = KwangwoonUniversityApi()
    api.cookies = {"JSESSIONID": "test"}
    api.session = FakeSession(*outcomes)
    return api


async def test_post_json_returns_parsed_body():
    api = _api(FakeResponse(payload={"kname": "홍길동"}))

    assert await api._post_json(URL_UNDER_TEST, {}) == {"kname": "홍길동"}


async def test_post_json_returns_none_on_non_json_body():
    """Regression: KLAS answers 200 with an empty body and no Content-Type for
    a student with no grade record yet, and the resulting ContentTypeError used
    to escape get_student_info and reach the /info handler as a crash."""
    api = _api(FakeResponse(raises=_content_type_error(URL_UNDER_TEST)))

    assert await api._post_json(URL_UNDER_TEST, {}) is None
    assert api.session.calls == 1, "a non-JSON body is an answer, not a retry"


async def test_post_json_returns_none_on_error_status():
    api = _api(FakeResponse(status=500))

    assert await api._post_json(URL_UNDER_TEST, {}) is None
    assert api.session.calls == 1


async def test_post_json_retries_a_dropped_connection():
    """KLAS drops idle keep-alive connections; the retry gets a fresh one."""
    api = _api(ConnectionFailure(), FakeResponse(payload=[{"value": "20252"}]))

    assert await api._post_json(URL_UNDER_TEST, {}) == [{"value": "20252"}]
    assert api.session.calls == 2


async def test_post_json_reraises_once_retries_run_out():
    api = _api(ConnectionFailure())

    with pytest.raises(aiohttp.ServerDisconnectedError):
        await api._post_json(URL_UNDER_TEST, {})

    assert api.session.calls == kw_service.REQUEST_RETRIES + 1


async def test_post_json_retries_a_timeout():
    api = _api(ConnectionFailure(TimeoutError()), FakeResponse(payload={"ok": True}))

    assert await api._post_json(URL_UNDER_TEST, {}) == {"ok": True}


async def test_student_info_request_returns_none_without_cookies():
    api = KwangwoonUniversityApi()

    assert await api._make_student_info_request(URL_UNDER_TEST) is None


async def test_get_subjects_unwraps_the_first_entry():
    api = _api(FakeResponse(payload=[{"value": "20252", "subjList": []}]))

    assert await api.get_subjects() == {"value": "20252", "subjList": []}


async def test_get_subjects_returns_none_on_empty_payload():
    api = _api(FakeResponse(payload=[]))

    assert await api.get_subjects() is None
