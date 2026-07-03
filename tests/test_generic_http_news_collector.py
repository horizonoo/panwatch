import asyncio

from src.collectors import news_collector
from datetime import datetime

from src.collectors.news_collector import GenericHttpNewsCollector, NewsCollector, NewsItem


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    calls = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return _FakeResponse(
            {
                "data": {
                    "items": [
                        {
                            "news_id": 1,
                            "headline": "AAPL gateway news",
                            "summary": "Apple summary",
                            "published_at": 1_725_000_000_000,
                            "link": "https://example.test/aapl",
                            "stocks": "AAPL, TSLA",
                            "level": "high",
                        },
                        {
                            "news_id": 2,
                            "headline": "Filtered out",
                            "summary": "No matching symbols",
                            "published_at": 1_725_000_000_000,
                            "stocks": "MSFT",
                        },
                    ]
                }
            }
        )


def test_generic_http_news_collector_maps_gateway_payload(monkeypatch):
    """通用 HTTP 新闻源按配置解析网关响应并透传请求配置。"""
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(news_collector.httpx, "AsyncClient", _FakeAsyncClient)

    collector = GenericHttpNewsCollector(
        "tradingview",
        {
            "url": "https://gateway.example.test/news",
            "method": "POST",
            "headers": {"Authorization": "Bearer test"},
            "params": {"limit": 20},
            "json_body": {"channel": "tv"},
            "items_path": "data.items",
            "field_map": {
                "id": "news_id",
                "title": "headline",
                "content": "summary",
                "time": "published_at",
                "url": "link",
                "symbols": "stocks",
                "importance": "level",
            },
        },
    )

    items = asyncio.run(collector.fetch_news(symbols=["AAPL"]))

    assert len(items) == 1
    assert items[0].source == "tradingview"
    assert items[0].external_id == "1"
    assert items[0].title == "AAPL gateway news"
    assert items[0].content == "Apple summary"
    assert items[0].symbols == ["AAPL", "TSLA"]
    assert items[0].importance == 3
    assert items[0].url == "https://example.test/aapl"

    call = _FakeAsyncClient.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://gateway.example.test/news"
    assert call["headers"] == {"Authorization": "Bearer test"}
    assert call["params"] == {"limit": 20, "symbols": "AAPL"}
    assert call["json"] == {"channel": "tv"}


class _StaticCollector:
    source = "gateway"

    def __init__(self, items):
        self.items = items

    async def fetch_news(self, symbols=None, since=None):
        return self.items


def test_news_collector_dedupes_missing_id_by_source_title_day():
    """聚合新闻 — 缺失 external_id 时按同源同日标题兜底去重。"""
    ts = datetime(2026, 7, 2, 9, 30)
    collector = NewsCollector(
        collectors=[
            _StaticCollector(
                [
                    NewsItem("gateway", "", "同一条 新闻", "", ts, importance=1),
                    NewsItem("gateway", "", "同一条新闻", "", ts, importance=3),
                    NewsItem("gateway", "", "另一条新闻", "", ts, importance=1),
                ]
            )
        ]
    )

    items = asyncio.run(collector.fetch_all(since_hours=24))

    assert [x.title for x in items] == ["同一条新闻", "另一条新闻"]
