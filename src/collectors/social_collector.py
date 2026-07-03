"""社交媒体数据采集器 — Twitter(AgentQL)、Reddit、雪球"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

_SOCIAL_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "social_watch.yaml"


def _load_social_config() -> dict:
    try:
        return yaml.safe_load(_SOCIAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning(f"加载 social_watch.yaml 失败: {e}")
        return {}


@dataclass
class SocialPost:
    source: str          # twitter / reddit / xueqiu
    platform_label: str  # 显示名称
    author: str
    text: str
    url: str = ""
    score: int = 0       # 赞/upvote 数
    comments: int = 0
    timestamp: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Twitter / X  — 使用 AgentQL + Playwright
# ---------------------------------------------------------------------------

async def get_twitter_posts(
    accounts: list[dict],
    tweet_count: int = 10,
    agentql_api_key: str = "",
) -> list[SocialPost]:
    """抓取指定 Twitter/X 账号的最新推文（使用 AgentQL）。"""
    if not accounts:
        return []

    try:
        import agentql
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("agentql 未安装，跳过 Twitter 采集（pip install agentql）")
        return []

    import os
    if agentql_api_key:
        os.environ.setdefault("AGENTQL_API_KEY", agentql_api_key)
    if not os.environ.get("AGENTQL_API_KEY"):
        logger.warning("AGENTQL_API_KEY 未配置，跳过 Twitter 采集")
        return []

    posts: list[SocialPost] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for account in accounts:
                handle = account.get("handle", "")
                name = account.get("name", handle)
                if not handle:
                    continue
                try:
                    page = await browser.new_page()
                    wrapped = agentql.wrap_async(page)
                    await wrapped.goto(
                        f"https://x.com/{handle}",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    await asyncio.sleep(3)

                    TWEET_QUERY = """
                    {
                        tweets[] {
                            text
                            timestamp
                            likes_count
                            retweets_count
                            reply_count
                        }
                    }
                    """
                    response = await wrapped.query_elements(TWEET_QUERY)
                    tweet_list = getattr(response, "tweets", None) or []

                    for tw in tweet_list[:tweet_count]:
                        text = getattr(tw, "text", "") or ""
                        if not text.strip():
                            continue
                        posts.append(SocialPost(
                            source="twitter",
                            platform_label="Twitter/X",
                            author=f"@{handle} ({name})",
                            text=text.strip()[:500],
                            url=f"https://x.com/{handle}",
                            score=_safe_int(getattr(tw, "likes_count", 0)),
                            comments=_safe_int(getattr(tw, "reply_count", 0)),
                            timestamp=str(getattr(tw, "timestamp", "") or ""),
                            extra={"retweets": _safe_int(getattr(tw, "retweets_count", 0))},
                        ))
                    await page.close()
                    logger.info(f"Twitter @{handle}: 获取 {len(tweet_list)} 条推文")
                except Exception as e:
                    logger.warning(f"Twitter @{handle} 采集失败: {e}")
        finally:
            await browser.close()

    return posts


def _safe_int(val: Any) -> int:
    try:
        return int(str(val).replace(",", "").replace("K", "000").replace("M", "000000")) if val else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Reddit  — 使用 Reddit JSON API（无需认证）
# ---------------------------------------------------------------------------

async def get_reddit_posts(
    subreddits: list[dict],
    post_limit: int = 15,
    proxy: str = "",
    reddit_client_id: str = "",
    reddit_client_secret: str = "",
) -> list[SocialPost]:
    """抓取 Reddit 热帖。

    优先使用 OAuth API（需提供 client_id/secret），降级使用 RSS 解析。
    """
    if not subreddits:
        return []

    # 尝试 OAuth 获取 token
    oauth_token = ""
    if reddit_client_id and reddit_client_secret:
        try:
            async with httpx.AsyncClient(proxy=proxy or None, timeout=10) as c:
                resp = await c.post(
                    "https://www.reddit.com/api/v1/access_token",
                    data={"grant_type": "client_credentials"},
                    auth=(reddit_client_id, reddit_client_secret),
                    headers={"User-Agent": "PanWatch/1.0"},
                )
                oauth_token = resp.json().get("access_token", "")
        except Exception as e:
            logger.warning(f"Reddit OAuth 失败: {e}")

    posts: list[SocialPost] = []

    for sub_cfg in subreddits:
        sub = sub_cfg.get("name", "")
        desc = sub_cfg.get("desc", sub)
        if not sub:
            continue

        fetched = await _fetch_reddit_sub(sub, desc, post_limit, proxy, oauth_token)
        posts.extend(fetched)

    return posts


async def _fetch_reddit_sub(
    sub: str, desc: str, limit: int, proxy: str, oauth_token: str
) -> list[SocialPost]:
    """尝试 OAuth API → RSS 两种方式抓取单个 subreddit。"""
    # --- 方式一：OAuth API ---
    if oauth_token:
        try:
            headers = {
                "Authorization": f"Bearer {oauth_token}",
                "User-Agent": "PanWatch/1.0",
            }
            async with httpx.AsyncClient(headers=headers, proxy=proxy or None, timeout=15) as c:
                resp = await c.get(f"https://oauth.reddit.com/r/{sub}/hot?limit={limit}")
                resp.raise_for_status()
                children = resp.json().get("data", {}).get("children", [])
                return _parse_reddit_children(children, sub, desc, limit)
        except Exception as e:
            logger.warning(f"Reddit OAuth API r/{sub} 失败，降级 RSS: {e}")

    # --- 方式二：RSS ---
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; PanWatch/1.0)"}
        async with httpx.AsyncClient(headers=headers, proxy=proxy or None, timeout=15) as c:
            resp = await c.get(f"https://www.reddit.com/r/{sub}/hot.rss?limit={limit}")
            resp.raise_for_status()
            return _parse_reddit_rss(resp.text, sub, desc, limit)
    except Exception as e:
        logger.warning(f"Reddit RSS r/{sub} 采集失败: {e}")

    return []


def _parse_reddit_children(children: list, sub: str, desc: str, limit: int) -> list[SocialPost]:
    posts = []
    for item in children[:limit]:
        d = item.get("data", {})
        title = d.get("title", "").strip()
        if not title:
            continue
        selftext = (d.get("selftext") or "").strip()[:300]
        posts.append(SocialPost(
            source="reddit",
            platform_label=f"Reddit r/{sub}",
            author=d.get("author", ""),
            text=title + (f"\n{selftext}" if selftext else ""),
            url=f"https://reddit.com{d.get('permalink', '')}",
            score=d.get("score", 0),
            comments=d.get("num_comments", 0),
            timestamp=_ts_to_str(d.get("created_utc", 0)),
            extra={"subreddit": sub, "subreddit_desc": desc, "flair": d.get("link_flair_text", "")},
        ))
    logger.info(f"Reddit r/{sub}: 获取 {len(posts)} 条")
    return posts


def _parse_reddit_rss(xml: str, sub: str, desc: str, limit: int) -> list[SocialPost]:
    """解析 Reddit Atom RSS feed"""
    import html
    import xml.etree.ElementTree as ET
    posts = []
    try:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml)
        entries = root.findall("atom:entry", ns)
        for entry in entries[:limit]:
            title_el = entry.find("atom:title", ns)
            content_el = entry.find("atom:content", ns)
            link_el = entry.find("atom:link", ns)
            author_el = entry.find(".//atom:name", ns)
            updated_el = entry.find("atom:updated", ns)

            title = html.unescape((title_el.text or "").strip()) if title_el is not None else ""
            if not title:
                continue
            raw_content = (content_el.text or "") if content_el is not None else ""
            content = _strip_html(html.unescape(raw_content))[:300]
            # 清理 Reddit 常见模板噪声
            content = _clean_reddit_text(content)
            url = link_el.get("href", "") if link_el is not None else ""
            author = html.unescape((author_el.text or "")) if author_el is not None else ""
            ts = (updated_el.text or "")[:16].replace("T", " ") if updated_el is not None else ""

            posts.append(SocialPost(
                source="reddit",
                platform_label=f"Reddit r/{sub}",
                author=author,
                text=title + (f"\n{content}" if content and len(content) > 20 else ""),
                url=url,
                score=0,
                comments=0,
                timestamp=ts,
                extra={"subreddit": sub, "subreddit_desc": desc},
            ))
    except Exception as e:
        logger.warning(f"Reddit RSS 解析失败 r/{sub}: {e}")
    logger.info(f"Reddit RSS r/{sub}: 获取 {len(posts)} 条")
    return posts


def _clean_reddit_text(text: str) -> str:
    import re
    # 移除 "submitted by /u/xxx [link] [comments]" 等模板文字
    text = re.sub(r"submitted by\s+/u/\S+", "", text)
    text = re.sub(r"\[link\]|\[comments\]|\[score hidden\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _ts_to_str(ts: float) -> str:
    if not ts:
        return ""
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 雪球  — 热帖 API
# ---------------------------------------------------------------------------

async def get_xueqiu_hot(
    limit: int = 20,
    cookie: str = "",
    proxy: str = "",
) -> list[SocialPost]:
    """抓取雪球热帖。"""
    posts: list[SocialPost] = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://xueqiu.com/",
    }
    if cookie:
        headers["Cookie"] = cookie

    endpoints = [
        f"https://xueqiu.com/v4/statuses/public_timeline_by_category.json?since_id=-1&max_id=-1&count={limit}&category=0",
        f"https://xueqiu.com/statuses/hot/listV2.json?since_id=-1&max_id=-1&size={limit}",
    ]

    if not cookie:
        logger.info("雪球未配置 xueqiu_cookie，跳过采集（在设置页填入雪球 Cookie 即可启用）")
        return []

    async with httpx.AsyncClient(headers=headers, proxy=proxy or None, timeout=20, follow_redirects=True) as client:
        for endpoint in endpoints:
            try:
                resp = await client.get(endpoint)
                if resp.status_code != 200:
                    continue
                data = resp.json()

                # 兼容两种 API 响应结构
                items = (
                    data.get("data", {}).get("statuses")
                    or data.get("data", {}).get("items")
                    or data.get("list")
                    or []
                )
                if not items:
                    continue

                for item in items[:limit]:
                    status = item.get("status") or item
                    text = _strip_html(status.get("text") or status.get("description") or "")
                    if not text:
                        continue
                    user = status.get("user") or {}
                    author = user.get("screen_name") or user.get("name") or "匿名"
                    sid = status.get("id") or ""
                    posts.append(SocialPost(
                        source="xueqiu",
                        platform_label="雪球",
                        author=author,
                        text=text[:400],
                        url=f"https://xueqiu.com/statuses/{sid}" if sid else "https://xueqiu.com",
                        score=status.get("like_count") or status.get("retweet_count") or 0,
                        comments=status.get("reply_count") or 0,
                        timestamp=_xq_ts(status.get("created_at")),
                    ))
                logger.info(f"雪球: 获取 {len(items)} 条热帖")
                break  # 成功则不继续尝试备用端点
            except Exception as e:
                logger.warning(f"雪球采集失败 ({endpoint}): {e}")

    return posts


def _strip_html(text: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _xq_ts(ts) -> str:
    if not ts:
        return ""
    import datetime
    try:
        ts_sec = int(ts) / 1000 if int(ts) > 1e10 else int(ts)
        return datetime.datetime.fromtimestamp(ts_sec).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts)


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

async def collect_all_social(
    agentql_api_key: str = "",
    xueqiu_cookie: str = "",
    proxy: str = "",
    reddit_client_id: str = "",
    reddit_client_secret: str = "",
) -> dict[str, list[SocialPost]]:
    """并发采集所有社交平台数据，返回 {platform: [posts]}。"""
    cfg = _load_social_config()

    tw_cfg = cfg.get("twitter") or {}
    rd_cfg = cfg.get("reddit") or {}
    xq_cfg = cfg.get("xueqiu") or {}

    tasks = {}

    if tw_cfg.get("enabled", True):
        tasks["twitter"] = get_twitter_posts(
            accounts=tw_cfg.get("accounts") or [],
            tweet_count=tw_cfg.get("tweet_count", 10),
            agentql_api_key=agentql_api_key,
        )

    if rd_cfg.get("enabled", True):
        tasks["reddit"] = get_reddit_posts(
            subreddits=rd_cfg.get("subreddits") or [],
            post_limit=rd_cfg.get("post_limit", 15),
            proxy=proxy,
            reddit_client_id=reddit_client_id,
            reddit_client_secret=reddit_client_secret,
        )

    if xq_cfg.get("enabled", True):
        tasks["xueqiu"] = get_xueqiu_hot(
            limit=xq_cfg.get("hot_limit", 20),
            cookie=xueqiu_cookie,
            proxy=proxy,
        )

    results: dict[str, list[SocialPost]] = {}
    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for key, result in zip(tasks.keys(), gathered):
        if isinstance(result, Exception):
            logger.error(f"社交采集 [{key}] 失败: {result}")
            results[key] = []
        else:
            results[key] = result or []

    total = sum(len(v) for v in results.values())
    logger.info(f"社交媒体采集完成: {total} 条内容 ({', '.join(f'{k}={len(v)}' for k, v in results.items())})")
    return results
