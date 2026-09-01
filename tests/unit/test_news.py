import pytest

from app.services import news as news_service


@pytest.fixture(autouse=True)
def clean_news_cache():
    news_service.news_cache = {"foreigners": [], "all": []}
    news_service.last_fetch_time = {"foreigners": 0.0, "all": 0.0}
    yield


@pytest.mark.asyncio
async def test_refetch_replaces_instead_of_appending(monkeypatch):
    """Regression: fetch_news used to append, so the cache grew on every refresh
    and the sliced views kept serving the very first batch forever."""
    calls = {"n": 0}

    async def fake_fetch(news_type):
        calls["n"] += 1
        news_service.news_cache[news_type] = [
            {"title": f"item-{calls['n']}", "link": "l", "date": "d"}
        ]
        news_service.last_fetch_time[news_type] = 0.0  # stay stale

    monkeypatch.setattr(news_service, "fetch_news", fake_fetch)

    first = await news_service.get_news("all")
    second = await news_service.get_news("all")

    assert len(first) == 1 and len(second) == 1
    assert second[0]["title"] == "item-2"


@pytest.mark.asyncio
async def test_feeds_have_independent_freshness(monkeypatch):
    """Regression: a single global timestamp meant fetching one feed marked
    the other one fresh."""
    fetched = []

    async def fake_fetch(news_type):
        fetched.append(news_type)
        news_service.news_cache[news_type] = [
            {"title": news_type, "link": "l", "date": "d"}
        ]
        news_service.last_fetch_time[news_type] = __import__("time").time()

    monkeypatch.setattr(news_service, "fetch_news", fake_fetch)

    await news_service.get_news("all")
    await news_service.get_news("foreigners")

    assert fetched == ["all", "foreigners"]


@pytest.mark.asyncio
async def test_cached_feed_is_not_refetched(monkeypatch):
    import time

    calls = {"n": 0}

    async def fake_fetch(news_type):
        calls["n"] += 1
        news_service.news_cache[news_type] = [{"title": "x", "link": "l", "date": "d"}]
        news_service.last_fetch_time[news_type] = time.time()

    monkeypatch.setattr(news_service, "fetch_news", fake_fetch)

    await news_service.get_news("all")
    await news_service.get_news("all")

    assert calls["n"] == 1
