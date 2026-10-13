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

That only holds for what goes through `logging`. An exception nobody caught is
printed by the interpreter straight to stderr, so `configure_logging` also
takes over `sys.excepthook` - and must run before the rest of `app` is
imported, because the likeliest startup failure of all is `app/config.py`
raising at import. Nothing in this module may import config, directly or not.
"""

import json
import logging
import os
import platform
import sys
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
        # Escaped to ASCII, which Railway decodes back: with ensure_ascii off, a
        # lone surrogate in a logged value (half an emoji out of a truncated
        # payload) cannot be written to a UTF-8 stream, and the line is lost.
        return json.dumps({"level": level, "message": super().format(record)})


def deployed_on_railway() -> bool:
    """Is this the Railway deployment, with its log collector reading stdout?

    `railway run` puts RAILWAY_ENVIRONMENT into a local shell as well, and
    there a person is reading the terminal, not a JSON parser.
    """
    return bool(os.getenv("RAILWAY_ENVIRONMENT")) and not sys.stdout.isatty()


def build_handlers(log_dir: str) -> list:
    # stdout is always a handler: a PaaS such as Railway collects logs from the
    # process output, and logging only to a file inside an ephemeral container
    # means the dashboard shows nothing and the file is lost on the next deploy.
    handlers = [logging.StreamHandler(sys.stdout)]
    on_railway = deployed_on_railway()

    # Keep the on-disk log for self-hosted (systemd) runs, where it survives.
    if platform.system() == "Linux" and not on_railway:
        os.makedirs(log_dir, exist_ok=True)
        handlers.append(
            logging.FileHandler(
                os.path.join(log_dir, "kwbot.log"), mode="a", encoding="UTF-8"
            )
        )

    # One formatter for every handler: the file handler only exists off Railway,
    # so the on-disk log stays plain text.
    formatter = RailwayFormatter() if on_railway else KoreanTimeFormatter()
    for handler in handlers:
        handler.setFormatter(formatter)
    return handlers


def log_uncaught(exc_type, exc, traceback) -> None:
    """sys.excepthook: an uncaught exception as one log entry, not raw stderr."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, traceback)
        return
    logging.critical("Uncaught exception", exc_info=(exc_type, exc, traceback))


def configure_logging(log_dir: str) -> None:
    logging.basicConfig(level=logging.INFO, handlers=build_handlers(log_dir))
    sys.excepthook = log_uncaught
