"""AgentKey API 客户端 — 统一多源数据采集（Twitter、Reddit、Yahoo Finance 等）。

AgentKey 文档: https://docs.agentkey.app
使用 execute_tool(Provider/Operation, params) 模式调用各数据源。
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AGENTKEY_BASE_URL = "https://api.agentkey.app/v1"


class AgentKeyClient:
    """AgentKey HTTP API 封装。"""

    def __init__(self, api_key: str, proxy: str = "", timeout: int = 30):
        self.api_key = api_key
        self.proxy = proxy or None
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def execute(self, tool: str, params: dict) -> dict:
        """调用 AgentKey execute_tool。

        Args:
            tool: Provider/Operation 格式，如 'Twitter/searchTweets'
            params: 工具参数字典
        Returns:
            工具返回的 JSON 数据
        """
        async with httpx.AsyncClient(
            proxy=self.proxy,
            timeout=self.timeout,
        ) as client:
            resp = await client.post(
                f"{AGENTKEY_BASE_URL}/execute",
                headers=self._headers(),
                json={"name": tool, "params": params},
            )
            resp.raise_for_status()
            return resp.json()

    async def find_tools(self, intent: str, limit: int = 5) -> list[dict]:
        """语义搜索可用工具（用于调试/探索）。"""
        async with httpx.AsyncClient(proxy=self.proxy, timeout=self.timeout) as client:
            resp = await client.post(
                f"{AGENTKEY_BASE_URL}/find_tools",
                headers=self._headers(),
                json={"intent": intent, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json().get("tools", [])

    async def account_balance(self) -> dict:
        """查询账户余额（免费调用）。"""
        async with httpx.AsyncClient(proxy=self.proxy, timeout=self.timeout) as client:
            resp = await client.post(
                f"{AGENTKEY_BASE_URL}/account",
                headers=self._headers(),
                json={},
            )
            resp.raise_for_status()
            return resp.json()

    # -----------------------------------------------------------------------
    # 股票投资信息采集快捷方法
    # -----------------------------------------------------------------------

    async def search_twitter(self, query: str, count: int = 20) -> list[dict]:
        """搜索 Twitter/X 相关推文。"""
        try:
            result = await self.execute("Twitter/searchTweets", {
                "query": query,
                "count": count,
                "result_type": "recent",
            })
            tweets = result.get("data") or result.get("statuses") or result.get("tweets") or []
            return [self._normalize_tweet(t) for t in tweets]
        except Exception as e:
            logger.warning(f"AgentKey Twitter 搜索失败 [{query}]: {e}")
            return []

    async def get_twitter_user_timeline(self, username: str, count: int = 10) -> list[dict]:
        """获取指定用户的最新推文。"""
        try:
            result = await self.execute("Twitter/getUserTimeline", {
                "username": username,
                "count": count,
            })
            tweets = result.get("data") or result.get("tweets") or []
            return [self._normalize_tweet(t) for t in tweets]
        except Exception as e:
            logger.warning(f"AgentKey Twitter 用户时间线失败 [{username}]: {e}")
            return []

    async def search_reddit(self, query: str, subreddit: str = "", limit: int = 10) -> list[dict]:
        """搜索 Reddit 帖子。"""
        params: dict = {"query": query, "limit": limit, "sort": "hot"}
        if subreddit:
            params["subreddit"] = subreddit
        try:
            result = await self.execute("Reddit/searchPosts", params)
            posts = result.get("data", {}).get("children", []) or result.get("posts", [])
            return [self._normalize_reddit(p) for p in posts]
        except Exception as e:
            logger.warning(f"AgentKey Reddit 搜索失败 [{query}]: {e}")
            return []

    async def get_yahoo_news(self, symbol: str, count: int = 10) -> list[dict]:
        """获取 Yahoo Finance 股票新闻。"""
        try:
            result = await self.execute("YahooFinance/getNews", {
                "ticker": symbol,
                "limit": count,
            })
            news = result.get("news") or result.get("items") or []
            return [self._normalize_news(n) for n in news]
        except Exception as e:
            logger.warning(f"AgentKey Yahoo Finance 新闻失败 [{symbol}]: {e}")
            return []

    async def get_yahoo_quote(self, symbol: str) -> dict:
        """获取 Yahoo Finance 股票报价。"""
        try:
            result = await self.execute("YahooFinance/getQuote", {"ticker": symbol})
            return result.get("quoteResponse", {}).get("result", [{}])[0] if result else {}
        except Exception as e:
            logger.warning(f"AgentKey Yahoo Finance 报价失败 [{symbol}]: {e}")
            return {}

    async def web_search(self, query: str, count: int = 5) -> list[dict]:
        """通用网页搜索（Brave/Tavily）。"""
        try:
            result = await self.execute("Brave/search", {"q": query, "count": count})
            results = result.get("web", {}).get("results", []) or result.get("results", [])
            return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")} for r in results]
        except Exception as e:
            try:
                result = await self.execute("Tavily/search", {"query": query, "max_results": count})
                results = result.get("results", [])
                return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")} for r in results]
            except Exception as e2:
                logger.warning(f"AgentKey 网页搜索失败 [{query}]: {e2}")
                return []

    # -----------------------------------------------------------------------
    # 归一化方法（统一各 provider 的响应格式）
    # -----------------------------------------------------------------------

    @staticmethod
    def _normalize_tweet(t: dict) -> dict:
        return {
            "source": "twitter",
            "author": t.get("user", {}).get("screen_name") or t.get("author_id") or "",
            "text": t.get("text") or t.get("full_text") or "",
            "likes": t.get("favorite_count") or t.get("public_metrics", {}).get("like_count") or 0,
            "retweets": t.get("retweet_count") or t.get("public_metrics", {}).get("retweet_count") or 0,
            "created_at": t.get("created_at") or "",
            "url": f"https://x.com/i/status/{t.get('id_str') or t.get('id') or ''}",
        }

    @staticmethod
    def _normalize_reddit(p: dict) -> dict:
        d = p.get("data") or p
        return {
            "source": "reddit",
            "subreddit": d.get("subreddit") or "",
            "author": d.get("author") or "",
            "title": d.get("title") or "",
            "text": d.get("selftext") or "",
            "score": d.get("score") or 0,
            "comments": d.get("num_comments") or 0,
            "url": f"https://reddit.com{d.get('permalink') or ''}",
        }

    @staticmethod
    def _normalize_news(n: dict) -> dict:
        return {
            "source": "yahoo_finance",
            "title": n.get("title") or "",
            "url": n.get("link") or n.get("url") or "",
            "publisher": n.get("publisher") or "",
            "published_at": n.get("providerPublishTime") or n.get("publishedAt") or "",
            "summary": n.get("summary") or "",
        }
