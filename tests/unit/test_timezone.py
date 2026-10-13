import datetime
from zoneinfo import ZoneInfo

from app.utils import timezone


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
