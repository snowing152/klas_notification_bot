import json
import logging
import sys
import time

import pytest

from app.utils.log_format import KoreanTimeFormatter, RailwayFormatter


@pytest.fixture
def utc_host(monkeypatch):
    """The process clock as Railway has it. app/config.py asks for Asia/Seoul,
    and an image with no system tz database gives UTC all the same."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def make_record(level=logging.INFO, message="Notification cycle finished", exc_info=None):
    record = logging.LogRecord("root", level, __file__, 1, message, None, exc_info)
    # 2026-10-06 12:37:06 UTC, the first line of the log export this guards.
    record.created = 1791290226.0
    record.msecs = 966.0
    return record


def test_log_time_is_korean_whatever_the_host_clock_says(utc_host):
    # Every line of a week of production logs was stamped nine hours early.
    line = KoreanTimeFormatter().format(make_record())

    assert line == "2026-10-06 21:37:06,966 - root - INFO - Notification cycle finished"


def test_railway_line_carries_the_level_railway_filters_by():
    # Plain text on stdout is "info" to Railway whatever the line says, and a
    # week of ERROR lines could not be found by severity.
    formatter = RailwayFormatter()

    levels = {
        level: json.loads(formatter.format(make_record(level)))["level"]
        for level in (
            logging.DEBUG,
            logging.INFO,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        )
    }

    assert levels == {
        logging.DEBUG: "debug",
        logging.INFO: "info",
        logging.WARNING: "warn",
        logging.ERROR: "error",
        logging.CRITICAL: "error",
    }


def test_railway_line_keeps_the_text_of_the_plain_one():
    record = make_record(message="과제 알림 sent")

    entry = json.loads(RailwayFormatter().format(record))

    assert entry["message"] == KoreanTimeFormatter().format(record)


def test_railway_keeps_a_traceback_in_one_entry():
    # Railway reads one entry per line of output; a traceback printed as text
    # arrived as a dozen entries with no time or level of their own.
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = make_record(logging.ERROR, "Error in main loop", sys.exc_info())

    line = RailwayFormatter().format(record)

    assert "\n" not in line
    assert "RuntimeError: boom" in json.loads(line)["message"]
