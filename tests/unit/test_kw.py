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

    async def text(self):
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


LOGIN_FORM_URL = "https://klas.kw.ac.kr/usr/cmn/login/LoginForm.do"


class FakeCookie:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class FakeLoginSession:
    """Serves KLAS's three-step login: form GET, public key POST, login POST."""

    def __init__(self, *post_outcomes, form_url=LOGIN_FORM_URL):
        self._post_outcomes = list(post_outcomes)
        self._form_url = form_url
        self.cookie_jar = [FakeCookie("JSESSIONID", "fresh")]
        self.posts = 0

    def get(self, *args, **kwargs):
        # Answering from the form's own URL means "not logged in yet", which is
        # what sends login() down the full three-step path.
        return FakeResponse(url=self._form_url)

    def post(self, *args, **kwargs):
        outcome = self._post_outcomes[min(self.posts, len(self._post_outcomes) - 1)]
        self.posts += 1
        return outcome


def _login_api(*post_outcomes, form_url=LOGIN_FORM_URL):
    api = KwangwoonUniversityApi()
    api.session = FakeLoginSession(*post_outcomes, form_url=form_url)
    # The real encryptor needs a valid RSA key; the login flow under test does
    # not care what the token contains.
    api._encryptor = lambda public_key, data: "encrypted-token"
    return api


PUBLIC_KEY = FakeResponse(payload={"publicKey": "test-key"})


async def test_login_retries_a_dropped_connection():
    """Regression: KLAS dropping the login POST used to leave cookies empty,
    and every later call reported 'No cookies found' instead of retrying."""
    api = _login_api(
        PUBLIC_KEY, ConnectionFailure(), FakeResponse(payload={"errorCount": 0})
    )

    assert await api.login("student", "pw") == {"JSESSIONID": "fresh"}
    assert api.session.posts == 3, "the public key POST plus the retried login POST"


async def test_login_returns_none_when_the_connection_never_recovers():
    api = _login_api(PUBLIC_KEY, ConnectionFailure())

    assert await api.login("student", "pw") is None
    assert api.session.posts == 1 + kw_service.REQUEST_RETRIES + 1
    assert api.cookies == {}


async def test_login_returns_none_on_wrong_password():
    api = _login_api(
        PUBLIC_KEY,
        FakeResponse(payload={"errorCount": 1, "fieldErrors": [{"message": "nope"}]}),
    )

    assert await api.login("student", "wrong") is None


async def test_login_with_valid_cookies_returns_them():
    """A redirect away from the login form means the session is still good.
    This path used to return a bare 1, which no caller could use as cookies."""
    api = _login_api(form_url="https://klas.kw.ac.kr/std/cmn/frame/Frame.do")

    assert await api.login("student", "pw") == {"JSESSIONID": "fresh"}
    assert api.session.posts == 0, "an already-valid session needs no login round trip"


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
