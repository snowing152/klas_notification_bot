# Time

Why the bot runs on Seoul time whatever the host says, and why an assignment carries its
deadline rather than the time left.

## Timezone is load-bearing

KLAS returns Korean-local date strings with no offset, and
`app/services/kw.py` compares/subtracts them against a naive "now"; running on any other
clock computes every deadline hours off. **Date-sensitive code calls `timezone.now()`
from `app/utils/timezone.py`** (naive Seoul time via `ZoneInfo`, escape hatch `KW_TZ`) —
never `datetime.now()`. That covers `kw.py` and `food.py`'s weekday lookups.
`app/config.py` additionally overwrites the process `TZ` at import as a fallback for
everything else; it's a fallback because it depends on system tzdata a slim container may
not have, and on config being imported before the first date is resolved. Don't "fix"
either by respecting the host `TZ` — some platforms export `TZ=UTC` themselves, which is
exactly what this defends against. The `tzdata` pin in `requirements.txt` is what makes
`ZoneInfo` resolve `Asia/Seoul` without a system tz database.

Things that follow:

- `timezone.now()` is naive on purpose. It is compared against, and subtracted from, the
  naive datetimes parsed out of the KW endpoints, and mixing naive with aware raises.
- Quiet hours are read through `timezone.now()`, so `quiet_start` and `quiet_end` are
  Korean local hours ([NOTIFICATIONS.md](NOTIFICATIONS.md#settings)).
- Log times are Korean on every host as well, read from the same `ZoneInfo`
  ([DEPLOYMENT.md](DEPLOYMENT.md#logs)).
- `KW_TZ` is read with `os.getenv` directly in both `app/config.py` and
  `app/utils/timezone.py`, deliberately not through `settings`: importing config there
  would make every service import the whole settings object just to ask what time it is.
  Leave it unset. Setting `TZ` in a deploy has no effect.
- Tests that depend on the date pass a `now` in or monkeypatch `timezone.now`; they do
  not rely on the machine's clock.

## Deadlines travel as deadlines, not as countdowns

Every assignment dict from
`get_todo_list()` carries `expire_at` — the deadline as naive Seoul time — and nothing
downstream stores "time left". A countdown is only true at the instant it is computed,
and this list takes seconds to build on a slow day and is then cached for five
minutes, so a stored one drifts twice over: lines of one `/show` would be measured from
different instants, and a cached list would keep claiming minutes already gone.
`todos._render_chunks` and `notifications._collect_messages` each take a single
`timezone.now()` and subtract it at render. Dropping what is already past belongs to the
same moment — `todos._pending`, not `_flatten_and_sort`, which may only sort, since the
order by deadline holds however late the cache is read.

Where KLAS gives only a date, as it does for a discussion's window, the deadline is that
day's 23:59 ([KLAS.md](KLAS.md#assignment-categories)).
