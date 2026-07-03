"""社交媒体速递 Agent — Twitter/X KOL 推文、Reddit 热帖、雪球讨论"""

import logging
from datetime import datetime
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.social_collector import SocialPost, collect_all_social
from src.core.analysis_history import save_analysis

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "social_digest.txt"


class SocialDigestAgent(BaseAgent):
    """社交媒体速递 Agent"""

    name = "social_digest"
    display_name = "社交速递"
    description = "汇总 Twitter KOL 推文、Reddit 热帖、雪球讨论，提炼市场情绪与信号"

    async def collect(self, context: AgentContext) -> dict:
        """采集各平台社交数据"""
        settings = context.config.settings if hasattr(context.config, "settings") else {}

        # 从 AppSettings 获取 key/cookie（用户可在 UI 设置页配置）
        agentql_api_key = self._get_setting(context, "agentql_api_key")
        xueqiu_cookie = self._get_setting(context, "xueqiu_cookie")
        reddit_client_id = self._get_setting(context, "reddit_client_id")
        reddit_client_secret = self._get_setting(context, "reddit_client_secret")
        proxy = self._get_setting(context, "http_proxy") or ""

        results = await collect_all_social(
            agentql_api_key=agentql_api_key,
            xueqiu_cookie=xueqiu_cookie,
            proxy=proxy,
            reddit_client_id=reddit_client_id,
            reddit_client_secret=reddit_client_secret,
        )

        total = sum(len(v) for v in results.values())
        return {
            "platforms": results,
            "total_count": total,
            "timestamp": datetime.now().isoformat(),
            "watchlist": context.watchlist,
        }

    def _get_setting(self, context: AgentContext, key: str) -> str:
        """从 DB AppSettings 读取配置值"""
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

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        platforms: dict[str, list[SocialPost]] = data.get("platforms", {})

        lines = [f"## 采集时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]

        # 自选股列表（给 AI 关联参考）
        if context.watchlist:
            lines.append("## 关注的自选股")
            for s in context.watchlist:
                lines.append(f"- {s.name}（{s.symbol}）[{s.market}]")
            lines.append("")

        # Twitter/X
        twitter_posts: list[SocialPost] = platforms.get("twitter", [])
        if twitter_posts:
            lines.append(f"## Twitter/X KOL 推文（{len(twitter_posts)} 条）")
            for p in twitter_posts:
                lines.append(f"\n**{p.author}**")
                lines.append(f"> {p.text}")
                meta = []
                if p.score:
                    meta.append(f"❤️ {p.score}")
                if p.comments:
                    meta.append(f"💬 {p.comments}")
                if p.timestamp:
                    meta.append(p.timestamp)
                if meta:
                    lines.append(f"*{' · '.join(meta)}*")
        else:
            lines.append("## Twitter/X\n- 暂无数据（未配置 AGENTQL_API_KEY 或采集失败）")

        lines.append("")

        # Reddit
        reddit_posts: list[SocialPost] = platforms.get("reddit", [])
        if reddit_posts:
            # 按 subreddit 分组
            by_sub: dict[str, list[SocialPost]] = {}
            for p in reddit_posts:
                sub = p.extra.get("subreddit", "unknown")
                by_sub.setdefault(sub, []).append(p)

            lines.append(f"## Reddit 热帖（{len(reddit_posts)} 条）")
            for sub, posts in by_sub.items():
                sub_desc = posts[0].extra.get("subreddit_desc", "") if posts else ""
                lines.append(f"\n### r/{sub}（{sub_desc}）")
                for p in posts[:5]:
                    flair = f"[{p.extra.get('flair')}] " if p.extra.get("flair") else ""
                    lines.append(f"- {flair}**{p.text[:150]}**")
                    lines.append(f"  ↑{p.score} 💬{p.comments} · {p.timestamp}")
        else:
            lines.append("## Reddit\n- 暂无数据")

        lines.append("")

        # 雪球
        xueqiu_posts: list[SocialPost] = platforms.get("xueqiu", [])
        if xueqiu_posts:
            lines.append(f"## 雪球热帖（{len(xueqiu_posts)} 条）")
            for p in xueqiu_posts[:10]:
                lines.append(f"- **{p.author}**：{p.text[:200]}")
                lines.append(f"  ❤️{p.score} 💬{p.comments} · {p.timestamp}")
        else:
            lines.append("## 雪球\n- 暂无数据")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        total = data.get("total_count", 0)
        title = f"【{self.display_name}】{datetime.now().strftime('%m/%d %H:%M')} · {total} 条社交内容"

        result = AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data=data,
        )

        save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "timestamp": data.get("timestamp"),
                "total_count": total,
                "platform_counts": {k: len(v) for k, v in data.get("platforms", {}).items()},
                "prompt_context": user_content[:2000],
            },
        )

        return result

    async def should_notify(self, result: AnalysisResult) -> bool:
        return result.raw_data.get("total_count", 0) > 0
