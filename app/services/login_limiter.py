"""Cap rejected KLAS passwords per student number.

KLAS locks an account after five wrong passwords. /register takes a student
number from whoever types it, so without a cap anyone could lock another
student out of KLAS by guessing at their number. The count is per student
number, across every Telegram account.

Kept in memory: a restart resets it. That is acceptable because KLAS's own
lock-out is the real backstop and a restart is rare; the cap only has to stop
a person typing, not a determined attacker with a way to restart the bot.
"""

import time

MAX_FAILURES = 3
WINDOW_SECONDS = 3600

# Above this many tracked student numbers, expired entries are dropped so a
# flood of made-up numbers cannot grow the dict without bound.
_PRUNE_AFTER_ENTRIES = 1000

_failures: dict[str, list[float]] = {}


def _recent(login_id: str, now: float) -> list[float]:
    stamps = [t for t in _failures.get(login_id, []) if now - t < WINDOW_SECONDS]
    if stamps:
        _failures[login_id] = stamps
    else:
        _failures.pop(login_id, None)
    return stamps


def is_locked(login_id: str) -> bool:
    """True when this student number already had its allowed failed logins."""
    return len(_recent(login_id.strip(), time.monotonic())) >= MAX_FAILURES


def record_failure(login_id: str) -> None:
    """Count one password KLAS rejected. Network failures must not call this."""
    now = time.monotonic()
    if len(_failures) >= _PRUNE_AFTER_ENTRIES:
        for key in list(_failures):
            _recent(key, now)
    key = login_id.strip()
    _recent(key, now)
    _failures.setdefault(key, []).append(now)


def reset(login_id: str) -> None:
    """Forget the failures of a student number whose password KLAS accepted."""
    _failures.pop(login_id.strip(), None)
