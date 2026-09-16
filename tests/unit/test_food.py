import asyncio
from datetime import datetime

import pytest

from app.services import food as food_service


# One breakfast row: Monday, Tuesday, Thursday, Friday have a menu; Wednesday
# is an empty cell, the way a holiday or an unpublished week renders.
HTML_WITH_ONE_EMPTY_DAY = """
<div class="table-scroll-box">
  <table class="tbl-list">
    <tbody class="dietData">
      <tr>
        <td class="vt al"><pre>Mon breakfast</pre></td>
        <td class="vt al"><pre>Tue breakfast</pre></td>
        <td class="vt al"></td>
        <td class="vt al"><pre>Thu breakfast</pre></td>
        <td class="vt al"><pre>Fri breakfast</pre></td>
      </tr>
    </tbody>
  </table>
</div>
"""

HTML_MISSING_STRUCTURE = "<div>nothing useful here</div>"

MONDAY_WEEK_A = datetime(2026, 9, 14)  # ISO week 38
MONDAY_WEEK_B = datetime(2026, 9, 21)  # ISO week 39


class FakeFoodResponse:
    def __init__(self, html, delay=0):
        self._html = html
        self._delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def text(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._html


class FakeFoodSession:
    """Hands back the next HTML in sequence and records every call.get()."""

    def __init__(self, html_sequence, calls, delay=0):
        self._html_sequence = list(html_sequence)
        self._calls = calls
        self._delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    def get(self, url):
        self._calls.append(url)
        html = self._html_sequence[min(len(self._calls) - 1, len(self._html_sequence) - 1)]
        return FakeFoodResponse(html, delay=self._delay)


def _stub_fetch(monkeypatch, *html_sequence, delay=0):
    """Stub aiohttp.ClientSession for fetch_weekly_menu; returns the call log."""
    calls = []

    def factory():
        return FakeFoodSession(html_sequence, calls, delay=delay)

    monkeypatch.setattr(food_service.aiohttp, "ClientSession", factory)
    return calls


def _freeze_now(monkeypatch, when: datetime):
    monkeypatch.setattr(food_service.timezone, "now", lambda: when)


@pytest.fixture(autouse=True)
def reset_food_cache():
    """local_cache and _cached_week are module-level state shared across
    every call - without resetting them, one test's fetch would seed the
    next test's cache."""
    food_service.local_cache.clear()
    food_service._cached_week = None
    yield
    food_service.local_cache.clear()
    food_service._cached_week = None


@pytest.mark.asyncio
async def test_fetch_weekly_menu_stores_none_for_a_day_with_no_menu(monkeypatch):
    """Regression: an empty cell used to be left out of local_cache entirely,
    which made it indistinguishable from 'never fetched' and forced a
    re-scrape on every single request for that day."""
    _stub_fetch(monkeypatch, HTML_WITH_ONE_EMPTY_DAY)

    result = await food_service.fetch_weekly_menu()

    assert result is True
    assert food_service.local_cache[0] == "Mon breakfast"
    assert food_service.local_cache[1] == "Tue breakfast"
    # The empty Wednesday cell is stored as an explicit None, not omitted.
    assert 2 in food_service.local_cache
    assert food_service.local_cache[2] is None
    assert food_service.local_cache[3] == "Thu breakfast"
    assert food_service.local_cache[4] == "Fri breakfast"


@pytest.mark.asyncio
async def test_fetch_weekly_menu_returns_false_on_unrecognized_page(monkeypatch):
    _stub_fetch(monkeypatch, HTML_MISSING_STRUCTURE)

    result = await food_service.fetch_weekly_menu()

    assert result is False


@pytest.mark.asyncio
async def test_get_menu_for_day_fetches_only_once_per_week(monkeypatch):
    calls = _stub_fetch(monkeypatch, HTML_WITH_ONE_EMPTY_DAY)
    _freeze_now(monkeypatch, MONDAY_WEEK_A)

    for _ in range(3):
        await food_service.get_menu_for_day(0, food_service.Language.EN)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_menu_for_day_refetches_when_the_week_changes(monkeypatch):
    calls = _stub_fetch(monkeypatch, HTML_WITH_ONE_EMPTY_DAY, HTML_WITH_ONE_EMPTY_DAY)

    _freeze_now(monkeypatch, MONDAY_WEEK_A)
    await food_service.get_menu_for_day(0, food_service.Language.EN)

    _freeze_now(monkeypatch, MONDAY_WEEK_B)
    await food_service.get_menu_for_day(0, food_service.Language.EN)

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_day_with_no_published_menu_shows_no_data_without_refetching(
    monkeypatch,
):
    """The actual bug from production: a day whose cell never had a <pre>
    tag re-scraped the whole page on every request for it."""
    calls = _stub_fetch(monkeypatch, HTML_WITH_ONE_EMPTY_DAY)
    _freeze_now(monkeypatch, MONDAY_WEEK_A)

    first = await food_service.get_menu_for_day(2, food_service.Language.EN)
    second = await food_service.get_menu_for_day(2, food_service.Language.EN)

    assert "No data" in first
    assert "No data" in second
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_requests_at_the_start_of_a_week_share_one_fetch(monkeypatch):
    """Regression: production logs showed three independent full scrapes of
    kw.ac.kr within 30 seconds from concurrent requests racing an empty
    cache."""
    calls = _stub_fetch(monkeypatch, HTML_WITH_ONE_EMPTY_DAY, delay=0.02)
    _freeze_now(monkeypatch, MONDAY_WEEK_A)

    await asyncio.gather(
        *(
            food_service.get_menu_for_day(0, food_service.Language.EN)
            for _ in range(5)
        )
    )

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_failed_fetch_does_not_block_the_next_attempt(monkeypatch):
    """A parse failure must not pin _cached_week to the current week forever
    - otherwise a transient scrape failure would show 'No data' all week."""
    calls = _stub_fetch(monkeypatch, HTML_MISSING_STRUCTURE, HTML_WITH_ONE_EMPTY_DAY)
    _freeze_now(monkeypatch, MONDAY_WEEK_A)

    first = await food_service.get_menu_for_day(0, food_service.Language.EN)
    second = await food_service.get_menu_for_day(0, food_service.Language.EN)

    assert "No data" in first
    assert "Mon breakfast" in second
    assert len(calls) == 2
