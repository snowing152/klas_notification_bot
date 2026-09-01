"""Korean local time, independent of the host clock.

KLAS, the quiz endpoint and the cafeteria page all speak Korean local time with
no offset attached ("2026-09-10 23:59"), and the callers compare those strings
against - or subtract them from - a naive datetime. So "now" must mean *Seoul's*
now, on any host.

`app/config.py` also forces the process TZ to Asia/Seoul, but that only reaches
libc: it needs the system tz database to be present (a slim container often has
none) and it is defeated by anything that resolves a date before config is
imported. Reading the zone explicitly here does not depend on either, which is
why every date-sensitive call site uses `now()` below rather than
`datetime.now()`.

The `tzdata` requirement is what makes `ZoneInfo` work on hosts with no
/usr/share/zoneinfo - do not drop it.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Same escape hatch as app/config.py, and deliberately not read from settings:
# importing config from here would make every service import the whole settings
# object just to ask what time it is.
TIMEZONE = ZoneInfo(os.getenv("KW_TZ", "Asia/Seoul"))


def now() -> datetime:
    """Current Korean local time, as a naive datetime.

    Naive on purpose: it is compared against - and subtracted from - the naive
    datetimes parsed out of the KW endpoints, and mixing the two raises.
    """
    return datetime.now(TIMEZONE).replace(tzinfo=None)
