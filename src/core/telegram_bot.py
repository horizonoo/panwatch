"""Telegram Bot — 长轮询模式，支持简单指令查询盘面信息。

支持指令:
  /行情 <代码>   — 实时报价
  /分析 <代码>   — 触发 AI 深度分析（TradingAgents）
  /持仓          — 模拟盘当前持仓
  /信号          — 当前活跃买入信号
  /帮助          — 指令列表

Bot token 从 AppSettings(key='telegram_bot_token') 读取。
安全: 可在 AppSettings(key='telegram_bot_allowed_chat_ids') 设置白名单
     (逗号分隔的 chat_id)，留空则允许所有人（适合个人使用）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, token: str, proxy: str | None = None):
        self.token = token
        self.proxy = proxy
        self._offset = 0
        self._running = False
        self._allowed_ids: set[int] = set()

    # ------------------------------------------------------------------ API

    async def _call(self, method: str, **params) -> dict:
        url = TELEGRAM_API.format(token=self.token, method=method)
        async with httpx.AsyncClient(proxy=self.proxy, timeout=35) as client:
            resp = await client.post(url, json=params)
            resp.raise_for_status()
            return resp.json()

    async def send(self, chat_id: int, text: str, parse_mode: str = "HTML") -> None:
        try:
            await self._call("sendMessage", chat_id=chat_id,
                             text=text, parse_mode=parse_mode)
        except Exception as e:
            logger.warning(f"[TGBot] 发送失败 chat={chat_id}: {e}")

    # ------------------------------------------------------------------ 指令处理

    async def _cmd_help(self, chat_id: int, _args: str) -> None:
        await self.send(chat_id, (
            "📋 <b>PanWatch 指令列表</b>\n\n"
            "/行情 &lt;代码&gt; — 实时报价（如 /行情 NVDA）\n"
            "/分析 &lt;代码&gt; — AI 深度分析（约 3-5 分钟）\n"
            "/持仓 — 模拟盘当前持仓\n"
            "/信号 — 当前活跃买入信号\n"
            "/帮助 — 显示此帮助\n\n"
            "代码格式：A股 600519，港股 00700，美股 NVDA"
        ))

    async def _cmd_quote(self, chat_id: int, args: str) -> None:
        symbol = args.strip().upper()
        if not symbol:
            await self.send(chat_id, "❌ 请输入股票代码，如：/行情 NVDA")
            return
        await self.send(chat_id, f"🔍 正在查询 {symbol}...")
        try:
            from src.core.providers import ProviderRequest, get_quote_orchestrator
            from src.models.market import MarketCode
            market = _guess_market(symbol)
            orch = get_quote_orchestrator()
            req = ProviderRequest(symbols=(symbol,), market=market)
            resp = await asyncio.to_thread(orch.fetch_sync, req)
            q = (resp.quotes or {}).get(symbol) or {}
            if not q:
                await self.send(chat_id, f"❌ 未找到 {symbol} 的行情数据")
                return
            price = q.get("current_price", q.get("price", "—"))
            chg = q.get("change_pct", q.get("pct_chg", "—"))
            name = q.get("name", symbol)
            chg_str = f"{float(chg):+.2f}%" if chg not in ("—", None) else "—"
            await self.send(chat_id, (
                f"📈 <b>{name}（{symbol}）</b>\n"
                f"现价：<b>{price}</b>\n"
                f"涨跌：{chg_str}"
            ))
        except Exception as e:
            logger.exception(f"[TGBot] 行情查询失败 {symbol}: {e}")
            await self.send(chat_id, f"❌ 查询失败：{e}")

    async def _cmd_analysis(self, chat_id: int, args: str) -> None:
        symbol = args.strip().upper()
        if not symbol:
            await self.send(chat_id, "❌ 请输入股票代码，如：/分析 NVDA")
            return
        await self.send(chat_id, f"🤖 开始对 <b>{symbol}</b> 进行 AI 深度分析，约需 3-5 分钟，完成后会发送结果...")
        asyncio.create_task(self._run_analysis(chat_id, symbol))

    async def _run_analysis(self, chat_id: int, symbol: str) -> None:
        try:
            from src.web.database import SessionLocal
            from src.web.models import Stock
            db = SessionLocal()
            stock = db.query(Stock).filter(Stock.symbol == symbol).first()
            db.close()
            if not stock:
                await self.send(chat_id, f"❌ {symbol} 不在自选股列表中，请先添加到自选股再分析")
                return

            import server as srv
            context = srv.build_context("tradingagents", stock_agent_id=stock.id)
            agent = context.ai_client  # 只是验证 context 可用
            from src.agents.tradingagents.agent import TradingAgentsAgent
            cfg_row = _get_agent_config("tradingagents")
            kwargs = cfg_row or {}
            ta = TradingAgentsAgent(**{k: v for k, v in kwargs.items()
                                       if k != "auto_trigger"})
            result = await asyncio.to_thread(ta.analyze_stock, stock, context)
            summary = (result or {}).get("summary", "分析完成，请查看应用内报告")
            await self.send(chat_id, f"✅ <b>{symbol} 分析完成</b>\n\n{summary[:800]}")
        except Exception as e:
            logger.exception(f"[TGBot] 分析失败 {symbol}: {e}")
            await self.send(chat_id, f"❌ 分析失败：{e}")

    async def _cmd_positions(self, chat_id: int, _args: str) -> None:
        try:
            from src.web.database import SessionLocal
            from src.web.models import PaperTradingPosition, PaperTradingAccount
            db = SessionLocal()
            account = db.query(PaperTradingAccount).first()
            positions = db.query(PaperTradingPosition).filter(
                PaperTradingPosition.status == "open"
            ).order_by(PaperTradingPosition.opened_at.desc()).all()
            db.close()

            if not positions:
                capital = account.current_capital if account else 0
                await self.send(chat_id, f"📭 当前无持仓\n💰 可用资金：¥{capital:,.0f}")
                return

            lines = ["📊 <b>模拟盘持仓</b>\n"]
            for p in positions[:10]:
                pnl_str = f"{p.pnl_pct:+.1f}%" if p.pnl_pct else "—"
                lines.append(f"• <b>{p.stock_name or p.stock_symbol}</b>（{p.stock_symbol}）"
                              f"  {pnl_str}  {p.quantity}股 @{p.entry_price:.2f}")
            if account:
                lines.append(f"\n💰 账户资金：¥{account.current_capital:,.0f}")
            await self.send(chat_id, "\n".join(lines))
        except Exception as e:
            await self.send(chat_id, f"❌ 查询失败：{e}")

    async def _cmd_signals(self, chat_id: int, _args: str) -> None:
        try:
            from src.web.database import SessionLocal
            from src.web.models import StrategySignalRun
            db = SessionLocal()
            signals = (db.query(StrategySignalRun)
                       .filter(StrategySignalRun.status == "active",
                               StrategySignalRun.action.in_(["buy", "add"]))
                       .order_by(StrategySignalRun.rank_score.desc())
                       .limit(10).all())
            db.close()
            if not signals:
                await self.send(chat_id, "📭 当前无活跃买入信号")
                return
            lines = [f"🎯 <b>活跃买入信号（{len(signals)} 条）</b>\n"]
            for s in signals:
                lines.append(f"• <b>{s.stock_name or s.stock_symbol}</b>（{s.stock_symbol}）"
                              f"  评分:{s.rank_score:.0f}"
                              f"  入场:{s.entry_low:.2f}-{s.entry_high:.2f}")
            await self.send(chat_id, "\n".join(lines))
        except Exception as e:
            await self.send(chat_id, f"❌ 查询失败：{e}")

    # ------------------------------------------------------------------ 路由

    COMMANDS: dict[str, Any] = {}  # 初始化后填充

    async def handle_message(self, msg: dict) -> None:
        chat_id = msg.get("chat", {}).get("id")
        text: str = msg.get("text", "") or ""
        if not chat_id or not text.startswith("/"):
            return

        # 白名单检查
        if self._allowed_ids and chat_id not in self._allowed_ids:
            await self.send(chat_id, "⛔ 未授权的用户")
            return

        parts = text.split(None, 1)
        cmd = parts[0].lstrip("/").split("@")[0]  # 去掉 @BotName 后缀
        args = parts[1] if len(parts) > 1 else ""

        handler = self.COMMANDS.get(cmd)
        if handler:
            await handler(self, chat_id, args)
        else:
            await self.send(chat_id, "❓ 未知指令，发送 /帮助 查看可用指令")

    # ------------------------------------------------------------------ 轮询

    async def poll_forever(self) -> None:
        self._running = True
        logger.info("[TGBot] 开始长轮询...")
        while self._running:
            try:
                data = await self._call(
                    "getUpdates",
                    offset=self._offset,
                    timeout=25,
                    allowed_updates=["message"],
                )
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    msg = update.get("message") or update.get("channel_post")
                    if msg:
                        asyncio.create_task(self.handle_message(msg))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[TGBot] 轮询异常: {e}")
                await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False


# 注册指令（中英文别名）
TelegramBot.COMMANDS = {
    "帮助": TelegramBot._cmd_help,
    "help": TelegramBot._cmd_help,
    "start": TelegramBot._cmd_help,
    "行情": TelegramBot._cmd_quote,
    "quote": TelegramBot._cmd_quote,
    "q": TelegramBot._cmd_quote,
    "分析": TelegramBot._cmd_analysis,
    "analysis": TelegramBot._cmd_analysis,
    "持仓": TelegramBot._cmd_positions,
    "portfolio": TelegramBot._cmd_positions,
    "信号": TelegramBot._cmd_signals,
    "signals": TelegramBot._cmd_signals,
}


# ------------------------------------------------------------------ 工具函数

def _guess_market(symbol: str) -> str:
    s = symbol.upper()
    if s.isdigit():
        return "HK" if len(s) == 5 else "CN"
    if any(c.isalpha() for c in s):
        return "US"
    return "CN"


def _get_agent_config(name: str) -> dict:
    try:
        from src.web.database import SessionLocal
        from src.web.models import AgentConfig
        db = SessionLocal()
        row = db.query(AgentConfig).filter(AgentConfig.name == name).first()
        db.close()
        return row.config or {} if row else {}
    except Exception:
        return {}


def _get_app_setting(key: str) -> str:
    try:
        from src.web.database import SessionLocal
        from src.web.models import AppSettings
        db = SessionLocal()
        row = db.query(AppSettings).filter(AppSettings.key == key).first()
        db.close()
        return row.value if row else ""
    except Exception:
        return ""


def build_bot() -> TelegramBot | None:
    """从 AppSettings 读取配置并构建 Bot 实例，无 token 则返回 None。"""
    token = _get_app_setting("telegram_bot_token").strip()
    if not token:
        return None
    from src.config import Settings
    proxy = _get_app_setting("http_proxy") or Settings().http_proxy or None
    bot = TelegramBot(token=token, proxy=proxy)
    allowed_raw = _get_app_setting("telegram_bot_allowed_chat_ids").strip()
    if allowed_raw:
        bot._allowed_ids = {int(x.strip()) for x in allowed_raw.split(",") if x.strip().isdigit()}
    return bot
