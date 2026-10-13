# KLAS

`app/services/kw.py` — the client for `klas.kw.ac.kr`, what it reads, and what to do
when KLAS changes. KLAS has no public API: every URL here was taken from what the web
interface itself requests, and none of it is documented or promised to stay.

## The client

`KwangwoonUniversityApi` is an async context manager — always use `async with`, one
instance per operation. `login(login_id, login_pwd)` fetches the login form, reads KLAS's
public key from `LoginSecurity.do`, posts the encrypted credentials to `LoginConfirm.do`
and keeps the cookies. It never raises for a network fault: a falsy answer means "this
user could not be read this time", and every caller checks it.

KLAS regularly drops idle keep-alive connections, so requests go through
`_request_with_retries`: `REQUEST_RETRIES = 2` replays one second apart, on connection
errors and timeouts only, and each request has a total timeout of
`REQUEST_TIMEOUT_SECONDS = 30`. Without it one hung request stalls the notification cycle
for every remaining user. Every retried call is an idempotent read.

What the return values mean:

| Call | `None` | Empty |
|---|---|---|
| `login()` | login failed or KLAS was unreachable | — |
| `_post_json()` | error status, or a body that is not JSON (logged as an error) | — |
| `get_todo_list()` | KLAS could not be read | `[]`: the student has no subjects this semester |

`_post_json` treats a 200 with an empty body as "no data": KLAS answers that way for
records a student does not have yet, a first-semester student asking for grades for
instance. One rejected request inside `get_todo_list()` costs that subject's category and
an `Incomplete assignment data for subject ...` warning, not the whole list.

## Endpoints in use

All under `https://klas.kw.ac.kr`.

| Path | Used for |
|---|---|
| `/usr/cmn/login/LoginForm.do`, `LoginSecurity.do`, `LoginConfirm.do` | login |
| `/std/cmn/frame/YearhakgiAtnlcSbjectList.do` | the semester's subjects |
| `/std/lis/evltn/SelectOnlineCntntsStdList.do` | lectures |
| `/std/lis/evltn/TaskStdList.do` | homeworks |
| `/std/lis/evltn/PrjctStdList.do` | team projects |
| `/std/lis/evltn/AnytmQuizStdList.do` | quizzes |
| `/std/lis/evltn/DscsnStdList.do` | 토론 (discussions) |
| `/std/lis/evltn/DscsnOpinionList.do` | the posts of one discussion |
| `/std/cps/inqire/AtnlcScreHakjukInfo.do` | `/info`: student record |
| `/std/cps/inqire/AtnlcScreSungjukTot.do` | `/info`: grades in total |
| `/std/cps/inqire/AtnlcScreSungjukInfo.do` | `/info`: grades per semester |
| `/std/sys/optrn/MyNumberQrStdPage.do` | `/info`: the page that leads to the ID photo |

The ID photo itself comes from `did-3.kw.ac.kr`: `MyNumberQrStdPage.do` only embeds an
iframe pointing there, carrying a token that expires about a minute after the page is
built, so the requests in `get_student_photo` have to run back to back.

## Assignment categories

`get_todo_list()` reads five per subject — lectures,
homeworks, team projects, quizzes and 토론 (`discussions`) — and a category only reaches
the user if it appears in *both* `TYPE_EMOJIS` dicts: `app/handlers/todos.py`'s drives
`/show`, `app/services/notifications.py`'s decides what `_collect_messages` even looks at,
and `ASSIGNMENT_TYPES` in the handler decides which side of the lectures/assignments
filter it falls on. Each also needs a `type_<name>` string in all three languages.
`discussions` is the odd one: `DscsnStdList.do` carries no "submitted" flag (its
`toroncnt` is how many posts the whole class wrote), so participation costs a second
request, `DscsnOpinionList.do`, per discussion whose date window is open — matched
against `KwangwoonUniversityApi.login_id`, since KLAS stamps each post with a `userId` of
`<student number>UA` and the login id *is* the student number. Never match on a post's
`title` instead: students type that by hand and write number and name in either order.
KLAS gives the window as bare dates, so the deadline is the end day's 23:59.

Every assignment dict carries `title` and `expire_at`; why it is a deadline and never a
countdown is in [TIMEZONE.md](TIMEZONE.md).

Adding a category therefore touches, in order:

1. a `_get_<name>` request and a `_get_not_done_<name>_info` parser in `kw.py`, and the
   `asyncio.gather` and `todo["todo"]` dict in `get_todo_list()`;
2. `TYPE_EMOJIS` in `app/handlers/todos.py`, and `ASSIGNMENT_TYPES` there unless it is
   something to watch rather than submit;
3. `TYPE_EMOJIS` in `app/services/notifications.py`;
4. `type_<name>` in EN, KO and RU ([LOCALIZATION.md](LOCALIZATION.md));
5. tests in `tests/unit/test_kw.py`, fed with a captured response.

## When KLAS changes

How it shows up: `Failed to retrieve <url>. Status code: ...` or
`Non-JSON response from <url>` in the logs, `Could not log in as user ...` for every user
at once, `/show` answering as if the login failed, or a category that silently stops
listing anything.

1. Reproduce it in a browser first: log into KLAS, open the page the feature reads, and
   look at the request in the network tab. Compare the URL and the body fields with what
   `kw.py` sends.
2. **Do not guess a `.do` name.** They are not derivable from one another, and a wrong
   one does not crash — `_post_json` logs an error and returns `None`, so the feature
   looks implemented and shows nothing. Copy the request out of the browser (as curl) and
   work from that.
3. The copied request carries live session cookies. They are enough to probe what the
   endpoint really returns — pagination, whether a "submitted" flag exists, which body
   fields are required — before writing the parser, and the answers belong in a comment
   next to it. Delete any saved cookie file afterwards, and never commit one.
4. Fix the parser, extend `tests/unit/test_kw.py` with the new response shape, and update
   the table above.

The same applies to the other KW hosts listed in
[ARCHITECTURE.md](ARCHITECTURE.md#what-it-talks-to); each has one service module.
