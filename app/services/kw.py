import os
import re
import json
import time
import logging
import asyncio
import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List

import aiohttp
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from fake_useragent import UserAgent
from bs4 import BeautifulSoup
from yarl import URL
import base64

from app.utils import timezone


# KLAS regularly drops idle keep-alive connections, and aiohttp only finds out
# when the next request goes out on the dead socket. Every retried call here is
# an idempotent read, so replaying one is safe.
RETRYABLE_ERRORS = (aiohttp.ClientConnectionError, asyncio.TimeoutError)
REQUEST_RETRIES = 2
RETRY_DELAY_SECONDS = 1
# Without this aiohttp waits its 5-minute default, and one hung KLAS request
# stalls the notification cycle for every remaining user.
REQUEST_TIMEOUT_SECONDS = 30
# get_todo_list() asks about every subject at once, five requests each, so a
# full timetable would be 30-odd in flight - and the notification cycle reads
# several users at a time on top of that. KLAS is a university server that
# never agreed to serve a bot, and it sees one address whoever the request is
# for, so the cap is on the process and not on one user's refresh.
MAX_CONCURRENT_REQUESTS = 10
# Module-level because every KwangwoonUniversityApi has to share it. Login is
# not counted: its requests run one after another.
_request_slots = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

KLAS_BASE_URL = "https://klas.kw.ac.kr"
LOGIN_FORM_URL = f"{KLAS_BASE_URL}/usr/cmn/login/LoginForm.do"
LOGIN_SECURITY_URL = f"{KLAS_BASE_URL}/usr/cmn/login/LoginSecurity.do"
LOGIN_CAPTCHA_URL = f"{KLAS_BASE_URL}/usr/cmn/login/LoginCaptcha.do"
LOGIN_CONFIRM_URL = f"{KLAS_BASE_URL}/usr/cmn/login/LoginConfirm.do"
UPDATE_SESSION_URL = f"{KLAS_BASE_URL}/usr/cmn/login/UpdateSession.do"
CERT_INFO_URL = f"{KLAS_BASE_URL}/ext/pass/UserCertInfoOne.do"
CERT_PUSH_PAGE_URL = f"{KLAS_BASE_URL}/usr/cmn/cert/UserCertPushResult.do"
CERT_PUSH_CHECK_URL = f"{KLAS_BASE_URL}/ext/pass/UserCertPushChk.do"

# The KLAS answer that means "accepted" on every /ext/pass/ endpoint.
CERT_OK = "0000"
# How often, and for how long, to ask whether the student approved the push.
# KLAS itself keeps a push valid for an hour, but nobody waits that long.
PUSH_POLL_INTERVAL_SECONDS = 3
PUSH_APPROVAL_TIMEOUT_SECONDS = 120
# UserCertPushChk.do is only known to answer CERT_OK after an approval. If it
# ever says so before one, the login that follows still asks for the second
# factor; this caps how many times the password is sent again in that case.
MAX_CREDENTIAL_RESUBMITS = 3

# UserCertPushResult.do writes the push id and its expiry into an inline script:
#   JSON.stringify({ 'loginId' : '...', 'tid' : '<hex>', 'limit' : '<epoch ms>'})
_PUSH_TID_PATTERN = re.compile(r"""['"]tid['"]\s*:\s*['"]([0-9A-Za-z]+)['"]""")
_PUSH_LIMIT_PATTERN = re.compile(r"""['"]limit['"]\s*:\s*['"](\d+)['"]""")


class LoginStatus(Enum):
    """How far a KLAS login got."""

    OK = "ok"
    # Password accepted; KLAS now wants the push approved in the KLAS app.
    TWO_FACTOR_REQUIRED = "two_factor_required"
    # KLAS rejected the login: wrong credentials or a locked account.
    FAILED = "failed"
    # KLAS could not be reached or answered with an error; this says nothing
    # about the credentials, so it must not be reported as a wrong password.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class LoginResult:
    """The outcome of a login step. `cookies` is set only for LoginStatus.OK."""

    status: LoginStatus
    cookies: Optional[Dict] = None


class PushStatus(Enum):
    """Whether KLAS sent the second-factor push to the student's phone."""

    SENT = "sent"
    # No phone is registered for push approval: the student has to install the
    # KLAS app and register it first.
    NO_DEVICE = "no_device"
    FAILED = "failed"


@dataclass(frozen=True)
class PushRequest:
    """One sent push. `tid` identifies it; `limit` is its expiry in epoch ms."""

    status: PushStatus
    tid: Optional[str] = None
    limit: Optional[str] = None


def _leaf_errors(error: BaseException) -> List[BaseException]:
    """The real failures inside what a TaskGroup raised, however deep."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaf_errors(inner)]
    return [error]


def _describe(error: BaseException) -> str:
    """An exception for the log, by type and str(), never repr().

    The repr of an aiohttp response error prints the request headers, and the
    Cookie among them is the student's live KLAS session.
    """
    return f"{type(error).__name__}: {error}"


def _parse_push_page(html: str) -> Optional[tuple[str, str]]:
    """Read (tid, limit) out of UserCertPushResult.do's inline script."""
    tid = _PUSH_TID_PATTERN.search(html)
    limit = _PUSH_LIMIT_PATTERN.search(html)
    if not tid or not limit:
        return None
    return tid.group(1), limit.group(1)


class KwangwoonUniversityApi:
    def __init__(self) -> None:
        self.ua: UserAgent = UserAgent()
        self.current_dir: str = os.path.dirname(os.path.abspath(__file__))
        self.headers: dict = {
            "Content-Type": "application/json",
            "User-Agent": self.ua.random,
        }
        self.cookies: dict = {}
        self.session: Optional[aiohttp.ClientSession] = None
        # The KLAS login id, which is the student number. Remembered because
        # discussion posts are stamped with it - see _has_own_opinion.
        self.login_id: Optional[str] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _cookies_is_valid(self) -> bool:
        if not self.cookies:
            logging.error("No cookies found. Please log in first.")
            return False
        return True

    def set_cookies(self, cookies: dict):
        """Continue a session from its cookies; same as restore_cookies()."""
        self.restore_cookies(cookies)

    def _cookies_from_jar(self) -> dict:
        self.cookies = {cookie.key: cookie.value for cookie in self.session.cookie_jar}
        return self.cookies

    async def _request_with_retries(self, url: str, make_request, handle_response):
        """Run one request, replaying it when the connection dies mid-flight.

        `make_request` builds a fresh request context manager per attempt — a
        replayed request cannot reuse the previous one — and `handle_response`
        reads the response while it is still open.

        Both login and the data endpoints go through here. Login used to sit
        outside any retry, so a dropped keep-alive during the login POST left
        self.cookies empty and every later call reported the misleading "No
        cookies found. Please log in first." instead of simply retrying.
        """
        for attempt in range(REQUEST_RETRIES + 1):
            try:
                async with make_request() as response:
                    return await handle_response(response)
            except RETRYABLE_ERRORS as e:
                if attempt == REQUEST_RETRIES:
                    logging.error(
                        f"Request to {url} failed after {REQUEST_RETRIES + 1} "
                        f"attempts: {_describe(e)}"
                    )
                    raise
                # KLAS drops idle keep-alives constantly and every one of
                # these recovers on replay (see REQUEST_RETRIES above) — it
                # was WARNING, but at ~200 of these a week it drowned out the
                # rare case that actually matters: the "failed after N
                # attempts" error a few lines up.
                logging.info(
                    f"Connection error on {url} ({_describe(e)}), "
                    f"retrying in {RETRY_DELAY_SECONDS}s"
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)

    async def login_with_cookies(self, cookies: dict) -> bool:
        self.set_cookies(cookies)
        login_form_url = "https://klas.kw.ac.kr/usr/cmn/login/LoginForm.do"

        async with self.session.get(login_form_url) as response:
            if str(response.url) != login_form_url:
                logging.info(f"Log in with cookies. Status code: {response.status}")
                return True
        return False

    def _encryptor(self, public_key: str, data: str) -> Optional[str]:
        pem = "-----BEGIN PUBLIC KEY-----\n" + public_key + "\n-----END PUBLIC KEY-----"

        public_key = RSA.importKey(pem)

        # Jsencrypt uses PKCS1_v1_5
        cipher = PKCS1_v1_5.new(public_key)

        encoded = base64.b64encode(
            cipher.encrypt(data.encode()),
        ).decode()

        return encoded

    async def login(self, login_id: str, login_pwd: str) -> Optional[Dict]:
        """Log in and return the session cookies, or None if that failed.

        Never raises for a network fault: the callers treat a falsy answer as
        "this user could not be read this time" and move on. A login that
        stops at the second factor also answers None; attempt_login() tells
        the outcomes apart.
        """
        result = await self.attempt_login(login_id, login_pwd)
        if result.status is LoginStatus.TWO_FACTOR_REQUIRED:
            logging.warning(
                "KLAS accepted the password but asks for push approval in the "
                "KLAS app; this login cannot finish without the student"
            )
        return result.cookies if result.status is LoginStatus.OK else None

    async def attempt_login(self, login_id: str, login_pwd: str) -> LoginResult:
        """Run the KLAS login up to the second factor.

        Reuses the session when the cookies already held are still valid.
        Otherwise submits the credentials; on TWO_FACTOR_REQUIRED the caller
        continues with request_push() and wait_for_push_approval() on this
        same instance, because KLAS remembers the approval in the session.
        Never raises for a network fault.
        """
        self.login_id = login_id

        async def redirected_away_from_login_form(response) -> bool:
            # KLAS bounces us off the login form when the cookies we already
            # hold are still valid, so there is nothing left to log in to.
            return str(response.url) != LOGIN_FORM_URL

        try:
            if await self._request_with_retries(
                LOGIN_FORM_URL,
                lambda: self.session.get(LOGIN_FORM_URL),
                redirected_away_from_login_form,
            ):
                logging.info("Logged in with existing cookies.")
                return LoginResult(LoginStatus.OK, self._cookies_from_jar())
        except Exception as e:
            logging.error(f"Could not open the KLAS login form: {_describe(e)}")
            return LoginResult(LoginStatus.UNAVAILABLE)

        return await self._submit_credentials(login_id, login_pwd)

    async def _submit_credentials(self, login_id: str, login_pwd: str) -> LoginResult:
        """Send the encrypted credentials the way the KLAS login page does.

        LoginSecurity.do hands out a fresh RSA key per attempt, LoginCaptcha.do
        is asked whether a captcha is due, and LoginConfirm.do decides. Run a
        second time after the push was approved, the same calls finish the login.
        """

        async def read_public_key(response) -> str:
            return (await response.json())["publicKey"]

        async def read_captcha_answer(response) -> Optional[str]:
            if response.status != 200:
                logging.error(f"LoginCaptcha.do answered with status {response.status}")
                return None
            return (await response.text()).strip()

        async def read_login_result(response) -> Optional[Dict]:
            if response.status != 200:
                logging.error(f"LoginConfirm.do answered with status {response.status}")
                return None
            return await response.json()

        try:
            public_key_str = await self._request_with_retries(
                LOGIN_SECURITY_URL,
                lambda: self.session.post(LOGIN_SECURITY_URL),
                read_public_key,
            )

            login_data = {"loginId": login_id, "loginPwd": login_pwd, "storeIdYn": "Y"}
            encrypted_login = self._encryptor(public_key_str, json.dumps(login_data))
            if not encrypted_login:
                logging.error("Encryption failed")
                return LoginResult(LoginStatus.FAILED)

            captcha_answer = await self._request_with_retries(
                LOGIN_CAPTCHA_URL,
                lambda: self.session.post(
                    LOGIN_CAPTCHA_URL,
                    json={"loginToken": encrypted_login, "captcha": ""},
                    headers=self.headers,
                ),
                read_captcha_answer,
            )
            # "0" is the only answer seen so far, on logins that needed no
            # captcha. Anything else is logged and left to LoginConfirm.do to
            # judge, which reports a missing captcha as an error of its own.
            # None means an error status, which read_captcha_answer already
            # logged. The answer is capped because KLAS can send a whole HTML
            # page here, which would flood the log.
            if captcha_answer is not None and captcha_answer != "0":
                logging.warning(
                    f"Unexpected LoginCaptcha.do answer: {captcha_answer[:200]!r}"
                )

            login_body = {
                "loginToken": encrypted_login,
                "captcha": "",
                "redirectUrl": "/std/cmn/frame/Frame.do",
                "redirectTabUrl": "",
            }
            response_data = await self._request_with_retries(
                LOGIN_CONFIRM_URL,
                lambda: self.session.post(
                    LOGIN_CONFIRM_URL,
                    json=login_body,
                    headers=self.headers,
                ),
                read_login_result,
            )
        except Exception as e:
            logging.error(f"An error occurred during KLAS login: {_describe(e)}")
            return LoginResult(LoginStatus.UNAVAILABLE)

        return self._read_login_confirm(response_data)

    def _read_login_confirm(self, response_data: Optional[Dict]) -> LoginResult:
        """Turn LoginConfirm.do's answer into a LoginResult.

        A password that is right but still needs the push answers
        errorCount 0 with response.twoFactorAt "Y". It used to be read as a
        finished login, so every later request ran on a half-open session.
        """
        if not isinstance(response_data, dict):
            return LoginResult(LoginStatus.UNAVAILABLE)

        if response_data.get("errorCount", 0) == 0:
            details = response_data.get("response") or {}
            if details.get("twoFactorAt") == "Y":
                return LoginResult(LoginStatus.TWO_FACTOR_REQUIRED)
            logging.debug("Login successful.")
            return LoginResult(LoginStatus.OK, self._cookies_from_jar())

        field_errors = response_data.get("fieldErrors") or []
        message = field_errors[0].get("message", "") if field_errors else ""
        if "계정이 잠겼습니다" in message:
            logging.error(
                "Login failed. Enter wrong password 5 times. Please reset password."
            )
        else:
            logging.error("Login failed. Wrong password or ID")
        return LoginResult(LoginStatus.FAILED)

    async def request_push(self) -> PushRequest:
        """Ask KLAS to send the second-factor push to the student's phone.

        Call after attempt_login() answered TWO_FACTOR_REQUIRED. Loading
        UserCertPushResult.do is what sends the push; the page carries the
        push id that wait_for_push_approval() needs. Never raises.
        """
        if not self.login_id:
            logging.error("request_push called before attempt_login")
            return PushRequest(PushStatus.FAILED)

        cert_body = {
            "certResultAt": "N",
            "certiGubun": "eml",
            "gubun": "E",
            "type": "LOGIN",
            "userId": f"{self.login_id}UA",
            "loginId": self.login_id,
            "inputKey": "",
            "certOpt": "smart",
        }

        async def read_push_page(response) -> Optional[str]:
            if response.status != 200:
                logging.error(
                    f"UserCertPushResult.do answered with status {response.status}"
                )
                return None
            # The page is EUC-KR; only the ASCII push id is read from it.
            return await response.text(errors="replace")

        try:
            cert_info = await self._post_json(CERT_INFO_URL, cert_body, self.headers)
            if not isinstance(cert_info, dict) or cert_info.get("returnCode") != CERT_OK:
                # Only the code and KLAS's own message: the rest of the answer
                # can describe the student's registered phone.
                code, message = (
                    (cert_info.get("returnCode"), cert_info.get("returnMsg"))
                    if isinstance(cert_info, dict)
                    else (None, None)
                )
                logging.error(
                    "UserCertInfoOne.do did not accept the request: "
                    f"returnCode={code!r} returnMsg={str(message)[:200]!r} "
                    f"answer={type(cert_info).__name__}"
                )
                return PushRequest(PushStatus.FAILED)
            # The KLAS page itself only offers the push when status is "1",
            # and otherwise sends the student off to register the app.
            if cert_info.get("status") != "1":
                logging.info("No phone is registered for KLAS push approval")
                return PushRequest(PushStatus.NO_DEVICE)

            html = await self._request_with_retries(
                CERT_PUSH_PAGE_URL,
                lambda: self.session.post(
                    CERT_PUSH_PAGE_URL,
                    params={"loginId": self.login_id},
                    data={"modalId": "ax5-modal-1"},
                ),
                read_push_page,
            )
        except Exception as e:
            logging.error(f"Could not send the KLAS push: {_describe(e)}")
            return PushRequest(PushStatus.FAILED)

        parsed = _parse_push_page(html) if html else None
        if not parsed:
            logging.error("UserCertPushResult.do carried no push id")
            return PushRequest(PushStatus.FAILED)
        tid, limit = parsed
        return PushRequest(PushStatus.SENT, tid=tid, limit=limit)

    async def check_push(self, push: PushRequest) -> bool:
        """Whether KLAS reports the push as approved. Never raises."""
        body = {"loginId": self.login_id, "tid": push.tid, "limit": push.limit}
        try:
            answer = await self._post_json(CERT_PUSH_CHECK_URL, body, self.headers)
        except Exception as e:
            logging.warning(f"Could not check the KLAS push: {_describe(e)}")
            return False
        return isinstance(answer, dict) and answer.get("returnCode") == CERT_OK

    async def wait_for_push_approval(
        self,
        login_pwd: str,
        push: PushRequest,
        timeout: float = PUSH_APPROVAL_TIMEOUT_SECONDS,
        interval: float = PUSH_POLL_INTERVAL_SECONDS,
    ) -> LoginResult:
        """Wait for the student to approve the push, then finish the login.

        Only the second LoginConfirm.do proves the approval: UserCertPushChk.do
        is a hint for when to try it. Answers TWO_FACTOR_REQUIRED when the time
        ran out without an approval, FAILED when KLAS rejected the login.
        """
        deadline = time.monotonic() + timeout
        resubmits = 0
        while time.monotonic() < deadline:
            if await self.check_push(push):
                result = await self._submit_credentials(self.login_id, login_pwd)
                if result.status is not LoginStatus.TWO_FACTOR_REQUIRED:
                    return result
                resubmits += 1
                if resubmits >= MAX_CREDENTIAL_RESUBMITS:
                    logging.warning(
                        "UserCertPushChk.do reports the push approved, but KLAS "
                        "still asks for the second factor"
                    )
                    return LoginResult(LoginStatus.FAILED)
            await asyncio.sleep(interval)

        logging.info("The KLAS push was not approved in time")
        return LoginResult(LoginStatus.TWO_FACTOR_REQUIRED)

    def restore_cookies(self, cookies: Dict) -> None:
        """Continue a stored KLAS session: put its cookies into the jar.

        The jar is the only place requests take cookies from. Passing them per
        request as well would override what KLAS rotates via Set-Cookie: aiohttp
        lets request cookies win over the jar, so a refreshed session cookie
        would keep being sent with its old value.
        """
        self.session.cookie_jar.update_cookies(cookies, URL(KLAS_BASE_URL))
        self.cookies = dict(cookies)

    def forget_cookies(self) -> None:
        """Drop a dead session's cookies before logging in on this client."""
        self.session.cookie_jar.clear()
        self.cookies = {}

    def klas_cookies(self) -> Dict:
        """The cookies KLAS would receive, to be stored for the next operation.

        Filtered by host because the jar also holds cookies of the other KW
        hosts an operation visited, such as did-3 for the ID photo.
        """
        jar_cookies = self.session.cookie_jar.filter_cookies(URL(KLAS_BASE_URL))
        return {name: morsel.value for name, morsel in jar_cookies.items()}

    async def keep_alive(self) -> Optional[bool]:
        """Extend the KLAS session and report whether it is still logged in.

        KLAS ends a session after two idle hours; UpdateSession.do is what its
        own "extend" button calls to reset that timer. Liveness is then read
        the same way attempt_login() reads it: a valid session is redirected
        away from the login form.

        Answers None when KLAS could not be reached, which says nothing about
        the session: treating it as dead would make the student approve a
        push for a network fault. Never raises.
        """
        async def check_extension(response) -> None:
            if response.status not in (200, 304):
                logging.warning(
                    f"UpdateSession.do answered with status {response.status}"
                )

        async def still_logged_in(response) -> bool:
            return str(response.url) != LOGIN_FORM_URL

        try:
            # Retried like every other read: KLAS drops idle keep-alive
            # connections, and one dropped socket must not cost the extension.
            await self._request_with_retries(
                UPDATE_SESSION_URL,
                lambda: self.session.get(UPDATE_SESSION_URL, headers=self.headers),
                check_extension,
            )
            alive = await self._request_with_retries(
                LOGIN_FORM_URL,
                lambda: self.session.get(LOGIN_FORM_URL),
                still_logged_in,
            )
        except Exception as e:
            logging.warning(f"Could not extend the KLAS session: {_describe(e)}")
            return None

        if alive:
            self._cookies_from_jar()
        return alive

    async def _post_json(
        self, url: str, body: dict, headers: Optional[dict] = None
    ) -> Optional[Dict]:
        """POST a JSON body to KLAS and decode the JSON answer.

        Returns None when KLAS answers with an error status or a body that is
        not JSON. Connection-level failures are retried (see RETRYABLE_ERRORS)
        and re-raised once the attempts run out, so a user whose data could not
        be fetched at all still surfaces as an error rather than as "no data".
        """

        async def read_body(response) -> Optional[Dict]:
            if response.status != 200:
                logging.error(
                    f"Failed to retrieve {url}. Status code: {response.status}"
                )
                return None
            try:
                return await response.json()
            except (aiohttp.ContentTypeError, json.JSONDecodeError) as e:
                # KLAS answers 200 with an empty body (and no Content-Type) for
                # records a student does not have yet — a first-semester student
                # querying grades, for instance. Treat that as "no data" instead
                # of letting it blow up the calling handler.
                logging.error(f"Non-JSON response from {url}: {e}")
                return None

        # The slot is held across the retries: a request waiting out its
        # retry delay is still one KLAS is about to hear from again.
        async with _request_slots:
            return await self._request_with_retries(
                url,
                lambda: self.session.post(
                    url=url,
                    json=body,
                    headers=headers or self.headers,
                ),
                read_body,
            )

    async def get_subjects(self) -> Optional[Dict]:
        if not self._cookies_is_valid():
            return None

        response_data = await self._post_json(
            "https://klas.kw.ac.kr/std/cmn/frame/YearhakgiAtnlcSbjectList.do", {}
        )
        if not response_data:
            return None

        logging.debug("Data about subjects retrieved successfully.")
        return response_data[0]

    async def _make_lecture_request(
        self, url: str, subject_id: str, year: str
    ) -> Optional[Dict]:
        if not self._cookies_is_valid():
            return None

        requests_body = {
            "selectSubj": subject_id,
            "selectYearhakgi": year,
            "selectChangeYn": "Y",
        }

        return await self._post_json(url, requests_body)

    async def _get_lectures(self, subject_id: str, year: str) -> Optional[Dict]:
        lectures_url = (
            "https://klas.kw.ac.kr/std/lis/evltn/SelectOnlineCntntsStdList.do"
        )
        return await self._make_lecture_request(lectures_url, subject_id, year)

    async def _get_homeworks(self, subject_id: str, year: str) -> Optional[Dict]:
        homeworks_url = "https://klas.kw.ac.kr/std/lis/evltn/TaskStdList.do"
        return await self._make_lecture_request(homeworks_url, subject_id, year)

    async def _get_team_projects(self, subject_id: str, year: str) -> Optional[Dict]:
        team_projects_url = "https://klas.kw.ac.kr/std/lis/evltn/PrjctStdList.do"
        return await self._make_lecture_request(team_projects_url, subject_id, year)

    async def _get_quizzes(self, subject_id: str, year: str) -> Optional[Dict]:
        quizzes_url = "https://klas.kw.ac.kr/std/lis/evltn/AnytmQuizStdList.do"
        return await self._make_lecture_request(quizzes_url, subject_id, year)

    async def _get_discussions(self, subject_id: str, year: str) -> Optional[Dict]:
        discussions_url = "https://klas.kw.ac.kr/std/lis/evltn/DscsnStdList.do"
        return await self._make_lecture_request(discussions_url, subject_id, year)

    async def _get_discussion_opinions(
        self, subject_id: str, year: str, tpcode
    ) -> Optional[Dict]:
        """Every post inside one 토론, which is how participation is read."""
        if not self._cookies_is_valid():
            return None

        opinions_url = "https://klas.kw.ac.kr/std/lis/evltn/DscsnOpinionList.do"
        return await self._post_json(
            opinions_url,
            {
                "selectSubj": subject_id,
                "selectYearhakgi": year,
                "selectChangeYn": "Y",
                "tpcode": str(tpcode),
            },
        )

    @staticmethod
    def _text(value) -> str:
        """A KLAS text field as a string: a missing or null field is "".

        `.get(key, default)` only covers an absent key, and KLAS sends the key
        with a null value just as often. Nothing else is touched, whitespace
        included: assignment_key() is built from the title, and trimming a
        title that KLAS has always sent with a trailing space would give it a
        new key and repeat its alerts to everyone already notified.
        """
        return "" if value is None else str(value)

    def _deadline_or_none(self, kind: str, expire_date, date_format, *args):
        """The row's deadline, or None - with a warning - when it has none.

        A row whose date is missing or malformed cannot be reminded about. It
        is skipped on its own: raising here fails the TaskGroup that reads
        every subject, and the user's whole todo list becomes None.
        """
        try:
            return self._get_expire_at(expire_date, date_format, *args)
        except (TypeError, ValueError):
            logging.warning(f"Skipping a {kind} without a usable deadline")
            return None

    def _get_not_done_lectures_info(self, lectures: list[dict]) -> list[dict]:
        not_done_lectures = []
        today_date = timezone.now().strftime("%Y-%m-%d %H:%M")
        for lecture in lectures:
            start, end = lecture.get("startDate"), lecture.get("endDate")
            if (
                lecture.get("prog") is not None
                and lecture.get("prog") < 100
                and isinstance(start, str)
                and isinstance(end, str)
                and start < today_date
                and end > today_date
            ):
                expire_at = self._deadline_or_none(
                    "lecture", end, "%Y-%m-%d %H:%M"
                )
                if expire_at is None:
                    continue
                not_done_lectures.append(
                    {
                        "title": self._text(lecture.get("sbjt")),
                        "progress": lecture.get("prog"),
                        "expire_date": end,
                        "expire_at": expire_at,
                    }
                )
        return not_done_lectures

    def _get_not_done_homeworks_info(self, homeworks: list[dict]) -> list[dict]:
        not_done_homeworks = []
        today_date = timezone.now().strftime("%Y-%m-%d %H:%M")
        for homework in homeworks:
            start, end = homework.get("startdate"), homework.get("expiredate")
            if (
                homework.get("submityn") == "N"
                and isinstance(start, str)
                and isinstance(end, str)
                and start < today_date
                and end > today_date
            ):
                expire_at = self._deadline_or_none(
                    "homework", end, "%Y-%m-%d %H:%M:%S"
                )
                if expire_at is None:
                    continue
                not_done_homeworks.append(
                    {
                        "title": self._text(homework.get("title")),
                        "expire_date": end,
                        "expire_at": expire_at,
                    }
                )
        return not_done_homeworks

    def _get_not_done_team_projects_info(self, team_projects: list[dict]) -> list[dict]:
        not_done_team_projects = []
        today_date = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        for team_project in team_projects:
            start, end = team_project.get("startdate"), team_project.get("expiredate")
            if (
                team_project.get("submityn") != "Y"
                and isinstance(start, str)
                and isinstance(end, str)
                and start < today_date
                and end > today_date
            ):
                expire_at = self._deadline_or_none(
                    "team project",
                    end,
                    "%Y-%m-%dT%H:%M:%S.%f%z",
                    True,
                )
                if expire_at is None:
                    continue
                not_done_team_projects.append(
                    {
                        "title": self._text(team_project.get("title")),
                        "expire_date": end,
                        "expire_at": expire_at,
                    }
                )
        return not_done_team_projects

    def _get_not_done_quizzes_info(self, quizzes: list[dict]) -> list[dict]:
        not_done_quizzes = []
        today_date = timezone.now().strftime("%Y%m%d%H%M")
        for quiz in quizzes:
            start, end = quiz.get("sdate"), quiz.get("edate")
            if (
                quiz.get("issubmit") == "N"
                and isinstance(start, str)
                and isinstance(end, str)
                and start < today_date
                and end > today_date
            ):
                expire_at = self._deadline_or_none(
                    "quiz", quiz.get("edt"), "%Y-%m-%d %H:%M"
                )
                if expire_at is None:
                    continue
                not_done_quizzes.append(
                    {
                        "title": self._text(quiz.get("papernm")),
                        "expire_date": quiz.get("edt"),
                        "expire_at": expire_at,
                    }
                )
        return not_done_quizzes

    @staticmethod
    def _post_author_number(opinion: dict) -> str:
        """The student number one 토론 post is stamped with.

        KLAS writes userId as the number followed by an account-type code -
        "2024322505UA" - so the digits are taken on their own and compared
        whole. A startswith() against the raw userId would also accept a
        longer number that merely begins the same way.
        """
        user_id = str(opinion.get("userId", ""))
        for index, char in enumerate(user_id):
            if not char.isdigit():
                return user_id[:index]
        return user_id

    def _has_own_opinion(self, opinions: list[dict]) -> bool:
        """Has the logged-in student already posted in this 토론?

        The student number is the KLAS login id. Each post also carries a
        `title` that students fill in with their number and name, but they type
        it by hand and write the halves in either order, so userId is the only
        field worth matching on.

        Without a login id - the login_with_cookies path never sees one - this
        says False, so the 토론 is reported as outstanding rather than lost.
        """
        if not self.login_id:
            return False
        return any(
            self._post_author_number(opinion) == self.login_id
            for opinion in opinions
        )

    async def _get_not_done_discussions_info(
        self, discussions: list[dict], subject_id: str, year: str
    ) -> list[dict]:
        """The 토론 that are open right now and still missing this student.

        Unlike every other category, the list endpoint carries no "submitted"
        flag - its only count, toroncnt, is how many posts the whole class
        wrote (KLAS labels the column 토론건수). Participation therefore costs
        one more request per discussion, so only the ones inside their date
        window are looked up: usually none or one per subject.
        """
        today_date = timezone.now().strftime("%Y%m%d")
        open_discussions = [
            discussion
            for discussion in discussions
            if discussion.get("started")
            and discussion.get("ended")
            and discussion["started"] <= today_date <= discussion["ended"]
        ]
        if not open_discussions:
            return []

        async with asyncio.TaskGroup() as group:
            requests = [
                group.create_task(
                    self._get_discussion_opinions(
                        subject_id, year, discussion.get("tpcode")
                    )
                )
                for discussion in open_discussions
            ]
        opinions = [request.result() for request in requests]

        not_done_discussions = []
        for discussion, posts in zip(open_discussions, opinions):
            # A request that failed (None) leaves the 토론 in the list: a
            # reminder for work already done beats silence about work that
            # isn't. Same reasoning if a big class ever paginates the posts.
            if posts and self._has_own_opinion(posts):
                continue

            # KLAS gives the end as a bare date, so the deadline is its last
            # minute.
            ended = discussion["ended"]
            not_done_discussions.append(
                {
                    "title": (discussion.get("title") or "").strip(),
                    "expire_date": f"{ended[:4]}-{ended[4:6]}-{ended[6:]} 23:59",
                    "expire_at": self._get_expire_at(
                        f"{ended}2359", "%Y%m%d%H%M"
                    ),
                }
            )
        return not_done_discussions

    def _get_expire_at(self, expire_date, date_format, remove_timezone=False):
        """The deadline itself, as naive Korean local time.

        Deliberately not "how long is left": a countdown is only true at the
        moment it is computed, and this list takes seconds to build on a slow
        day and is then cached for minutes. Callers subtract it from a single `now()` of
        their own when they render, so every line of one message is measured
        from the same instant and a cached list never goes stale.
        """
        expire_date_time = datetime.datetime.strptime(expire_date, date_format)
        if remove_timezone:
            # Converted, not just stripped: dropping the offset of a
            # non-+09:00 stamp would shift the deadline by the difference.
            expire_date_time = expire_date_time.astimezone(
                timezone.TIMEZONE
            ).replace(tzinfo=None)
        return expire_date_time

    async def _get_subject_todo(
        self, subject_id: str, year: str
    ) -> tuple[Dict, bool]:
        """One subject's unfinished work, filed by category, and whether any
        category is missing because KLAS refused the request for it."""
        try:
            async with asyncio.TaskGroup() as group:
                requests = [
                    group.create_task(fetch(subject_id, year))
                    for fetch in (
                        self._get_lectures,
                        self._get_homeworks,
                        self._get_team_projects,
                        self._get_quizzes,
                        self._get_discussions,
                    )
                ]
            lectures, homeworks, team_projects, quizzes, discussions = (
                request.result() for request in requests
            )

            incomplete = None in (
                lectures,
                homeworks,
                team_projects,
                quizzes,
                discussions,
            )
            if incomplete:
                # One rejected request should cost this subject's missing
                # category, not the whole user's todo list. The caller is told
                # all the same: an empty category is otherwise exactly what
                # "everything handed in" looks like.
                logging.warning(
                    f"Incomplete assignment data for subject {subject_id}"
                )

            todo = {
                "lectures": self._get_not_done_lectures_info(lectures or []),
                "homeworks": self._get_not_done_homeworks_info(homeworks or []),
                "team_projects": self._get_not_done_team_projects_info(
                    team_projects or []
                ),
                "quizzes": self._get_not_done_quizzes_info(quizzes or []),
                "discussions": await self._get_not_done_discussions_info(
                    discussions or [], subject_id, year
                ),
            }
            return todo, incomplete
        except Exception as e:
            # The traceback get_todo_list() logs is the only place a failure
            # in a parser, which names no URL, can say which subject it was.
            e.add_note(f"while reading subject {subject_id}")
            raise

    async def get_todo_list(self) -> Optional[List[Dict]]:
        if not self._cookies_is_valid():
            return None

        subjects = await self.get_subjects()
        if not subjects:
            return None

        todo_list = [
            {
                "id": subject.get("value"),
                # Only a null name is replaced: an empty one has stored keys.
                "name": (
                    "Unknown Subject"
                    if subject.get("name") is None
                    else subject.get("name")
                ),
                "todo": {},
                "incomplete": False,
            }
            for subject in subjects.get("subjList")
        ]

        try:
            year = subjects.get("value")

            # Every subject at once, not one after another. Each wait is a
            # round trip to Korea from a host that is nowhere near it, and
            # taken in turn a six-subject timetable was six of them where one
            # would do. TaskGroups rather than gather(), here and below, so
            # that one failed request cancels everything else in flight,
            # instead of leaving it to run against a session the caller is
            # about to close.
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(self._get_subject_todo(todo.get("id"), year))
                    for todo in todo_list
                ]
            for todo, task in zip(todo_list, tasks):
                todo["todo"], todo["incomplete"] = task.result()

            return todo_list

        except Exception as e:
            # A TaskGroup reports its failures wrapped in a group whose own
            # message names none of them, and two subjects can fail for
            # different reasons at once: a dropped connection must not hide a
            # parser that breaks on every cycle.
            # Named by type and str(), never repr(): the repr of an aiohttp
            # response error prints the request headers, and the Cookie among
            # them is this student's live KLAS session.
            causes = "; ".join(
                f"{type(leaf).__name__}: {leaf}" for leaf in _leaf_errors(e)
            )
            logging.error(
                f"An error occurred while getting todo list: {causes}", exc_info=e
            )
            return None

    async def _make_student_info_request(self, url: str) -> Optional[Dict]:
        if not self._cookies_is_valid():
            return None

        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.ua.random,
        }

        return await self._post_json(url, {}, headers)

    async def _get_major_credits(self, major):
        if "전자정보공학대학" in major:
            return {
                "total_credits": 133,
                "major_credits": 60,
                "elective_credits": 30,
            }
        elif "소프트웨어학" in major:
            return {
                "total_credits": 133,
                "major_credits": 60,
                "elective_credits": 30,
            }
        elif "공과대학" in major:
            if "건축학" in major:
                return {
                    "total_credits": 163,
                    "major_credits": 120,
                    "elective_credits": 55,
                }
            else:
                return {
                    "total_credits": 133,
                    "major_credits": 60,
                    "elective_credits": 30,
                }
        elif "자연과학대학" in major:
            return {
                "total_credits": 133,
                "major_credits": 60,
                "elective_credits": 30,
            }
        elif "인문사회과학대학" in major:
            return {
                "total_credits": 130,
                "general_credits": 51,
                "major_credits": 45,
                "elective_credits": 34,
            }
        elif "정책법학대학" in major:
            return {
                "total_credits": 130,
                "general_credits": 45,
                "major_credits": 36,
                "elective_credits": 49,
            }
        elif "경영대학" in major:
            if "국제통상학부" in major:
                return {
                    "total_credits": 133,
                    "general_credits": 57,
                    "major_credits": 45,
                    "elective_credits": 31,
                }
            else:
                return {
                    "total_credits": 130,
                    "general_credits": 54,
                    "major_credits": 45,
                    "elective_credits": 31,
                }
        else:
            return {
                "total_credits": 0,
                "general_credits": 0,
                "major_credits": 0,
                "elective_credits": 0,
            }

    async def get_student_info(self) -> Optional[Dict]:
        if not self._cookies_is_valid():
            return None

        student_info = await self._make_student_info_request(
            "https://klas.kw.ac.kr/std/cps/inqire/AtnlcScreHakjukInfo.do",
        )
        
        if not student_info:
            logging.error("Failed to retrieve student info")
            return None
            
        grades = await self._make_student_info_request(
            "https://klas.kw.ac.kr/std/cps/inqire/AtnlcScreSungjukTot.do",
        )
        
        if not grades:
            logging.error("Failed to retrieve grades")
            return None

        grades_for_each_semester = await self._make_student_info_request(
            "https://klas.kw.ac.kr/std/cps/inqire/AtnlcScreSungjukInfo.do",
        )
        
        if not grades_for_each_semester:
            logging.error("Failed to retrieve grades for each semester")
            return None

        semester = 0
        for semester_info in grades_for_each_semester:
            if semester_info.get("hakgiOrder") == "계절학기(동계)":
                continue
            semester += 1

        grade = student_info.get("grade", "N/A")
        student_id = student_info.get("hakbun", "N/A")
        major = student_info.get("hakgwa", "N/A")
        student_name = student_info.get("kname", "N/A")
        student_credits = grades.get("chidukHakjum", 0)
        elective_credits = grades.get("cultureChidukHakjum", 0)
        major_credits = grades.get("majorChidukHakjum", 0)
        average_score = grades.get("jaechulScoresum", "N/A")

        if major and "소프트웨어" in major:
            total_credits = 133
            total_major_credits = 60
            total_elective_credits = 30

            try:
                credits_ratio = round((student_credits / total_credits) * 100, 2)
                major_credits_ratio = round((major_credits / total_major_credits) * 100, 2)

                credits_for_each_semester = round(
                    (total_credits - student_credits) / max(1, (4 * 2 - semester + 1)), 2
                )
                major_credits_for_each_semester = round(
                    (total_major_credits - major_credits) / max(1, (4 * 2 - semester + 1)), 2
                )
            except (ZeroDivisionError, TypeError):
                credits_ratio = "N/A"
                major_credits_ratio = "N/A"
                credits_for_each_semester = "N/A"
                major_credits_for_each_semester = "N/A"
        else:
            total_credits = "N/A"
            total_major_credits = "N/A"
            total_elective_credits = "N/A"
            credits_ratio = "N/A"
            major_credits_ratio = "N/A"
            credits_for_each_semester = "N/A"
            major_credits_for_each_semester = "N/A"

        return {
            "uid": student_id,
            "name": student_name,
            "major": major,
            "grade": grade,
            "semester": semester,
            "credits": {
                "total": student_credits,
                "required": total_credits,
                "ratio": credits_ratio,
            },
            "major_credits": {
                "total": major_credits,
                "required": total_major_credits,
                "ratio": major_credits_ratio,
            },
            "elective_credits": {
                "total": elective_credits,
                "required": total_elective_credits,
            },
            "average_score": average_score,
            "credits_for_each_semester": credits_for_each_semester,
            "major_credits_for_each_semester": major_credits_for_each_semester,
        }

    @staticmethod
    def _decode_data_uri(src: str) -> Optional[bytes]:
        header, _, encoded = src.partition(",")
        if not encoded or "base64" not in header:
            logging.warning("Student photo is not an inline base64 image")
            return None
        try:
            # b64decode raises binascii.Error, a ValueError subclass.
            return base64.b64decode(encoded)
        except ValueError as e:
            logging.error(f"Could not decode student photo: {e}")
            return None

    @staticmethod
    def _text_reader(url: str):
        """Build a handler that returns a page's markup, or None on an error status."""

        async def read_text(response) -> Optional[str]:
            if response.status != 200:
                logging.error(
                    f"Failed to retrieve {url}. Status code: {response.status}"
                )
                return None
            return await response.text()

        return read_text

    async def _get_html(self, url: str) -> Optional[str]:
        return await self._request_with_retries(
            url, lambda: self.session.get(url), self._text_reader(url)
        )

    async def get_student_photo(self) -> Optional[bytes]:
        """Return the student's ID photo as JPEG bytes, or None.

        KLAS itself no longer renders the photo: MyNumberQrStdPage.do only
        embeds an iframe pointing at the mobile-ID site, carrying a token that
        expires about a minute after the page is built — so the three requests
        below have to run back to back. The photo on that page is an inline
        base64 data URI, not a fetchable URL, which is why this returns bytes.
        """
        if not self._cookies_is_valid():
            return None

        qr_page_url = "https://klas.kw.ac.kr/std/sys/optrn/MyNumberQrStdPage.do"
        info_url = "https://did-3.kw.ac.kr/std/app/myidv2_main.php?menu=info"
        body = {
            "selectedGrcode": "",
            "selectedYearhakgi": "",
            "selectedSubj": "",
        }

        try:
            qr_page = await self._request_with_retries(
                qr_page_url,
                lambda: self.session.post(
                    qr_page_url,
                    headers=self.headers,
                    json=body,
                ),
                self._text_reader(qr_page_url),
            )
            if not qr_page:
                return None

            iframe = BeautifulSoup(qr_page, "html.parser").select_one("iframe#qrimg")
            if not iframe or not iframe.get("src"):
                logging.warning("Could not find the mobile-ID iframe on the QR page")
                return None

            # The token URL answers with nothing but a JavaScript redirect; what
            # matters is the mobile-ID session cookie it leaves in the jar, which
            # is what authorises the personal-info page fetched next.
            if await self._get_html(iframe["src"]) is None:
                return None

            info_page = await self._get_html(info_url)
            if not info_page:
                return None

            img_tag = BeautifulSoup(info_page, "html.parser").select_one(
                "img[alt='faceofperson']"
            )
            if not img_tag or not img_tag.get("src"):
                logging.warning("Could not find student photo image tag")
                return None

            return self._decode_data_uri(img_tag["src"])
        except Exception as e:
            logging.error(f"Failed to retrieve student photo: {e}")
            return None


if __name__ == "__main__":
    import getpass

    logging.basicConfig(level=logging.INFO)

    async def main():
        async with KwangwoonUniversityApi() as student:
            username = input("Enter your ID: ")
            # getpass: the password must not be echoed into a terminal that
            # gets copied or logged.
            password = getpass.getpass("Enter your password: ")
            result = await student.attempt_login(username, password)
            if result.status is LoginStatus.TWO_FACTOR_REQUIRED:
                push = await student.request_push()
                print(f"push: {push.status.value} - approve it in the KLAS app")
                if push.status is PushStatus.SENT:
                    result = await student.wait_for_push_approval(password, push)
            print(f"login: {result.status.value}")
            if result.status is not LoginStatus.OK:
                return
            todo_list = await student.get_todo_list()
            student_info = await student.get_student_info()
            student_photo = await student.get_student_photo()
            print(f"student photo: {len(student_photo) if student_photo else 0} bytes")
            print(student_info)
            print(todo_list)

    asyncio.run(main())
