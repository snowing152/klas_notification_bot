import asyncio
import base64
import datetime

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from app.services import kw as kw_service
from app.services.kw import KwangwoonUniversityApi
from app.handlers.todos import TYPE_EMOJIS as show_emojis
from app.services.notifications import TYPE_EMOJIS as notify_emojis

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

    def __init__(self, status=200, payload=None, raises=None, url=None, text=None):
        self.status = status
        self.url = url
        self._payload = payload
        self._raises = raises
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def json(self):
        if self._raises:
            raise self._raises
        return self._payload

    async def text(self, **kwargs):
        if self._raises:
            raise self._raises
        return self._text


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


@pytest.fixture(autouse=True)
def own_request_slots(monkeypatch):
    """The request cap is one semaphore for the whole process, and a semaphore
    that ever made someone wait belongs to that test's event loop from then on."""
    monkeypatch.setattr(
        kw_service,
        "_request_slots",
        asyncio.Semaphore(kw_service.MAX_CONCURRENT_REQUESTS),
    )


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


LOGIN_FORM_URL = kw_service.LOGIN_FORM_URL
FRAME_URL = "https://klas.kw.ac.kr/std/cmn/frame/Frame.do"


class FakeCookie:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class FakeLoginSession:
    """Serves KLAS's login endpoints, each from its own queue of answers.

    Routing by URL instead of by call order keeps a test readable when the
    login gains a step, as it did with LoginCaptcha.do and the push requests.
    Each queue repeats its last answer once it runs out.
    """

    def __init__(self, routes=None, form_url=LOGIN_FORM_URL):
        self._routes = {url: list(outcomes) for url, outcomes in (routes or {}).items()}
        self._form_url = form_url
        self.cookie_jar = [FakeCookie("JSESSIONID", "fresh")]
        self.posted = []
        self.got = []

    def get(self, url, *args, **kwargs):
        self.got.append(url)
        if url == kw_service.UPDATE_SESSION_URL:
            return FakeResponse(status=304)
        # Answering from the form's own URL means "not logged in"; any other
        # final URL is KLAS redirecting a valid session away from the form.
        return FakeResponse(url=self._form_url)

    def post(self, url=None, *args, **kwargs):
        self.posted.append((url, kwargs))
        outcomes = self._routes.get(url)
        if not outcomes:
            raise AssertionError(f"unexpected POST to {url}")
        calls = sum(1 for posted_url, _ in self.posted if posted_url == url)
        return outcomes[min(calls - 1, len(outcomes) - 1)]

    def posts_to(self, url):
        return [kwargs for posted_url, kwargs in self.posted if posted_url == url]


def _login_api(routes=None, form_url=LOGIN_FORM_URL):
    api = KwangwoonUniversityApi()
    api.session = FakeLoginSession(routes, form_url=form_url)
    # The real encryptor needs a valid RSA key; the login flow under test does
    # not care what the token contains.
    api._encryptor = lambda public_key, data: "encrypted-token"
    return api


PUBLIC_KEY = FakeResponse(payload={"publicKey": "test-key"})
NO_CAPTCHA = FakeResponse(text="0")
# LoginConfirm.do's answers, as captured from a browser login on 2026-11-24.
TWO_FACTOR_REQUIRED = FakeResponse(
    payload={
        "redirectUrl": "",
        "fieldErrors": [],
        "responseText": "",
        "response": {"twoFactorAt": "Y"},
        "errorCount": 0,
        "redirect": False,
        "loginRequired": False,
    }
)
LOGGED_IN = FakeResponse(
    payload={
        "redirectUrl": "/std/cmn/frame/Frame.do",
        "fieldErrors": [],
        "responseText": "",
        "response": {"frstPwdAt": None, "pushToken": None, "userId": "2024000000UA"},
        "errorCount": 0,
        "redirect": True,
        "loginRequired": False,
    }
)


def _credential_routes(*confirm_outcomes):
    return {
        kw_service.LOGIN_SECURITY_URL: [PUBLIC_KEY],
        kw_service.LOGIN_CAPTCHA_URL: [NO_CAPTCHA],
        kw_service.LOGIN_CONFIRM_URL: list(confirm_outcomes),
    }


async def test_login_retries_a_dropped_connection():
    """Regression: KLAS dropping the login POST used to leave cookies empty,
    and every later call reported 'No cookies found' instead of retrying."""
    api = _login_api(_credential_routes(ConnectionFailure(), LOGGED_IN))

    assert await api.login("student", "pw") == {"JSESSIONID": "fresh"}
    assert len(api.session.posts_to(kw_service.LOGIN_CONFIRM_URL)) == 2


async def test_login_returns_none_when_the_connection_never_recovers():
    api = _login_api({kw_service.LOGIN_SECURITY_URL: [ConnectionFailure()]})

    assert await api.login("student", "pw") is None
    assert (
        len(api.session.posts_to(kw_service.LOGIN_SECURITY_URL))
        == kw_service.REQUEST_RETRIES + 1
    )
    assert api.cookies == {}


async def test_unreachable_klas_is_not_a_wrong_password():
    """A student must not be told their password is wrong during an outage."""
    api = _login_api({kw_service.LOGIN_SECURITY_URL: [ConnectionFailure()]})

    result = await api.attempt_login("student", "pw")

    assert result.status is kw_service.LoginStatus.UNAVAILABLE


async def test_an_error_status_from_login_confirm_is_unavailable():
    api = _login_api(_credential_routes(FakeResponse(status=502)))

    result = await api.attempt_login("student", "pw")

    assert result.status is kw_service.LoginStatus.UNAVAILABLE


async def test_wrong_password_is_failed():
    api = _login_api(
        _credential_routes(
            FakeResponse(payload={"errorCount": 1, "fieldErrors": [{"message": "nope"}]})
        )
    )

    result = await api.attempt_login("student", "wrong")

    assert result.status is kw_service.LoginStatus.FAILED


async def test_login_returns_none_on_wrong_password():
    api = _login_api(
        _credential_routes(
            FakeResponse(payload={"errorCount": 1, "fieldErrors": [{"message": "nope"}]})
        )
    )

    assert await api.login("student", "wrong") is None


async def test_login_failure_without_field_errors_does_not_crash():
    """The old parser indexed fieldErrors[0] and raised on an empty list."""
    api = _login_api(_credential_routes(FakeResponse(payload={"errorCount": 1})))

    result = await api.attempt_login("student", "pw")

    assert result.status is kw_service.LoginStatus.FAILED


async def test_login_with_valid_cookies_returns_them():
    """A redirect away from the login form means the session is still good.
    This path used to return a bare 1, which no caller could use as cookies."""
    api = _login_api(form_url=FRAME_URL)

    assert await api.login("student", "pw") == {"JSESSIONID": "fresh"}
    assert api.session.posted == [], "an already-valid session needs no login round trip"


async def test_login_sends_the_captcha_check_like_the_browser():
    api = _login_api(_credential_routes(LOGGED_IN))

    await api.login("student", "pw")

    (captcha_call,) = api.session.posts_to(kw_service.LOGIN_CAPTCHA_URL)
    assert captcha_call["json"] == {"loginToken": "encrypted-token", "captcha": ""}
    (confirm_call,) = api.session.posts_to(kw_service.LOGIN_CONFIRM_URL)
    assert confirm_call["json"]["loginToken"] == "encrypted-token"
    assert confirm_call["json"]["captcha"] == ""


async def test_two_factor_answer_is_not_a_finished_login():
    """Regression: LoginConfirm.do answers errorCount 0 when it wants the push,
    and login() used to hand back the cookies of that half-open session."""
    api = _login_api(_credential_routes(TWO_FACTOR_REQUIRED))

    result = await api.attempt_login("student", "pw")

    assert result.status is kw_service.LoginStatus.TWO_FACTOR_REQUIRED
    assert result.cookies is None


async def test_login_answers_none_when_two_factor_is_required():
    api = _login_api(_credential_routes(TWO_FACTOR_REQUIRED))

    assert await api.login("student", "pw") is None


# UserCertPushResult.do's inline script, trimmed from a captured page.
PUSH_PAGE = """
<script type="text/javascript">
function fnResult() {
	$.ajax({
		url: "/ext/pass/UserCertPushChk.do",
		type: "POST",
		contentType: 'application/json',
		data : JSON.stringify({ 'loginId' : '2024000000', 'tid' : 'c03db56f3c9d4842afd5333623471974', 'limit' : '1795492496562'}),
	});
}
</script>
"""
CERT_INFO_REGISTERED = FakeResponse(
    payload={
        "loginId": None,
        "returnCode": "0000",
        "tid": None,
        "limit": None,
        "status": "1",
        "issueDateTime": "2026-09-07 15:30:39",
        "deviceOS": "I",
        "deviceModel": "iPhone",
    }
)
PUSH_APPROVED = FakeResponse(payload={"returnCode": "0000", "status": None})
PUSH_PENDING = FakeResponse(payload={"returnCode": "9999", "status": None})


def test_push_page_parser_reads_the_push_id():
    assert kw_service._parse_push_page(PUSH_PAGE) == (
        "c03db56f3c9d4842afd5333623471974",
        "1795492496562",
    )


def test_push_page_parser_answers_none_without_a_push_id():
    assert kw_service._parse_push_page("<html>세션이 만료되었습니다</html>") is None


def _push_api(routes):
    api = _login_api(routes)
    api.login_id = "2024000000"
    return api


async def test_request_push_returns_the_push_id():
    api = _push_api(
        {
            kw_service.CERT_INFO_URL: [CERT_INFO_REGISTERED],
            kw_service.CERT_PUSH_PAGE_URL: [FakeResponse(text=PUSH_PAGE)],
        }
    )

    push = await api.request_push()

    assert push == kw_service.PushRequest(
        kw_service.PushStatus.SENT,
        tid="c03db56f3c9d4842afd5333623471974",
        limit="1795492496562",
    )
    (cert_call,) = api.session.posts_to(kw_service.CERT_INFO_URL)
    assert cert_call["json"]["userId"] == "2024000000UA"
    assert cert_call["json"]["certOpt"] == "smart"
    (page_call,) = api.session.posts_to(kw_service.CERT_PUSH_PAGE_URL)
    assert page_call["params"] == {"loginId": "2024000000"}


async def test_request_push_without_a_registered_phone():
    """The KLAS page only offers the push when status is "1"."""
    api = _push_api(
        {
            kw_service.CERT_INFO_URL: [
                FakeResponse(payload={"returnCode": "0000", "status": "0"})
            ],
        }
    )

    push = await api.request_push()

    assert push.status is kw_service.PushStatus.NO_DEVICE
    assert api.session.posts_to(kw_service.CERT_PUSH_PAGE_URL) == []


async def test_request_push_fails_when_the_page_has_no_push_id():
    api = _push_api(
        {
            kw_service.CERT_INFO_URL: [CERT_INFO_REGISTERED],
            kw_service.CERT_PUSH_PAGE_URL: [FakeResponse(text="<html></html>")],
        }
    )

    assert (await api.request_push()).status is kw_service.PushStatus.FAILED


async def test_request_push_before_login_fails_cleanly():
    api = _login_api()

    assert (await api.request_push()).status is kw_service.PushStatus.FAILED


SENT_PUSH = kw_service.PushRequest(kw_service.PushStatus.SENT, tid="abc", limit="1")


async def test_approved_push_finishes_the_login():
    routes = _credential_routes(LOGGED_IN)
    routes[kw_service.CERT_PUSH_CHECK_URL] = [PUSH_PENDING, PUSH_APPROVED]
    api = _push_api(routes)

    result = await api.wait_for_push_approval("pw", SENT_PUSH, interval=0)

    assert result.status is kw_service.LoginStatus.OK
    assert result.cookies == {"JSESSIONID": "fresh"}
    (check_call, _) = api.session.posts_to(kw_service.CERT_PUSH_CHECK_URL)
    assert check_call["json"] == {"loginId": "2024000000", "tid": "abc", "limit": "1"}
    assert len(api.session.posts_to(kw_service.LOGIN_CONFIRM_URL)) == 1, (
        "the password goes out again only once the push reads as approved"
    )


async def test_push_check_alone_is_not_trusted():
    """If UserCertPushChk.do says approved but KLAS still wants the second
    factor, the password is not resent forever."""
    routes = _credential_routes(TWO_FACTOR_REQUIRED)
    routes[kw_service.CERT_PUSH_CHECK_URL] = [PUSH_APPROVED]
    api = _push_api(routes)

    result = await api.wait_for_push_approval("pw", SENT_PUSH, interval=0)

    assert result.status is kw_service.LoginStatus.FAILED
    assert (
        len(api.session.posts_to(kw_service.LOGIN_CONFIRM_URL))
        == kw_service.MAX_CREDENTIAL_RESUBMITS
    )


async def test_unapproved_push_times_out():
    api = _push_api({kw_service.CERT_PUSH_CHECK_URL: [PUSH_PENDING]})

    result = await api.wait_for_push_approval(
        "pw", SENT_PUSH, timeout=0.05, interval=0.01
    )

    assert result.status is kw_service.LoginStatus.TWO_FACTOR_REQUIRED
    assert api.session.posts_to(kw_service.LOGIN_CONFIRM_URL) == []


async def test_keep_alive_extends_a_live_session():
    api = _login_api(form_url=FRAME_URL)

    assert await api.keep_alive() is True
    assert api.session.got[0] == kw_service.UPDATE_SESSION_URL
    assert api.cookies == {"JSESSIONID": "fresh"}


async def test_keep_alive_reports_a_dead_session():
    api = _login_api(form_url=LOGIN_FORM_URL)

    assert await api.keep_alive() is False


async def test_keep_alive_cannot_tell_when_klas_is_unreachable():
    """None, not False: a network fault must not end a kept session."""
    api = _login_api()
    api.session.get = lambda *args, **kwargs: ConnectionFailure()

    assert await api.keep_alive() is None


class LeakyError(Exception):
    """Like an aiohttp response error: its repr carries the request headers."""

    def __repr__(self):
        return "LeakyError(headers={'Cookie': 'JSESSIONID=LIVE-SECRET'})"

    def __str__(self):
        return "400, message='Bad Request'"


async def test_a_failed_keep_alive_does_not_log_the_session_cookie(caplog):
    """Regression: keep_alive logged {e!r}, which for aiohttp's response
    errors prints the Cookie header - a live, password-equivalent session."""
    api = _login_api()
    api.session.get = lambda *args, **kwargs: ConnectionFailure(LeakyError())

    with caplog.at_level("INFO"):
        assert await api.keep_alive() is None

    assert "LIVE-SECRET" not in caplog.text
    assert "LeakyError: 400" in caplog.text


@pytest.fixture
async def rotating_klas(monkeypatch):
    """A local stand-in for KLAS that rotates JSESSIONID on UpdateSession.do
    and treats only the rotated value as logged in. Loopback only."""
    from aiohttp import web

    async def update_session(request):
        response = web.Response(text="{}")
        response.set_cookie("JSESSIONID", "NEW")
        return response

    async def login_form(request):
        if request.cookies.get("JSESSIONID") == "NEW":
            raise web.HTTPFound("/frame")
        return web.Response(text="login form")

    async def frame(request):
        return web.Response(text="frame")

    app = web.Application()
    app.router.add_get("/usr/cmn/login/UpdateSession.do", update_session)
    app.router.add_get("/usr/cmn/login/LoginForm.do", login_form)
    app.router.add_get("/frame", frame)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    monkeypatch.setattr(kw_service, "KLAS_BASE_URL", base)
    monkeypatch.setattr(
        kw_service, "UPDATE_SESSION_URL", f"{base}/usr/cmn/login/UpdateSession.do"
    )
    monkeypatch.setattr(
        kw_service, "LOGIN_FORM_URL", f"{base}/usr/cmn/login/LoginForm.do"
    )
    yield base
    await runner.cleanup()


async def test_a_cookie_klas_rotates_is_sent_from_then_on(rotating_klas):
    """Regression: every request also passed cookies=self.cookies, and aiohttp
    lets those win over the jar, so the old JSESSIONID kept going out after
    KLAS replaced it and a healthy session read as dead."""
    async with KwangwoonUniversityApi() as api:
        # The default jar refuses cookies for an IP address; this test server
        # has one, KLAS does not.
        await api.session.close()
        api.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        api.restore_cookies({"JSESSIONID": "OLD"})

        assert await api.keep_alive() is True
        assert api.klas_cookies() == {"JSESSIONID": "NEW"}


async def test_stored_cookies_round_trip_through_the_jar():
    """No request goes out: the jar works offline."""
    async with KwangwoonUniversityApi() as api:
        api.restore_cookies({"JSESSIONID": "abc", "SESSION": "def"})

        assert api.klas_cookies() == {"JSESSIONID": "abc", "SESSION": "def"}
        assert api.cookies == {"JSESSIONID": "abc", "SESSION": "def"}


async def test_cookies_of_other_hosts_are_not_stored():
    async with KwangwoonUniversityApi() as api:
        api.restore_cookies({"JSESSIONID": "abc"})
        api.session.cookie_jar.update_cookies(
            {"PHPSESSID": "photo"}, URL("https://did-3.kw.ac.kr/")
        )

        assert api.klas_cookies() == {"JSESSIONID": "abc"}


QR_PAGE = (
    '<main><iframe id="qrimg" src="https://did-3.kw.ac.kr/std/app/'
    'myidauth.php?token=abc" width="100%"></iframe></main>'
)
# A one-pixel JPEG is enough: the test only cares that the bytes round-trip.
PHOTO_BYTES = b"\xff\xd8\xffnot-really-a-jpeg"
PHOTO_B64 = base64.b64encode(PHOTO_BYTES).decode()
INFO_PAGE = (
    '<div class="col-sm-5 text-center"><p class="p-10">'
    f'<img alt="faceofperson" class="border" src="data:image/jpeg;base64,{PHOTO_B64}">'
    "</p></div>"
)
# myidauth.php answers with nothing but a client-side redirect; the mobile-ID
# session cookie it sets is the part that matters.
AUTH_REDIRECT = '<script>location.replace("myidv2_main.php?menu=qid");</script>'


class FakePhotoSession:
    """Serves the photo chain: QR page POST, then the two mobile-ID GETs."""

    def __init__(self, qr_page=QR_PAGE, auth=AUTH_REDIRECT, info=INFO_PAGE):
        self._qr_page = qr_page
        self._gets = [auth, info]
        self.requested = []

    def post(self, url, *args, **kwargs):
        self.requested.append(url)
        if isinstance(self._qr_page, (FakeResponse, ConnectionFailure)):
            return self._qr_page
        return FakeResponse(text=self._qr_page)

    def get(self, url, *args, **kwargs):
        self.requested.append(url)
        outcome = self._gets.pop(0) if self._gets else None
        if isinstance(outcome, (FakeResponse, ConnectionFailure)):
            return outcome
        return FakeResponse(text=outcome)


def _photo_api(**kwargs):
    api = KwangwoonUniversityApi()
    api.cookies = {"JSESSIONID": "test"}
    api.session = FakePhotoSession(**kwargs)
    return api


async def test_get_student_photo_decodes_the_inline_image():
    """KLAS stopped rendering the photo itself: the QR page now only embeds a
    mobile-ID iframe, and the photo there is a base64 data URI, not a URL."""
    api = _photo_api()

    assert await api.get_student_photo() == PHOTO_BYTES
    assert api.session.requested == [
        "https://klas.kw.ac.kr/std/sys/optrn/MyNumberQrStdPage.do",
        "https://did-3.kw.ac.kr/std/app/myidauth.php?token=abc",
        "https://did-3.kw.ac.kr/std/app/myidv2_main.php?menu=info",
    ]


async def test_get_student_photo_returns_none_without_cookies():
    api = _photo_api()
    api.cookies = {}

    assert await api.get_student_photo() is None
    assert api.session.requested == []


async def test_get_student_photo_returns_none_without_the_iframe():
    api = _photo_api(qr_page="<main>no iframe here</main>")

    assert await api.get_student_photo() is None
    assert len(api.session.requested) == 1, "nothing to follow without the token URL"


async def test_get_student_photo_returns_none_when_the_photo_is_missing():
    api = _photo_api(info="<div>no photo on this page</div>")

    assert await api.get_student_photo() is None


async def test_get_student_photo_returns_none_on_a_non_data_uri_source():
    api = _photo_api(info='<img alt="faceofperson" src="/assets/placeholder.png">')

    assert await api.get_student_photo() is None


async def test_get_student_photo_survives_a_dropped_connection():
    """The mobile-ID token lasts about a minute, so a retry still lands in time."""
    api = _photo_api()
    api.session._gets = [ConnectionFailure(), AUTH_REDIRECT, INFO_PAGE]

    assert await api.get_student_photo() == PHOTO_BYTES


# --- 토론 (discussions) -------------------------------------------------------
#
# The only category KLAS lists without a "submitted" flag: participation has to
# be read out of the posts themselves, one extra request per open discussion.

STUDENT_ID = "2024322505"
NOW = datetime.datetime(2026, 9, 20, 12, 0)

OPEN_DISCUSSION = {
    "tpcode": 2,
    # KLAS leaves the trailing space in; it should not reach the user.
    "title": "문안나의 논문을 읽고 토론 ",
    "started": "20260915",
    "ended": "20260921",
    "toroncnt": 9,
}
CLOSED_DISCUSSION = {
    "tpcode": 1,
    "title": "Kwon의 irregular verbs읽고 토론 ",
    "started": "20260910",
    "ended": "20260914",
    "toroncnt": 14,
}


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(kw_service.timezone, "now", lambda: NOW)


def _discussion_api(*opinion_payloads):
    api = _api(*(FakeResponse(payload=payload) for payload in opinion_payloads))
    api.login_id = STUDENT_ID
    return api


def _post(user_id, title="누군가"):
    return {"userId": f"{user_id}UA", "name": "학생", "title": title}


async def test_an_open_discussion_nobody_joined_is_reported(fixed_now):
    api = _discussion_api([])

    items = await api._get_not_done_discussions_info(
        [OPEN_DISCUSSION], "U2026291983220013", "2026,2"
    )

    assert len(items) == 1
    assert items[0]["title"] == "문안나의 논문을 읽고 토론"
    assert items[0]["expire_date"] == "2026-09-21 23:59"
    # The end date is a bare day, so the deadline is its last minute.
    assert items[0]["expire_at"] == datetime.datetime(2026, 9, 21, 23, 59)


async def test_a_discussion_the_student_already_posted_in_is_skipped(fixed_now):
    api = _discussion_api([_post("2025322001"), _post(STUDENT_ID), _post("2024322030")])

    assert (
        await api._get_not_done_discussions_info(
            [OPEN_DISCUSSION], "U2026291983220013", "2026,2"
        )
        == []
    )


async def test_participation_is_not_read_out_of_the_post_title(fixed_now):
    """Students type the "number name" title by hand, in either order, so a
    title that merely mentions this student is not their post - only userId is."""
    api = _discussion_api([_post("2025322001", title=f"{STUDENT_ID} 사비토바다야나")])

    items = await api._get_not_done_discussions_info(
        [OPEN_DISCUSSION], "U2026291983220013", "2026,2"
    )

    assert len(items) == 1


async def test_discussions_outside_their_dates_are_skipped_without_a_request(fixed_now):
    """Reading participation costs a request per discussion, so the closed ones
    must not be looked up at all."""
    api = _discussion_api([])

    assert (
        await api._get_not_done_discussions_info(
            [CLOSED_DISCUSSION], "U2026291983220013", "2026,2"
        )
        == []
    )
    assert api.session.calls == 0


async def test_a_failed_opinion_request_leaves_the_discussion_in_the_list(fixed_now):
    """A reminder about work already done beats silence about work that isn't."""
    api = _api(FakeResponse(status=500))
    api.login_id = STUDENT_ID

    items = await api._get_not_done_discussions_info(
        [OPEN_DISCUSSION], "U2026291983220013", "2026,2"
    )

    assert len(items) == 1


async def test_discussions_are_reported_when_the_login_id_is_unknown(fixed_now):
    """login_with_cookies never sees a student number; the 토론 still shows up."""
    api = _api(FakeResponse(payload=[_post(STUDENT_ID)]))

    items = await api._get_not_done_discussions_info(
        [OPEN_DISCUSSION], "U2026291983220013", "2026,2"
    )

    assert len(items) == 1


async def test_login_remembers_the_student_number():
    """The login id is the student number discussion posts are stamped with."""
    api = _api(ConnectionFailure())
    api.session.get = lambda *a, **k: ConnectionFailure()

    await api.login(STUDENT_ID, "password")

    assert api.login_id == STUDENT_ID


async def test_a_longer_number_starting_the_same_way_is_not_this_student(fixed_now):
    """The account-type suffix makes userId longer than the number, so the two
    have to be compared whole rather than with startswith."""
    api = _discussion_api([_post(STUDENT_ID + "1")])

    items = await api._get_not_done_discussions_info(
        [OPEN_DISCUSSION], "U2026291983220013", "2026,2"
    )

    assert len(items) == 1


def _returning(value):
    async def call(*args, **kwargs):
        return value

    return call


async def test_get_todo_list_files_every_category_under_a_known_name(
    monkeypatch, fixed_now
):
    """The wiring nothing else covers: a category get_todo_list forgets, or
    files under a name the two TYPE_EMOJIS dicts don't know, disappears from
    /show and from the notification loop with the whole suite still green."""
    api = _api(
        FakeResponse(
            payload=[
                {"value": "2026,2", "subjList": [{"value": "SUBJ", "name": "English"}]}
            ]
        )
    )
    api.login_id = STUDENT_ID
    for name in ("_get_lectures", "_get_homeworks", "_get_team_projects", "_get_quizzes"):
        monkeypatch.setattr(api, name, _returning([]))
    monkeypatch.setattr(api, "_get_discussions", _returning([OPEN_DISCUSSION]))
    monkeypatch.setattr(api, "_get_discussion_opinions", _returning([]))

    todo_list = await api.get_todo_list()

    categories = todo_list[0]["todo"]
    assert set(categories) == {
        "lectures",
        "homeworks",
        "team_projects",
        "quizzes",
        "discussions",
    }
    assert set(categories) <= set(show_emojis), "a type /show cannot render"
    assert set(categories) <= set(notify_emojis), "a type never notified about"
    assert [item["title"] for item in categories["discussions"]] == [
        "문안나의 논문을 읽고 토론"
    ]


CATEGORY_FETCHERS = (
    "_get_lectures",
    "_get_homeworks",
    "_get_team_projects",
    "_get_quizzes",
    "_get_discussions",
)


def _todo_api(monkeypatch, *subject_ids):
    """An api that lists these subjects and finds nothing to do in any."""
    api = _api(
        FakeResponse(
            payload=[
                {
                    "value": "2026,2",
                    "subjList": [
                        {"value": subject_id, "name": subject_id}
                        for subject_id in subject_ids
                    ],
                }
            ]
        )
    )
    api.login_id = STUDENT_ID
    for name in CATEGORY_FETCHERS:
        monkeypatch.setattr(api, name, _returning([]))
    monkeypatch.setattr(api, "_get_discussion_opinions", _returning([]))
    return api


async def test_get_todo_list_asks_about_every_subject_at_once(monkeypatch, fixed_now):
    """Taken in turn, each subject is one more round trip to Korea before the
    user sees anything. No lecture request here answers until every subject's
    has gone out, which a subject-by-subject loop can never satisfy."""
    subject_ids = ["A", "B", "C"]
    api = _todo_api(monkeypatch, *subject_ids)
    started = []
    all_started = asyncio.Event()

    async def lectures(subject_id, year):
        started.append(subject_id)
        if len(started) == len(subject_ids):
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        return []

    monkeypatch.setattr(api, "_get_lectures", lectures)

    todo_list = await api.get_todo_list()

    assert todo_list is not None, "the subjects were fetched one after another"
    assert [subject["id"] for subject in todo_list] == subject_ids


async def test_get_todo_list_keeps_each_answer_with_its_subject(monkeypatch, fixed_now):
    """Answers come back in whatever order KLAS finishes them; the first
    subject listed answering last must not hand its work to another."""
    api = _todo_api(monkeypatch, "SLOW", "FAST")
    fast_answered = asyncio.Event()

    async def discussions(subject_id, year):
        if subject_id == "SLOW":
            await asyncio.wait_for(fast_answered.wait(), timeout=1)
        else:
            fast_answered.set()
        return [{**OPEN_DISCUSSION, "title": f"토론 for {subject_id}"}]

    monkeypatch.setattr(api, "_get_discussions", discussions)

    todo_list = await api.get_todo_list()

    assert {
        subject["id"]: [item["title"] for item in subject["todo"]["discussions"]]
        for subject in todo_list
    } == {"SLOW": ["토론 for SLOW"], "FAST": ["토론 for FAST"]}


async def test_get_todo_list_marks_the_subject_klas_refused_a_category_for(
    monkeypatch, fixed_now
):
    """A refused request and a finished category are both an empty list by the
    time they leave here. The flag is the only thing that tells the callers
    apart "nothing to do" and "could not ask"."""
    api = _todo_api(monkeypatch, "REFUSED", "FINE")

    async def homeworks(subject_id, year):
        return None if subject_id == "REFUSED" else []

    monkeypatch.setattr(api, "_get_homeworks", homeworks)

    todo_list = await api.get_todo_list()

    assert {subject["id"]: subject["incomplete"] for subject in todo_list} == {
        "REFUSED": True,
        "FINE": False,
    }
    assert todo_list[0]["todo"]["homeworks"] == []


def _hanging(cancelled, label):
    """A request that never answers, and reports being cancelled."""

    async def call(subject_id, year):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append((label, subject_id))
            raise

    return call


async def test_get_todo_list_cancels_everything_in_flight_when_one_request_fails(
    monkeypatch, fixed_now
):
    """A subject whose requests never got through fails the whole list, as it
    always has - and nothing is left running against a session the caller
    closes next: not the other subjects, not the failed subject's own other
    requests."""
    api = _todo_api(monkeypatch, "BROKEN", "PENDING")
    cancelled = []
    hanging_quizzes = _hanging(cancelled, "quizzes")

    async def quizzes(subject_id, year):
        if subject_id == "BROKEN":
            raise aiohttp.ServerDisconnectedError()
        return await hanging_quizzes(subject_id, year)

    monkeypatch.setattr(api, "_get_quizzes", quizzes)
    monkeypatch.setattr(api, "_get_lectures", _hanging(cancelled, "lectures"))

    assert await api.get_todo_list() is None
    assert sorted(cancelled) == [
        ("lectures", "BROKEN"),
        ("lectures", "PENDING"),
        ("quizzes", "PENDING"),
    ]


async def test_a_failed_opinion_request_cancels_the_other_discussions(fixed_now):
    api = _discussion_api()
    cancelled = []
    hanging = _hanging(cancelled, "opinions")

    async def opinions(subject_id, year, tpcode):
        if tpcode == "BROKEN":
            raise aiohttp.ServerDisconnectedError()
        return await hanging(tpcode, year)

    api._get_discussion_opinions = opinions

    with pytest.raises(ExceptionGroup):
        await api._get_not_done_discussions_info(
            [
                {**OPEN_DISCUSSION, "tpcode": "BROKEN"},
                {**OPEN_DISCUSSION, "tpcode": "PENDING"},
            ],
            "SUBJ",
            "2026,2",
        )

    assert cancelled == [("opinions", "PENDING")]


async def test_get_todo_list_logs_every_failure_and_its_subject(
    monkeypatch, fixed_now, caplog
):
    """Two subjects failing in the same instant, for unrelated reasons: a
    dropped connection must not be all the log has to say when the other one is
    a parser that will break again on every cycle."""
    api = _todo_api(monkeypatch, "OFFLINE", "MALFORMED")
    both_asked = asyncio.Event()
    asked = []

    async def homeworks(subject_id, year):
        asked.append(subject_id)
        if len(asked) == 2:
            both_asked.set()
        await asyncio.wait_for(both_asked.wait(), timeout=1)
        if subject_id == "OFFLINE":
            raise aiohttp.ServerDisconnectedError()
        raise TypeError("'<' not supported between 'NoneType' and 'str'")

    monkeypatch.setattr(api, "_get_homeworks", homeworks)

    assert await api.get_todo_list() is None
    assert "ServerDisconnectedError" in caplog.messages[-1]
    assert "TypeError" in caplog.messages[-1]
    assert "while reading subject OFFLINE" in caplog.text
    assert "while reading subject MALFORMED" in caplog.text


async def test_a_failure_is_logged_without_the_session_cookie(
    monkeypatch, fixed_now, caplog
):
    """aiohttp's response errors carry the request they answered, and their
    repr prints its headers - the Cookie header being a live KLAS session that
    whoever reads the logs could log in with."""
    request_info = aiohttp.RequestInfo(
        URL(URL_UNDER_TEST),
        "POST",
        CIMultiDictProxy(CIMultiDict({"Cookie": "JSESSIONID=live-session"})),
        URL(URL_UNDER_TEST),
    )
    api = _todo_api(monkeypatch, "SUBJ")

    async def lectures(subject_id, year):
        raise aiohttp.TooManyRedirects(request_info, ())

    monkeypatch.setattr(api, "_get_lectures", lectures)

    assert await api.get_todo_list() is None
    assert "TooManyRedirects" in caplog.messages[-1]
    assert "live-session" not in caplog.text


class CountingSession:
    """Answers every request, and remembers how many were open at once."""

    def __init__(self):
        self.in_flight = 0
        self.peak = 0

    def post(self, *args, **kwargs):
        return _CountedRequest(self)


class _CountedRequest:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        self._session.in_flight += 1
        self._session.peak = max(self._session.peak, self._session.in_flight)
        # Give every other request its chance to open while this one is.
        await asyncio.sleep(0)
        return FakeResponse(payload={})

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._session.in_flight -= 1
        return False


async def test_post_json_never_has_more_than_the_cap_in_flight():
    """Fetching every subject at once must not turn into 30 simultaneous
    requests against KLAS - and the notification cycle reads several users at
    a time, each through an api of its own, so the cap has to hold across all
    of them rather than for each."""
    session = CountingSession()
    apis = [_api() for _ in range(3)]
    for api in apis:
        api.session = session

    await asyncio.gather(
        *(
            api._post_json(URL_UNDER_TEST, {})
            for api in apis
            for _ in range(kw_service.MAX_CONCURRENT_REQUESTS)
        )
    )

    assert session.peak == kw_service.MAX_CONCURRENT_REQUESTS
