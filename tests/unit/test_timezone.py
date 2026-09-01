import datetime
import os
import time
from zoneinfo import ZoneInfo

import pytest

from app.utils import timezone


@pytest.fixture
def host_timezone():
    """Move the *process* clock to another zone, the way a US host would.

    app/config.py forces TZ=Asia/Seoul at import, so the test has to undo that
    to reproduce what the deployment actually looks like.
    """
    original = os.environ.get("TZ")

    def _set(name: str):
        os.environ["TZ"] = name
        time.tzset()

    yield _set

    if original is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = original
    time.tzset()


def test_now_is_korean_time_on_a_non_korean_host(host_timezone):
    # The bug this guards: deployed on a US server, every deadline came out
    # 13 hours away from the truth because "now" was the host's local time.
    host_timezone("America/New_York")

    assert timezone.now().replace(microsecond=0) == datetime.datetime.now(
        ZoneInfo("Asia/Seoul")
    ).replace(tzinfo=None, microsecond=0)


def test_now_is_naive(host_timezone):
    # It is subtracted from the naive datetimes parsed out of KLAS; an aware
    # value would raise there instead.
    host_timezone("UTC")

    assert timezone.now().tzinfo is None
