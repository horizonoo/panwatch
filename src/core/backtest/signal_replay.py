"""历史信号回放 —— 在历史 K 线上逐日复现各策略入场规则，生成可回测信号。

为什么需要它:
  实盘 StrategySignalRun 只有最近几天的数据，holding period 还没走完，
  无法统计胜率。本模块在一年(可配)的真实 K 线上，对每一天判定「若当日产生
  入场信号」，从而得到几百条带完整后续走势的信号，供 Backtester 验证与优化。

设计:
  - 无未来函数: 第 i 日只用 bars[:i+1] 的信息判定信号，入场由 Backtester 在
    第 i+1 日开盘执行。
  - 每个策略一个纯函数 rule(closes, highs, lows, vols, i) -> bool，返回当日是否触发。
  - 止损/止盈由优化器以「相对入场价的百分比」注入(此处只产出信号日 + 策略)。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.backtest.data_adapter import PriceBar


@dataclass(frozen=True)
class ReplaySignal:
    """回放产出的一条信号(策略 + 日期，价格止损止盈待优化器注入)。"""
    symbol: str
    market: str
    signal_date: str
    strategy_code: str
    close: float  # 信号日收盘(用于设定止损止盈基准)


# ---------------------------------------------------------------------------
# 指标(滚动，作用于 closes[:i+1])
# ---------------------------------------------------------------------------

def _ma(values: list[float], i: int, period: int) -> float | None:
    if i + 1 < period:
        return None
    return sum(values[i - period + 1: i + 1]) / period


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    out = [values[0]]
    k = 2 / (period + 1)
    for v in values[1:]:
        out.append((v - out[-1]) * k + out[-1])
    return out


def _macd(closes: list[float], fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None
    ema_f = _ema_series(closes, fast)
    ema_s = _ema_series(closes, slow)
    dif = [f - s for f, s in zip(ema_f, ema_s)]
    dea = _ema_series(dif, signal)
    return dif, dea


def _rsi(closes: list[float], i: int, period: int = 6) -> float | None:
    if i + 1 < period + 1:
        return None
    gains = losses = 0.0
    for j in range(i - period + 1, i + 1):
        chg = closes[j] - closes[j - 1]
        if chg >= 0:
            gains += chg
        else:
            losses -= chg
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


# ---------------------------------------------------------------------------
# 策略入场规则: rule(bars, i) -> bool（第 i 日收盘后判定是否触发）
# ---------------------------------------------------------------------------

def _rule_trend_follow(closes, highs, lows, vols, i) -> bool:
    """趋势延续: 均线多头排列(MA5>MA10>MA20) 且收盘站上 MA5，近5日动量为正。"""
    ma5, ma10, ma20 = _ma(closes, i, 5), _ma(closes, i, 10), _ma(closes, i, 20)
    if None in (ma5, ma10, ma20):
        return False
    if not (ma5 > ma10 > ma20):
        return False
    if closes[i] < ma5:
        return False
    if i < 5 or closes[i] <= closes[i - 5]:
        return False
    return True


def _rule_macd_golden(closes, highs, lows, vols, i) -> bool:
    """MACD金叉: DIF 上穿 DEA(当日金叉)，且 DIF 在零轴附近或之上。"""
    res = _macd(closes[: i + 1])
    if not res:
        return False
    dif, dea = res
    if len(dif) < 2:
        return False
    # 当日金叉: 昨日 DIF<=DEA，今日 DIF>DEA
    if dif[-2] <= dea[-2] and dif[-1] > dea[-1] and dif[-1] > -0.05 * closes[i]:
        return True
    return False


def _rule_volume_breakout(closes, highs, lows, vols, i) -> bool:
    """放量突破: 成交量 >= 2× 近20日均量，且收盘创近20日新高。"""
    if i < 20:
        return False
    avg_vol = sum(vols[i - 20: i]) / 20
    if avg_vol <= 0 or vols[i] < 2 * avg_vol:
        return False
    prior_high = max(highs[i - 20: i])
    return closes[i] > prior_high


def _rule_pullback(closes, highs, lows, vols, i) -> bool:
    """回踩确认: MA20 向上(趋势)，价格回踩 MA10 附近(±2%)后当日收阳。"""
    ma10, ma20 = _ma(closes, i, 10), _ma(closes, i, 20)
    ma20_prev = _ma(closes, i - 3, 20) if i >= 3 else None
    if None in (ma10, ma20, ma20_prev):
        return False
    if ma20 <= ma20_prev:          # MA20 须上行
        return False
    near_ma10 = abs(closes[i] - ma10) / ma10 <= 0.02
    bullish = closes[i] > closes[i - 1]   # 当日收阳
    return near_ma10 and bullish


def _rule_rebound(closes, highs, lows, vols, i) -> bool:
    """超跌反弹: RSI(6) 昨日<30(超卖)，今日上穿30 且当日收阳。"""
    rsi_today = _rsi(closes, i, 6)
    rsi_prev = _rsi(closes, i - 1, 6) if i >= 1 else None
    if rsi_today is None or rsi_prev is None:
        return False
    return rsi_prev < 30 <= rsi_today and closes[i] > closes[i - 1]


STRATEGY_RULES = {
    "trend_follow": _rule_trend_follow,
    "macd_golden": _rule_macd_golden,
    "volume_breakout": _rule_volume_breakout,
    "pullback": _rule_pullback,
    "rebound": _rule_rebound,
}


def replay_signals(
    symbol: str,
    market: str,
    bars: list[PriceBar],
    strategies: list[str] | None = None,
    warmup: int = 40,
    min_gap_days: int = 3,
) -> list[ReplaySignal]:
    """对单只股票回放所有策略，产出信号列表。

    Args:
        warmup: 前 N 根 bar 仅用于指标预热，不产信号。
        min_gap_days: 同策略两次信号最小间隔(bar)，避免连续重复入场。
    """
    if len(bars) < warmup + 5:
        return []
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    vols = [b.volume for b in bars]
    rules = {k: v for k, v in STRATEGY_RULES.items()
             if not strategies or k in strategies}

    out: list[ReplaySignal] = []
    last_fire: dict[str, int] = {}
    # 最后 max_holding 根不产信号(留出持有期评估空间)
    for i in range(warmup, len(bars) - 1):
        for code, rule in rules.items():
            if i - last_fire.get(code, -10_000) < min_gap_days:
                continue
            try:
                if rule(closes, highs, lows, vols, i):
                    out.append(ReplaySignal(
                        symbol=symbol, market=market,
                        signal_date=bars[i].date, strategy_code=code,
                        close=closes[i],
                    ))
                    last_fire[code] = i
            except Exception:
                continue
    return out
