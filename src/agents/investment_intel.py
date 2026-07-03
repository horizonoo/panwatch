"""投资情报 Agent — 基于 AgentKey 多源汇总 + 置信度评分。"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.core.analysis_history import save_analysis
from src.core.suggestion_pool import save_suggestion

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "investment_intel.txt"

TAG_START = "<!--INTEL_JSON-->"
TAG_END = "<!--/INTEL_JSON-->"


def _extract_json(content: str) -> dict:
    start = content.find(TAG_START)
    end = content.find(TAG_END)
    if start == -1 or end == -1:
        return {}
    raw = content[start + len(TAG_START): end].strip()
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _strip_json(content: str) -> str:
    start = content.find(TAG_START)
    if start == -1:
        return content
    return content[:start].strip()


class InvestmentIntelAgent(BaseAgent):
    """投资情报 Agent — 多源信息聚合 + 置信度评分"""

    name = "investment_intel"
    display_name = "投资情报"
    description = "通过 AgentKey 聚合 Twitter/Reddit/Yahoo Finance 等多源数据，对每只自选股给出置信度评分"

    def _get_setting(self, context: AgentContext, key: str) -> str:
        try:
            from src.web.database import SessionLocal
            from src.web.models import AppSettings
            db = SessionLocal()
            try:
                row = db.query(AppSettings).filter(AppSettings.key == key).first()
                return (row.value or "") if row else ""
            finally:
                db.close()
        except Exception:
            return ""

    async def _fetch_stock_intel(self, symbol: str, name: str, market: str, client) -> dict:
        """用 AgentKey 并发采集单只股票的多源情报。"""
        import asyncio

        # 搜索关键词（中英文均考虑）
        en_query = symbol
        cn_query = name if name else symbol

        tasks = {
            "twitter_symbol": client.search_twitter(f"${symbol} stock", count=15),
            "twitter_cn": client.search_twitter(cn_query, count=10) if name and name != symbol else asyncio.sleep(0, result=[]),
            "reddit": client.search_reddit(f"{symbol} {name}", limit=10),
            "reddit_wsb": client.search_reddit(symbol, subreddit="wallstreetbets", limit=5),
            "yahoo_news": client.get_yahoo_news(symbol, count=8),
            "yahoo_quote": client.get_yahoo_quote(symbol),
            "web_search": client.web_search(f"{symbol} {cn_query} stock analysis latest news", count=5),
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data = {}
        for key, result in zip(tasks.keys(), results):
            data[key] = [] if isinstance(result, Exception) else (result or [])

        return data

    async def collect(self, context: AgentContext) -> dict:
        api_key = self._get_setting(context, "agentkey_api_key")
        proxy = self._get_setting(context, "http_proxy") or ""

        if not api_key:
            logger.warning("agentkey_api_key 未配置，investment_intel 无法运行")
            return {
                "error": "未配置 AgentKey API Key（在设置页填写 agentkey_api_key）",
                "stocks": [],
                "timestamp": datetime.now().isoformat(),
            }

        from src.collectors.agentkey_client import AgentKeyClient
        client = AgentKeyClient(api_key=api_key, proxy=proxy)

        stock_data = []
        for stock in context.watchlist[:10]:  # 每次最多处理 10 只，控制 API 消耗
            logger.info(f"[InvestmentIntel] 采集 {stock.symbol} {stock.name}")
            try:
                intel = await self._fetch_stock_intel(
                    symbol=stock.symbol,
                    name=stock.name,
                    market=stock.market.value if hasattr(stock.market, "value") else str(stock.market),
                    client=client,
                )
                stock_data.append({
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
                    "intel": intel,
                })
            except Exception as e:
                logger.error(f"[InvestmentIntel] {stock.symbol} 采集失败: {e}")

        return {
            "stocks": stock_data,
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        if data.get("error"):
            user_content = f"错误：{data['error']}"
            return system_prompt, user_content

        lines = [f"## 分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]

        stocks = data.get("stocks", [])
        for sd in stocks:
            symbol = sd["symbol"]
            name = sd["name"]
            intel = sd.get("intel", {})

            lines.append(f"---\n# {name}（{symbol}）[{sd['market']}]\n")

            # Twitter 推文
            tw_posts = (intel.get("twitter_symbol") or []) + (intel.get("twitter_cn") or [])
            if tw_posts:
                lines.append(f"## Twitter/X（{len(tw_posts)} 条）")
                for p in tw_posts[:8]:
                    text = (p.get("text") or "").strip()[:200]
                    if text:
                        lines.append(f"- @{p.get('author','')} ❤️{p.get('likes',0)}: {text}")

            # Reddit
            rd_posts = (intel.get("reddit") or []) + (intel.get("reddit_wsb") or [])
            if rd_posts:
                lines.append(f"\n## Reddit（{len(rd_posts)} 条）")
                for p in rd_posts[:6]:
                    title = p.get("title") or ""
                    if title:
                        lines.append(f"- [{p.get('subreddit','')}] ↑{p.get('score',0)} {title[:150]}")

            # Yahoo Finance 新闻
            news = intel.get("yahoo_news") or []
            if news:
                lines.append(f"\n## Yahoo Finance 新闻（{len(news)} 条）")
                for n in news[:6]:
                    lines.append(f"- [{n.get('publisher','')}] {n.get('title','')[:150]}")

            # Yahoo 报价
            quote = intel.get("yahoo_quote") or {}
            if quote:
                price = quote.get("regularMarketPrice") or quote.get("price") or ""
                change_pct = quote.get("regularMarketChangePercent") or 0
                if price:
                    lines.append(f"\n## 最新报价: {price} ({change_pct:+.2f}%)")

            # 网页搜索
            web = intel.get("web_search") or []
            if web:
                lines.append(f"\n## 网页搜索摘要")
                for w in web[:4]:
                    lines.append(f"- {w.get('title','')}: {w.get('snippet','')[:120]}")

            lines.append("")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        system_prompt, user_content = self.build_prompt(data, context)

        if data.get("error"):
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】配置错误",
                content=data["error"],
                raw_data=data,
            )

        content = await context.ai_client.chat(system_prompt, user_content)
        if context.model_label:
            idx = content.rfind(TAG_START)
            insert = f"\n\n---\nAI: {context.model_label}\n\n"
            content = (content[:idx].rstrip() + insert + content[idx:]) if idx >= 0 else content.rstrip() + insert

        intel_json = _extract_json(content)
        display_content = _strip_json(content)

        stocks = data.get("stocks", [])
        names = "、".join(f"{s['name']}({s['symbol']})" for s in stocks[:4])
        if len(stocks) > 4:
            names += f" 等{len(stocks)}只"
        title = f"【{self.display_name}】{names or '全局'}"

        result = AnalysisResult(
            agent_name=self.name,
            title=title,
            content=display_content,
            raw_data={**data, "intel_structured": intel_json},
        )

        # 写入建议池
        if intel_json and context.watchlist:
            stock_map = {s.symbol: s for s in context.watchlist}
            for sd in data.get("stocks", []):
                sym = sd["symbol"]
                stock = stock_map.get(sym)
                if not stock:
                    continue
                rec = intel_json.get("recommendation", "watch")
                rec_label = intel_json.get("recommendation_label", "关注观望")
                confidence = intel_json.get("overall_confidence", 0)
                save_suggestion(
                    stock_symbol=sym,
                    stock_name=sd["name"],
                    action=rec,
                    action_label=rec_label,
                    signal=f"情报置信度 {confidence}%",
                    reason=f"多源情报综合（置信度{confidence}%）",
                    agent_name=self.name,
                    agent_label=self.display_name,
                    expires_hours=8,
                    prompt_context=user_content[:2000],
                    ai_response=display_content,
                    stock_market=sd["market"],
                    meta={"confidence": confidence, "intel_json": intel_json},
                )

        save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "timestamp": data.get("timestamp"),
                "stock_count": len(stocks),
                "intel_structured": intel_json,
                "prompt_context": user_content[:2000],
            },
        )

        return result

    async def should_notify(self, result: AnalysisResult) -> bool:
        intel = result.raw_data.get("intel_structured") or {}
        confidence = intel.get("overall_confidence", 0)
        # 置信度 > 50 才通知，避免低质量数据打扰
        return confidence >= 50 or bool(result.raw_data.get("stocks"))
