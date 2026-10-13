"""How a log line is written: on the Korean clock, and as JSON on Railway.

The clock: a line's time used to come from the process `TZ`, which
`app/config.py` sets to Asia/Seoul but which only works where the system has a
tz database. Railway's image has none, so every line was stamped in UTC, nine
hours away from the hour the student saw the message. Reading the zone the way
`app/utils/timezone.py` does needs no system tz database.

JSON: Railway takes a plain line's severity from the stream it arrived on, so
everything written to stdout is "info" - the ERROR lines as well, and a filter
by severity in the dashboard came back empty. A single-line JSON object with
`level` and `message` is parsed instead, and a traceback, which plain text
splits into one entry per line, stays in the entry it belongs to.
"""

import json
import logging
from datetime import datetime

from app.utils.timezone import TIMEZONE

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Railway knows four severities, by these names.
RAILWAY_LEVELS = (
    (logging.ERROR, "error"),
    (logging.WARNING, "warn"),
    (logging.INFO, "info"),
)


def korean_time(timestamp: float):
    return datetime.fromtimestamp(timestamp, TIMEZONE).timetuple()


class KoreanTimeFormatter(logging.Formatter):
    """The usual text line, timed in Korean local time on any host."""

    # staticmethod: Formatter calls self.converter(seconds), and a plain
    # function stored on the class would be handed self as well.
    converter = staticmethod(korean_time)

    def __init__(self):
        super().__init__(LOG_FORMAT)


class RailwayFormatter(KoreanTimeFormatter):
    """The same line, wrapped so Railway reads its level."""

    def format(self, record: logging.LogRecord) -> str:
        level = next(
            (name for floor, name in RAILWAY_LEVELS if record.levelno >= floor),
            "debug",
        )
        return json.dumps(
            {"level": level, "message": super().format(record)}, ensure_ascii=False
        )
