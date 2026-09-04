import os
import json
import logging
import asyncio
import datetime
from typing import Optional, Dict, List

import aiohttp
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from fake_useragent import UserAgent
from bs4 import BeautifulSoup
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
        self.cookies = cookies

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
                        f"attempts: {e!r}"
                    )
                    raise
                logging.warning(
                    f"Connection error on {url} ({e!r}), "
                    f"retrying in {RETRY_DELAY_SECONDS}s"
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)

    async def login_with_cookies(self, cookies: dict) -> bool:
        self.set_cookies(cookies)
        login_form_url = "https://klas.kw.ac.kr/usr/cmn/login/LoginForm.do"

        async with self.session.get(login_form_url, cookies=self.cookies) as response:
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
        "this user could not be read this time" and move on.
        """
        login_form_url = "https://klas.kw.ac.kr/usr/cmn/login/LoginForm.do"
        public_key_url = "https://klas.kw.ac.kr/usr/cmn/login/LoginSecurity.do"
        login_url = "https://klas.kw.ac.kr/usr/cmn/login/LoginConfirm.do"

        async def redirected_away_from_login_form(response) -> bool:
            # KLAS bounces us off the login form when the cookies we already
            # hold are still valid, so there is nothing left to log in to.
            return str(response.url) != login_form_url

        async def read_public_key(response) -> str:
            return (await response.json())["publicKey"]

        async def read_login_result(response) -> Optional[Dict]:
            if response.status != 200:
                logging.error("Failed to communicate with server.")
                logging.error(f"Status code: {response.status}")
                return None
            return await response.json()

        try:
            if await self._request_with_retries(
                login_form_url,
                lambda: self.session.get(login_form_url, cookies=self.cookies),
                redirected_away_from_login_form,
            ):
                logging.info("Logged in with existing cookies.")
                return self._cookies_from_jar()

            public_key_str = await self._request_with_retries(
                public_key_url,
                lambda: self.session.post(public_key_url),
                read_public_key,
            )

            login_data = {"loginId": login_id, "loginPwd": login_pwd, "storeIdYn": "Y"}
            encrypted_login = self._encryptor(public_key_str, json.dumps(login_data))
            if not encrypted_login:
                logging.error("Encryption failed")
                return None

            login_body = {
                "loginToken": encrypted_login,
                "redirectUrl": "/std/cmn/frame/Frame.do",
                "redirectTabUrl": "",
            }

            response_data = await self._request_with_retries(
                login_url,
                lambda: self.session.post(
                    login_url,
                    json=login_body,
                    headers=self.headers,
                    cookies=self.cookies,
                ),
                read_login_result,
            )
            if response_data is None:
                return None

            if response_data.get("errorCount", 0) == 0:
                logging.debug("Login successful.")
                return self._cookies_from_jar()
            elif (
                response_data.get("fieldErrors")[0].get("message")
                == "비밀번호 실패 5회 초과로 인하여 계정이 잠겼습니다.\n비밀번호 찾기를 이용해주세요."
            ):
                logging.error(
                    "Login failed. Enter wrong password 5 times. Please reset password."
                )
                return None
            else:
                logging.error(
                    "Failed to parse response. Login failed. Wrong password or ID"
                )
                return None

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            return None

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

        return await self._request_with_retries(
            url,
            lambda: self.session.post(
                url=url,
                json=body,
                headers=headers or self.headers,
                cookies=self.cookies,
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

    def _get_not_done_lectures_info(self, lectures: list[dict]) -> list[dict]:
        not_done_lectures = []
        today_date = timezone.now().strftime("%Y-%m-%d %H:%M")
        for lecture in lectures:
            if (
                lecture.get("prog") is not None
                and lecture.get("prog") < 100
                and lecture.get("startDate") < today_date
                and lecture.get("endDate") > today_date
            ):
                not_done_lectures.append(
                    {
                        "title": lecture.get("sbjt"),
                        "progress": lecture.get("prog"),
                        "expire_date": lecture.get("endDate"),
                        "left_time": self._get_left_time(
                            lecture.get("endDate"), "%Y-%m-%d %H:%M"
                        ),
                    }
                )
        return not_done_lectures

    def _get_not_done_homeworks_info(self, homeworks: list[dict]) -> list[dict]:
        not_done_homeworks = []
        today_date = timezone.now().strftime("%Y-%m-%d %H:%M")
        for homework in homeworks:
            if (
                homework.get("submityn") == "N"
                and homework.get("startdate") < today_date
                and homework.get("expiredate") > today_date
            ):
                not_done_homeworks.append(
                    {
                        "title": homework.get("title"),
                        "expire_date": homework.get("expiredate"),
                        "left_time": self._get_left_time(
                            homework.get("expiredate"), "%Y-%m-%d %H:%M:%S"
                        ),
                    }
                )
        return not_done_homeworks

    def _get_not_done_team_projects_info(self, team_projects: list[dict]) -> list[dict]:
        not_done_team_projects = []
        today_date = timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        for team_project in team_projects:
            if (
                team_project.get("submityn") != "Y"
                and team_project.get("startdate") < today_date
                and team_project.get("expiredate") > today_date
            ):
                not_done_team_projects.append(
                    {
                        "title": team_project.get("title"),
                        "expire_date": team_project.get("expiredate"),
                        "left_time": self._get_left_time(
                            team_project.get("expiredate"),
                            "%Y-%m-%dT%H:%M:%S.%f%z",
                            True,
                        ),
                    }
                )
        return not_done_team_projects

    def _get_not_done_quizzes_info(self, quizzes: list[dict]) -> list[dict]:
        not_done_quizzes = []
        today_date = timezone.now().strftime("%Y%m%d%H%M")
        for quiz in quizzes:
            if (
                quiz.get("issubmit") == "N"
                and quiz.get("sdate") < today_date
                and quiz.get("edate") > today_date
            ):
                not_done_quizzes.append(
                    {
                        "title": quiz.get("papernm"),
                        "expire_date": quiz.get("edt"),
                        "left_time": self._get_left_time(
                            quiz.get("edt"), "%Y-%m-%d %H:%M"
                        ),
                    }
                )
        return not_done_quizzes

    def _get_left_time(self, expire_date, date_format, remove_timezone=False):
        expire_date_time = datetime.datetime.strptime(expire_date, date_format)
        if remove_timezone:
            expire_date_time = expire_date_time.replace(tzinfo=None)
        now_time = timezone.now()
        return expire_date_time - now_time

    async def get_todo_list(self) -> Optional[List[Dict]]:
        if not self._cookies_is_valid():
            return None

        subjects = await self.get_subjects()
        if not subjects:
            return None

        todo_list = [
            {"id": subject.get("value"), "name": subject.get("name"), "todo": {}}
            for subject in subjects.get("subjList")
        ]

        try:
            subject_semester = subjects.get("value")

            # Use asyncio.gather to fetch all data concurrently
            for todo in todo_list:
                year = subject_semester
                subject_id = todo.get("id")

                lectures, homeworks, team_projects, quizzes = await asyncio.gather(
                    self._get_lectures(subject_id, year),
                    self._get_homeworks(subject_id, year),
                    self._get_team_projects(subject_id, year),
                    self._get_quizzes(subject_id, year),
                )

                if None in (lectures, homeworks, team_projects, quizzes):
                    # One rejected request should cost this subject's missing
                    # category, not the whole user's todo list.
                    logging.warning(
                        f"Incomplete assignment data for subject {subject_id}"
                    )

                todo["todo"] = {
                    "lectures": self._get_not_done_lectures_info(lectures or []),
                    "homeworks": self._get_not_done_homeworks_info(homeworks or []),
                    "team_projects": self._get_not_done_team_projects_info(
                        team_projects or []
                    ),
                    "quizzes": self._get_not_done_quizzes_info(quizzes or []),
                }

            return todo_list

        except Exception as e:
            logging.error(f"An error occurred while getting todo list: {e}")
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
                    cookies=self.cookies,
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
    logging.basicConfig(level=logging.INFO)

    async def main():
        async with KwangwoonUniversityApi() as student:
            username = input("Enter your ID: ")
            password = input("Enter your password: ")
            await student.login(username, password)
            todo_list = await student.get_todo_list()
            student_info = await student.get_student_info()
            student_photo = await student.get_student_photo()
            print(f"student photo: {len(student_photo) if student_photo else 0} bytes")
            print(student_info)
            print(todo_list)

    asyncio.run(main())
