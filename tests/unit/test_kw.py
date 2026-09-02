import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from app.services.kw import KwangwoonUniversityApi


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


class FakeSession:
    def __init__(self, response):
        self._response = response

    def post(self, *args, **kwargs):
        return self._response


def _api(response):
    api = KwangwoonUniversityApi()
    api.cookies = {"JSESSIONID": "test"}
    api.session = FakeSession(response)
    return api


async def test_student_info_request_returns_parsed_json():
    api = _api(FakeResponse(payload={"kname": "홍길동"}))

    assert await api._make_student_info_request("https://klas.kw.ac.kr/x.do") == {
        "kname": "홍길동"
    }


async def test_student_info_request_returns_none_on_non_json_body():
    """Regression: KLAS answers 200 with an empty body and no Content-Type for
    a student with no grade record yet, and the resulting ContentTypeError used
    to escape get_student_info and reach the /info handler as a crash."""
    api = _api(FakeResponse(raises=_content_type_error("https://klas.kw.ac.kr/x.do")))

    assert await api._make_student_info_request("https://klas.kw.ac.kr/x.do") is None


async def test_student_info_request_returns_none_on_error_status():
    api = _api(FakeResponse(status=500))

    assert await api._make_student_info_request("https://klas.kw.ac.kr/x.do") is None


async def test_student_info_request_returns_none_without_cookies():
    api = KwangwoonUniversityApi()

    assert await api._make_student_info_request("https://klas.kw.ac.kr/x.do") is None
