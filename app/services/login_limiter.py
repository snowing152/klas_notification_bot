"""Cap rejected KLAS passwords per student number.

KLAS locks an account after five wrong passwords. /register takes a student
number from whoever types it, so without a cap anyone could lock another
student out of KLAS by guessing at their number. The count is per student
number, across every Telegram account.

Attempts are reserved before the (slow) KLAS call and settled after it, so
several accounts submitting at the same moment cannot all pass a check made
before any of them has failed: in-flight attempts count like failures.

Kept in memory: a restart resets it. That is acceptable because KLAS's own
lock-out is the real backstop and a restart is rare; the cap only has to stop
a person typing, not a determined attacker with a way to restart the bot.
"""

import time
from collections import OrderedDict

MAX_FAILURES = 3
WINDOW_SECONDS = 3600

# Hard cap on tracked student numbers, so a flood of made-up numbers cannot
# grow the dict without bound; the oldest entry is evicted first.
MAX_TRACKED = 1000

# student number -> times of rejected passwords, oldest number first.
_failures: "OrderedDict[str, list[float]]" = OrderedDict()
# student number -> attempts reserved and not yet settled.
_in_flight: dict[str, int] = {}


def _recent(login_id: str, now: float) -> list[float]:
    stamps = [t for t in _failures.get(login_id, []) if now - t < WINDOW_SECONDS]
    if stamps:
        _failures[login_id] = stamps
    else:
        _failures.pop(login_id, None)
    return stamps


def _used(login_id: str, now: float) -> int:
    return len(_recent(login_id, now)) + _in_flight.get(login_id, 0)


def is_locked(login_id: str) -> bool:
    """True when this student number has used up its failed and pending logins."""
    return _used(login_id.strip(), time.monotonic()) >= MAX_FAILURES


def reserve(login_id: str) -> bool:
    """Take a slot for one login attempt; False when none is left.

    Runs without awaiting, so the check and the reservation cannot interleave
    with another task. Every True must be paired with one finish().
    """
    key = login_id.strip()
    if _used(key, time.monotonic()) >= MAX_FAILURES:
        return False
    _in_flight[key] = _in_flight.get(key, 0) + 1
    return True


def finish(login_id: str, *, rejected: bool = False, accepted: bool = False) -> None:
    """Settle a reserved attempt.

    `rejected`: KLAS refused the password, so the slot becomes a recorded
    failure. `accepted`: KLAS took the password, so earlier failures are
    forgotten. Neither (a network failure, a refused push): the slot is just
    released.
    """
    key = login_id.strip()
    left = _in_flight.get(key, 0) - 1
    if left > 0:
        _in_flight[key] = left
    else:
        _in_flight.pop(key, None)

    if accepted:
        _failures.pop(key, None)
    elif rejected:
        now = time.monotonic()
        stamps = _recent(key, now)
        stamps.append(now)
        _failures[key] = stamps
        _failures.move_to_end(key)
        while len(_failures) > MAX_TRACKED:
            _failures.popitem(last=False)
