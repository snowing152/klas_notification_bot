# KLAS

`app/services/kw.py` — the client for `klas.kw.ac.kr`, what it reads, and what to do
when KLAS changes. KLAS has no public API: every URL here was taken from what the web
interface itself requests, and none of it is documented or promised to stay.

## The client

`KwangwoonUniversityApi` is an async context manager — always use `async with`, one
instance per operation. It never raises for a network fault: a falsy answer means "this
user could not be read this time", and every caller checks it.

### Login and the second factor

Since November 2026 KLAS can ask for a second factor on a login: a push the student
approves in the KLAS phone app. It does not ask every time, and the rule is unknown. On
2026-11-24 the browser was asked on every login, and two script runs from the same PC
minutes apart got one login without and one with the push; the production server is
asked. Code must handle both answers. The flow, captured from a browser login on
2026-11-24:

1. `LoginSecurity.do` hands out a fresh RSA public key; the credentials are encrypted
   with it (PKCS#1 v1.5, what the page's jsencrypt uses) into `loginToken`.
2. `LoginCaptcha.do` `{loginToken, captcha: ""}` answers `0` when no captcha is due.
   Any other answer is logged and left to the next step to judge.
3. `LoginConfirm.do` `{loginToken, captcha: "", redirectUrl, redirectTabUrl}` answers
   `errorCount: 0` **and** `response.twoFactorAt: "Y"` when the password is right but
   the push is still due. Only `errorCount: 0` without `twoFactorAt` is a login.
4. `/ext/pass/UserCertInfoOne.do` (body in `request_push`) answers `status: "1"` when
   a phone is registered for push; otherwise the student has to install the KLAS app.
5. `POST /usr/cmn/cert/UserCertPushResult.do?loginId=<id>` **sends the push**. The
   EUC-KR page carries the push id `tid` and its expiry `limit` (epoch ms, one hour
   out) in an inline script; `_parse_push_page` reads them.
6. `/ext/pass/UserCertPushChk.do` `{loginId, tid, limit}` answers `returnCode: "0000"`
   once the push is approved. The browser asks once, when the student clicks 확인;
   the bot polls.
7. Steps 1-3 again **on the same session cookies**: KLAS remembers the approval in the
   session, and this time `LoginConfirm.do` answers `redirect: true`.

In code: `attempt_login()` runs the login form check and steps 1-3 and answers a
`LoginResult`: `OK`, `TWO_FACTOR_REQUIRED`, `FAILED` (KLAS rejected the credentials) or
`UNAVAILABLE` (KLAS unreachable or answering with an error, which says nothing about the
password and must never be reported as a wrong one). `request_push()` runs steps 4-5
and `wait_for_push_approval()` steps 6-7 on the same instance. The bot treats only the
second `LoginConfirm.do` as proof: a `0000` from `UserCertPushChk.do` that is not
followed by a login is retried at most `MAX_CREDENTIAL_RESUBMITS` times. `login()` is the
old interface: cookies on `OK`, `None` otherwise, so it can no longer finish a login on
its own.

A session ends after two idle hours (`/api/v1/session/info` reports `remainingTime`,
7200 right after login). Polling `session/info` does not extend it; `GET
UpdateSession.do` resets it, which is what KLAS's own "연장" button calls.
`keep_alive()` calls it and then checks the session is still logged in; it answers
`None`, not `False`, when KLAS could not be reached, so a network fault never ends a kept
session. Whether KLAS also has a hard limit on how long a session may live is not known
yet: `klas_session.mark_session_dead` logs `KLAS session of <user> ended after <n> hours`
to find out.

`app/services/klas_session.py` keeps one session per user between operations.
`open_session(user)` restores the stored cookies, calls `keep_alive()`, and yields
`ACTIVE` with a client, `NEEDS_LOGIN`, `PASSWORD_REJECTED` or `UNAVAILABLE` (KLAS
unreachable). The cookies are written back on the way out, since KLAS may rotate them,
and `save_new_session()` stores the session of a login the student just approved.

When there is no live session, `open_session` first tries a **silent login**: the saved
password through `attempt_login()` only, which never sends a push. If KLAS lets it in,
the new session is stored and the caller never notices. If KLAS wants the push,
`klas_sessions.two_factor_required` is set and the answer is `NEEDS_LOGIN`; if it
rejects the password, `password_rejected` is set and the answer is `PASSWORD_REJECTED`.
Either flag stops further silent tries until the student logs in or re-registers, so a
lost session costs one silent login, not one every cycle, and a rejected password is
never resent (KLAS locks the account after five). A session replaced by a newer login
while it was being checked answers `UNAVAILABLE` for that one operation rather than
logging in over it.

`app/handlers/klas_login.py` (`run_push_login`) runs a whole login from a chat: it sends
the push, tells the student to approve it, waits, stores the session, and explains a
timeout, a missing phone or a failure, with the "Log in to KLAS" button where trying
again helps. Only a user action starts it — `/register` (`auth.process_password`) or
that button (`auth.process_klas_relogin`, callback `klas_login`) — never the notification
loop: a push nobody asked for every half hour would be spam, and would teach students to
approve pushes they did not start. `/register` saves the credentials as soon as KLAS has
accepted the password and before the push goes out, so a push that times out is retried
with the button rather than by typing the password again. One login per user at a time:
a second tap answers "already waiting" instead of sending a second push.

When KLAS rejects the saved password on a re-login, `klas_sessions.password_rejected` is
set and the button refuses to try again until `/register` saves a password KLAS accepts:
KLAS locks the account after five wrong passwords, and every tap would resend the same
one. Writes made at the end of an operation (`_store_cookies`, `mark_session_dead`) are
conditional on the login the operation started with (`logged_in_at`) and never insert,
so they cannot bring back a session deleted by `/unregister` or replace a newer login.
Requests take cookies from the client's jar only: passing them per request as well
would override any cookie KLAS rotates, since aiohttp lets request cookies win.

KLAS regularly drops idle keep-alive connections, so requests go through
`_request_with_retries`: `REQUEST_RETRIES = 2` replays one second apart, on connection
errors and timeouts only, and each request has a total timeout of
`REQUEST_TIMEOUT_SECONDS = 30`. Without it one hung request stalls the notification cycle
for every remaining user. Every retried call is an idempotent read.

`get_todo_list()` asks about every subject at once — one task per subject, five
requests in each — because the wait is round trips to Korea and, taken in turn, every
subject added one more. What keeps that from being a burst is `_request_slots`: a single
module-level semaphore, `MAX_CONCURRENT_REQUESTS = 10`, that every `_post_json` call of
every instance goes through. It is per process on purpose: KLAS sees one address whoever
the request is for, and the notification cycle reads several users at a time. A request
keeps its slot while it retries. The login steps are not counted; they run one after
another.

The requests run in `asyncio.TaskGroup`s, not `gather()`: one that fails for good cancels
everything else still in flight for that user, so nothing is left running against a
session the caller is about to close. The error then logged lists every failure in the
group, and its traceback says which subject each belonged to.

Reading subjects concurrently is safe because KLAS answers from the request body, not
from a "selected subject" kept in the session, even though every body carries
`selectChangeYn: "Y"`. Checked on 2026-10-13: 150 requests for six subjects interleaved
on one session matched the answers taken one at a time. If a category ever shows up
under the wrong subject, this is the first thing to check again.

What the return values mean:

| Call | `None` | Empty |
|---|---|---|
| `login()` | login failed, KLAS was unreachable, or the second factor is due | — |
| `_post_json()` | error status, or a body that is not JSON (logged as an error) | — |
| `get_todo_list()` | KLAS could not be read | `[]`: the student has no subjects this semester |

`_post_json` treats a 200 with an empty body as "no data": KLAS answers that way for
records a student does not have yet, a first-semester student asking for grades for
instance. One rejected request inside `get_todo_list()` costs that subject's category and
an `Incomplete assignment data for subject ...` warning, not the whole list. The subject
is then returned with `"incomplete": True`, because its empty category is otherwise
exactly what "everything handed in" looks like: the notification cycle keeps its history
instead of pruning it ([NOTIFICATIONS.md](NOTIFICATIONS.md)), and `/show` does not cache
the list. Anything new that acts on work being *absent* has to read the flag too.

## Endpoints in use

All under `https://klas.kw.ac.kr`.

| Path | Used for |
|---|---|
| `/usr/cmn/login/LoginForm.do`, `LoginSecurity.do`, `LoginCaptcha.do`, `LoginConfirm.do` | login |
| `/ext/pass/UserCertInfoOne.do`, `/usr/cmn/cert/UserCertPushResult.do`, `/ext/pass/UserCertPushChk.do` | second factor (push) |
| `/usr/cmn/login/UpdateSession.do` | extending the session |
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
the user if it appears in `TYPE_EMOJIS` (`app/utils/assignment_render.py`): it drives `/show`,
`notifications.py` imports the same dict and `_collect_messages` only looks at its keys,
and `ASSIGNMENT_TYPES` in the handler decides which side of the lectures/assignments
filter it falls on. No label string is needed: the emoji is the label.
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
   list of fetches and the returned dict in `_get_subject_todo()`;
2. `TYPE_EMOJIS` (and `TYPE_ORDER`, the tie-break order) in `app/utils/assignment_render.py`, and `ASSIGNMENT_TYPES` in `app/handlers/todos.py` unless it is
   something to watch rather than submit;
3. tests in `tests/unit/test_kw.py`, fed with a captured response.

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
