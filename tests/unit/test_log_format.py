import json
import logging
import subprocess
import sys

from app.utils import log_format
from app.utils.log_format import KoreanTimeFormatter, RailwayFormatter


class FakeStdout:
    def __init__(self, terminal):
        self._terminal = terminal

    def isatty(self):
        return self._terminal


def make_record(level=logging.INFO, message="Notification cycle finished", exc_info=None):
    record = logging.LogRecord("root", level, __file__, 1, message, None, exc_info)
    # 2026-10-06 12:37:06 UTC, the first line of the log export this guards.
    record.created = 1791290226.0
    record.msecs = 966.0
    return record


def test_log_time_is_korean_whatever_the_host_clock_says(host_timezone):
    # Every line of a week of production logs was stamped nine hours early:
    # app/config.py asks for Asia/Seoul, and an image with no system tz
    # database gives UTC all the same.
    host_timezone("UTC")

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


def test_railway_line_survives_half_an_emoji():
    # A lone surrogate cannot be written to a UTF-8 stream. Left unescaped it
    # raised inside the handler and the line it was part of never appeared.
    line = RailwayFormatter().format(make_record(logging.ERROR, "bad payload \ud83d"))

    line.encode("utf-8")
    assert json.loads(line)["message"].endswith("bad payload \ud83d")


def test_railway_run_in_a_terminal_is_not_the_deployment(monkeypatch, tmp_path):
    """`railway run python main.py` sets RAILWAY_ENVIRONMENT in a local shell,
    where JSON lines are read by a person and the log file is wanted."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setattr(log_format.platform, "system", lambda: "Linux")

    monkeypatch.setattr(sys, "stdout", FakeStdout(terminal=True))
    local = log_format.build_handlers(str(tmp_path))
    monkeypatch.setattr(sys, "stdout", FakeStdout(terminal=False))
    deployed = log_format.build_handlers(str(tmp_path / "unused"))

    try:
        assert [type(h.formatter) for h in local] == [KoreanTimeFormatter] * 2
        assert (tmp_path / "kwbot.log").exists()
        assert [type(h.formatter) for h in deployed] == [RailwayFormatter]
        assert not (tmp_path / "unused").exists()
    finally:
        for handler in local + deployed:
            handler.close()


def test_off_railway_the_log_is_plain_text(monkeypatch, tmp_path):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setattr(sys, "stdout", FakeStdout(terminal=False))  # systemd
    monkeypatch.setattr(log_format.platform, "system", lambda: "Linux")

    handlers = log_format.build_handlers(str(tmp_path))

    try:
        assert [type(h.formatter) for h in handlers] == [KoreanTimeFormatter] * 2
    finally:
        for handler in handlers:
            handler.close()


def test_an_uncaught_exception_is_one_log_entry(caplog):
    try:
        raise RuntimeError("DATA_DIR does not exist")
    except RuntimeError:
        log_format.log_uncaught(*sys.exc_info())

    (record,) = caplog.records
    assert record.levelno == logging.CRITICAL
    assert "DATA_DIR does not exist" in RailwayFormatter().format(record)


def test_a_broken_config_is_logged_and_not_just_printed(tmp_path):
    """The whole reason logging is set up before the rest of app is imported:
    app/config.py raises at import, and that traceback used to reach Railway
    as a dozen unlabelled lines of stderr."""
    # main.py as the entry point, with asyncio.run disarmed: whatever this
    # environment turns out to hold, the test can never start the real bot.
    run_main = (
        "import asyncio, runpy\n"
        "asyncio.run = lambda coro: coro.close()\n"
        "runpy.run_path('main.py', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", run_main],
        env={
            "PATH": "",
            "RAILWAY_ENVIRONMENT": "production",
            "BOT_TOKEN": "",
            "ADMIN_ID": "1",
            "DATA_DIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    (line,) = result.stdout.splitlines()
    entry = json.loads(line)
    assert entry["level"] == "error"
    assert "BOT_TOKEN must be set" in entry["message"]
    assert result.stderr == ""
