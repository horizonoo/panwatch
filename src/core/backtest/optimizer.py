"""多轮回测优化器 —— 找出各市场(A股/港股/美股)最佳量化策略与出场参数。

流程:
  1. 股票池 = 自选股 ∪ 近期推荐股(EntryCandidate)，按市场分组。
  2. 每只股票拉 ~1 年真实 K 线(带缓存)，用 signal_replay 回放各策略入场信号。
  3. 对每个 (市场, 策略)，在出场参数网格(止损% × 止盈% × 持有天数)上跑 Backtester，
     用市场专属成本模型(A股/港股/美股费率不同)结算，统计胜率/收益/夏普等。
  4. 多轮迭代细化(coordinate refinement): 每轮在上一轮最优点邻域收窄网格再搜，
     逼近最优出场参数。
  5. 产出每个市场的最佳策略 + 最佳参数 + 完整绩效，并持久化为 JSON 工件。

「最佳」综合评分(可解释):
  score = 0.40·胜率 + 0.35·归一化收益 + 0.25·归一化夏普
  (三者各自裁剪到 [0,1]，平衡胜率、收益与稳定性，避免单一指标过拟合。)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

from src.core.backtest.cost_model import get_cost_model
from src.core.backtest.data_adapter import PriceBar, load_price_history
from src.core.backtest.engine import Backtester, Signal, fixed_cash_sizer
from src.core.backtest.signal_replay import replay_signals, STRATEGY_RULES

logger = logging.getLogger(__name__)

MARKETS = ("CN", "HK", "US")

# 每个市场的子池资金(与模拟盘 20w / 50-30-20 配置对齐，用于真实单笔金额)
MARKET_CASH_PER_TRADE = {"CN": 20_000.0, "HK": 15_000.0, "US": 10_000.0}
MARKET_LOT = {"CN": 100, "HK": 100, "US": 1}

# 实盘可操作性约束: 这里优化的是市场级出场参数，不是个股目标价。
# 参数过宽会让历史收益好看，但对真实交易指令帮助很小。
MIN_STOP_PCT = 0.02
MAX_STOP_PCT = 0.12
MIN_TARGET_PCT = 0.04
MAX_TARGET_PCT = 0.25
MAX_HOLDING_DAYS = 30

# 风险收益比下限: 止盈 ≥ MIN_REWARD_RISK × 止损。
# 防止优化器挑出「高胜率堆小赢、一次大亏吃光」的极端不对称参数。
MIN_REWARD_RISK = 1.5


@dataclass
class ParamResult:
    """一组出场参数的回测结果。"""
    stop_pct: float
    target_pct: float
    holding_days: int
    trades: int
    win_rate: float
    total_return: float
    annualized_return: float
    sharpe: float
    profit_factor: float
    max_drawdown: float
    avg_win: float
    avg_loss: float
    expectancy: float
    avg_holding_bars: float
    target_hit_rate: float
    stop_hit_rate: float
    expire_rate: float
    score: float = 0.0


@dataclass
class StrategyResult:
    """单个 (市场, 策略) 的最优结果(含样本内调参 + 样本外验证)。"""
    market: str
    strategy_code: str
    strategy_name: str
    best_is: ParamResult | None      # 样本内(用于调参)
    best_oos: ParamResult | None     # 样本外(最优参数在未见数据上的真实表现)
    grid_size: int
    rounds: int
    signal_count: int
    is_count: int
    oos_count: int


@dataclass
class OptimizationReport:
    created_at: str
    universe_size: int
    bars_loaded: int
    rounds: int
    per_market_best: dict           # market -> {strategy_code, params, metrics}
    all_results: list               # list[StrategyResult-ish dict]
    elapsed_sec: float
    notes: str = ""
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------

def _score(win_rate: float, total_return: float, profit_factor: float) -> float:
    """综合评分: 胜率40% + 收益35% + 盈亏比25%，各分量裁剪到 [0,1]。

    用盈亏比(总盈利/总亏损)而非夏普: 本回测净值按「笔」累积而非按「日」，
    对其做 252 年化会严重高估夏普，不可信; 盈亏比是稳健的逐笔风险收益度量。
    """
    r = max(0.0, min(1.0, total_return / 0.5))      # 50% 总收益 → 满分
    pf = 0.0 if profit_factor in (0.0, float("inf")) else max(0.0, min(1.0, profit_factor / 3.0))
    if profit_factor == float("inf"):
        pf = 1.0                                     # 全胜无亏损
    w = max(0.0, min(1.0, win_rate))
    return round(0.40 * w + 0.35 * r + 0.25 * pf, 4)


# ---------------------------------------------------------------------------
# 股票池 + K线加载
# ---------------------------------------------------------------------------

def build_universe(max_per_market: int = 40) -> dict[str, list[tuple[str, str]]]:
    """构建股票池: 自选股 ∪ 近期推荐。返回 {market: [(symbol, name), ...]}。"""
    from src.web.database import SessionLocal
    from src.web.models import Stock, EntryCandidate

    db = SessionLocal()
    try:
        uni: dict[str, dict[str, str]] = {m: {} for m in MARKETS}
        for s in db.query(Stock).all():
            mkt = (s.market or "CN").upper()
            if mkt in uni:
                uni[mkt][s.symbol] = s.name or s.symbol
        # 补充近期推荐候选股
        latest_snap = (db.query(EntryCandidate.snapshot_date)
                       .order_by(EntryCandidate.snapshot_date.desc()).first())
        if latest_snap:
            cands = (db.query(EntryCandidate)
                     .filter(EntryCandidate.snapshot_date == latest_snap[0]).all())
            for c in cands:
                mkt = (c.stock_market or "CN").upper()
                code = c.stock_symbol
                if mkt in uni and code and code not in uni[mkt]:
                    uni[mkt][code] = c.stock_name or code
        out: dict[str, list[tuple[str, str]]] = {}
        for m in MARKETS:
            items = list(uni[m].items())[:max_per_market]
            out[m] = items
        return out
    finally:
        db.close()


def load_bars_for_universe(
    universe: dict[str, list[tuple[str, str]]],
    days: int = 250,
    workers: int = 6,
    retries: int = 1,
) -> dict[tuple[str, str], list[PriceBar]]:
    """为股票池并发加载历史 K 线(带重试)。键 = (symbol, market)。

    并发加速 + 单股失败重试，降低代理/DNS 瞬时抖动导致整市场缺数据的概率。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tasks: list[tuple[str, str]] = []
    for market, items in universe.items():
        for symbol, _name in items:
            tasks.append((symbol, market))

    def _load_one(symbol: str, market: str):
        for attempt in range(retries + 1):
            try:
                bars = load_price_history(symbol, market, days=days)
                if bars and len(bars) >= 60:
                    return (symbol, market), bars
            except Exception as e:
                if attempt == retries:
                    logger.warning(f"[优化] 加载 {symbol} K线失败: {e}")
            time.sleep(0.3 * (attempt + 1))
        return (symbol, market), None

    bars_map: dict[tuple[str, str], list[PriceBar]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_load_one, s, m) for s, m in tasks]
        for fut in as_completed(futures):
            try:
                key, bars = fut.result()
                if bars:
                    bars_map[key] = bars
            except Exception:
                continue
    return bars_map


# ---------------------------------------------------------------------------
# 单组参数回测
# ---------------------------------------------------------------------------

def _backtest_params(
    signals: list[Signal],
    bars_by_symbol: dict,
    market: str,
    stop_pct: float,
    target_pct: float,
    holding_days: int,
) -> ParamResult:
    """给定出场参数，对该市场全部信号跑回测并汇总。"""
    # 用相对百分比注入止损/止盈
    concrete: list[Signal] = []
    for s in signals:
        # entry_price=None → Backtester 用次日开盘价；止损止盈基于信号日收盘
        base = s.entry_price or 0.0
        if base <= 0:
            continue
        concrete.append(Signal(
            symbol=s.symbol, market=s.market, signal_date=s.signal_date,
            entry_price=None,
            stop_loss=round(base * (1 - stop_pct), 4),
            target_price=round(base * (1 + target_pct), 4),
            holding_days=holding_days,
        ))

    bt = Backtester(
        cost_model=get_cost_model(market),
        initial_capital=1_000_000.0,
        sizer=fixed_cash_sizer(
            MARKET_CASH_PER_TRADE.get(market, 10_000.0),
            lot=MARKET_LOT.get(market, 1),
        ),
    )
    res = bt.run(concrete, bars_by_symbol)
    m = res.metrics
    trades = res.trades
    trade_count = len(trades)
    target_hits = sum(1 for t in trades if t.exit_reason == "target")
    stop_hits = sum(1 for t in trades if t.exit_reason == "stop_loss")
    expires = sum(1 for t in trades if t.exit_reason in ("expire", "eod"))
    avg_holding = (
        sum(float(t.holding_bars or 0) for t in trades) / trade_count
        if trade_count
        else 0.0
    )
    expectancy = (
        sum(float(t.pnl_pct or 0.0) for t in trades) / trade_count
        if trade_count
        else 0.0
    )
    pf = m["profit_factor"]
    if pf == float("inf"):
        pf = 99.0  # 全胜无亏损，裁剪为有限值便于 JSON 序列化与展示
    return ParamResult(
        stop_pct=stop_pct, target_pct=target_pct, holding_days=holding_days,
        trades=m["trades"], win_rate=m["win_rate"],
        total_return=m["total_return"], annualized_return=m["annualized_return"],
        sharpe=m["sharpe"], profit_factor=pf,
        max_drawdown=m["max_drawdown"], avg_win=m["avg_win"], avg_loss=m["avg_loss"],
        expectancy=expectancy,
        avg_holding_bars=avg_holding,
        target_hit_rate=(target_hits / trade_count) if trade_count else 0.0,
        stop_hit_rate=(stop_hits / trade_count) if trade_count else 0.0,
        expire_rate=(expires / trade_count) if trade_count else 0.0,
        score=_score(m["win_rate"], m["total_return"], pf),
    )


def _parameter_quality(params: ParamResult, metrics: ParamResult, in_sample: ParamResult | None = None) -> dict:
    reasons: list[str] = []
    level_score = 3
    if metrics.trades < 12:
        level_score -= 1
        reasons.append("样本外成交笔数偏少")
    if metrics.win_rate < 0.5:
        level_score -= 1
        reasons.append("样本外胜率低于 50%")
    if metrics.expectancy <= 0:
        level_score -= 1
        reasons.append("单笔期望不为正")
    if metrics.target_hit_rate < 0.15:
        level_score -= 1
        reasons.append("目标命中率偏低")
    if params.stop_pct > 0.10:
        reasons.append("止损幅度较宽，需降低仓位")
    if in_sample and in_sample.win_rate - metrics.win_rate > 0.15:
        level_score -= 1
        reasons.append("样本外表现明显弱于调参样本")

    level = "high" if level_score >= 3 else "medium" if level_score >= 1 else "low"
    return {
        "level": level,
        "label": {"high": "高", "medium": "中", "low": "低"}[level],
        "reasons": reasons[:4] or ["样本外表现与参数范围基本可接受"],
    }


def _make_playbook(
    market: str,
    strategy_name: str,
    params: ParamResult,
    metrics: ParamResult,
    in_sample: ParamResult | None = None,
) -> dict:
    stop_pct = float(params.stop_pct or 0.0)
    target_pct = float(params.target_pct or 0.0)
    holding_days = int(params.holding_days or 0)
    lot = MARKET_LOT.get(market, 1)
    return {
        "action": "buy",
        "action_label": "符合信号后买入",
        "entry_rule": f"{strategy_name} 触发后的下一交易日开盘买入；若开盘高于信号收盘价 2% 以上，放弃追高。",
        "price_formula": {
            "entry": "next_open",
            "stop_loss": f"入场价下方 {stop_pct:.1%}",
            "target_price": f"入场价上方 {target_pct:.1%}",
        },
        "risk_control": f"这是市场级参数模板；具体个股价格以每张机会卡的「交易计划」为准。{holding_days} 个交易日仍未达标则按收盘价退出。",
        "position_sizing": f"按固定金额下单，股数按 {lot} 股/份整数倍取整。",
        "holding_days": holding_days,
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        "parameter_quality": _parameter_quality(params, metrics, in_sample),
        "sample_quality": {
            "trades": int(metrics.trades or 0),
            "win_rate": float(metrics.win_rate or 0.0),
            "expectancy": float(metrics.expectancy or 0.0),
            "target_hit_rate": float(metrics.target_hit_rate or 0.0),
            "stop_hit_rate": float(metrics.stop_hit_rate or 0.0),
            "avg_holding_bars": float(metrics.avg_holding_bars or 0.0),
        },
    }


# ---------------------------------------------------------------------------
# 多轮优化
# ---------------------------------------------------------------------------

def _optimize_one(
    signals: list[Signal],
    bars_by_symbol: dict,
    market: str,
    rounds: int,
    min_trades: int,
) -> tuple[ParamResult | None, int]:
    """对单个 (市场, 策略) 多轮细化搜索最优出场参数。返回 (最优, 总评估次数)。"""
    if not signals:
        return None, 0

    # 初始网格(粗)
    stop_grid = [0.03, 0.05, 0.08, 0.10, 0.12]
    target_grid = [0.06, 0.10, 0.15, 0.20, 0.25]
    hold_grid = [5, 10, 15, 20, 30]

    best: ParamResult | None = None
    evals = 0
    for rnd in range(rounds):
        round_best: ParamResult | None = None
        for sp in stop_grid:
            for tp in target_grid:
                # 风险收益比下限: 跳过止盈过小的极端不对称组合
                if tp < MIN_REWARD_RISK * sp:
                    continue
                for hd in hold_grid:
                    pr = _backtest_params(signals, bars_by_symbol, market, sp, tp, hd)
                    evals += 1
                    if pr.trades < min_trades:
                        continue
                    if round_best is None or pr.score > round_best.score:
                        round_best = pr
        if round_best is None:
            break
        if best is None or round_best.score > best.score:
            best = round_best
        # 在最优点邻域收窄网格(coordinate refinement)
        sp0, tp0, hd0 = round_best.stop_pct, round_best.target_pct, round_best.holding_days
        stop_grid = sorted({
            min(MAX_STOP_PCT, max(MIN_STOP_PCT, sp0 * f))
            for f in (0.7, 0.85, 1.0, 1.15, 1.3)
        })
        target_grid = sorted({
            min(MAX_TARGET_PCT, max(MIN_TARGET_PCT, tp0 * f))
            for f in (0.7, 0.85, 1.0, 1.15, 1.3)
        })
        hold_grid = sorted({
            min(MAX_HOLDING_DAYS, max(3, int(round(hd0 * f))))
            for f in (0.6, 0.8, 1.0, 1.25, 1.5)
        })

    return best, evals


def run_optimization(
    rounds: int = 3,
    max_per_market: int = 40,
    history_days: int = 750,
    min_trades: int = 8,
    persist: bool = True,
) -> OptimizationReport:
    """主入口: 构建股票池 → 回放信号 → 多轮优化 → 产出每市场最佳策略。"""
    t0 = time.time()
    from src.core.strategy_catalog import DEFAULT_STRATEGIES
    name_map = {s.code: s.name for s in DEFAULT_STRATEGIES}

    logger.info(f"[优化] 构建股票池(每市场上限 {max_per_market})...")
    universe = build_universe(max_per_market=max_per_market)
    uni_size = sum(len(v) for v in universe.values())

    logger.info(f"[优化] 加载历史 K 线({history_days}日)...")
    bars_map = load_bars_for_universe(universe, days=history_days)
    logger.info(f"[优化] 已加载 {len(bars_map)} 只股票的 K 线")

    # 回放信号: {(market, strategy): [Signal, ...]}
    sig_groups: dict[tuple[str, str], list[Signal]] = {}
    bars_by_symbol: dict = {}
    for (symbol, market), bars in bars_map.items():
        bars_by_symbol[(symbol, market)] = bars
        replays = replay_signals(symbol, market, bars)
        for rs in replays:
            key = (market, rs.strategy_code)
            sig_groups.setdefault(key, []).append(Signal(
                symbol=rs.symbol, market=rs.market, signal_date=rs.signal_date,
                entry_price=rs.close,  # 基准价(信号日收盘)，用于设定止损止盈
            ))

    all_results: list[dict] = []
    per_market: dict[str, list[StrategyResult]] = {m: [] for m in MARKETS}

    for (market, strat), signals in sorted(sig_groups.items()):
        # 按日期排序后做时间序列切分: 前 65% 调参(IS)，后 35% 验证(OOS)
        signals_sorted = sorted(signals, key=lambda s: s.signal_date)
        split = int(len(signals_sorted) * 0.65)
        is_sigs = signals_sorted[:split]
        oos_sigs = signals_sorted[split:]

        # 样本内调参(OOS 样本可少些，门槛减半)
        best_is, evals = _optimize_one(is_sigs, bars_by_symbol, market,
                                       rounds=rounds, min_trades=min_trades)
        best_oos = None
        if best_is and oos_sigs:
            # 用样本内最优参数，在样本外未见数据上验证真实表现
            best_oos = _backtest_params(
                oos_sigs, bars_by_symbol, market,
                best_is.stop_pct, best_is.target_pct, best_is.holding_days,
            )

        sr = StrategyResult(
            market=market, strategy_code=strat,
            strategy_name=name_map.get(strat, strat),
            best_is=best_is, best_oos=best_oos, grid_size=evals, rounds=rounds,
            signal_count=len(signals), is_count=len(is_sigs), oos_count=len(oos_sigs),
        )
        per_market[market].append(sr)
        all_results.append({
            "market": market, "strategy_code": strat,
            "strategy_name": sr.strategy_name, "signal_count": len(signals),
            "is_count": len(is_sigs), "oos_count": len(oos_sigs), "evals": evals,
            "best_is": asdict(best_is) if best_is else None,
            "best_oos": asdict(best_oos) if best_oos else None,
        })
        if best_oos:
            logger.info(
                f"[优化] {market}/{strat}: IS{len(is_sigs)}/OOS{len(oos_sigs)} "
                f"样本外胜率{best_oos.win_rate:.0%} 收益{best_oos.total_return:+.1%} "
                f"盈亏比{best_oos.profit_factor:.2f} 评分{best_oos.score:.3f} "
                f"(止损{best_is.stop_pct:.0%}/止盈{best_is.target_pct:.0%}/持有{best_is.holding_days}日)"
            )

    # 每市场最佳策略(按样本外评分排名，要求样本外有足够成交)
    oos_min = max(5, min_trades // 2)
    per_market_best: dict = {}
    for m in MARKETS:
        ranked = [sr for sr in per_market[m]
                  if sr.best_oos and sr.best_is and sr.best_oos.trades >= oos_min]
        ranked.sort(key=lambda x: x.best_oos.score, reverse=True)
        if ranked:
            top = ranked[0]
            o, i = top.best_oos, top.best_is
            per_market_best[m] = {
                "strategy_code": top.strategy_code,
                "strategy_name": top.strategy_name,
                "signal_count": top.signal_count,
                "params": {
                    "stop_pct": i.stop_pct,
                    "target_pct": i.target_pct,
                    "holding_days": i.holding_days,
                },
                # 样本外(真实战绩) —— headline
                "metrics": {
                    "trades": o.trades,
                    "win_rate": o.win_rate,
                    "total_return": o.total_return,
                    "annualized_return": o.annualized_return,
                    "sharpe": o.sharpe,
                    "profit_factor": o.profit_factor,
                    "max_drawdown": o.max_drawdown,
                    "expectancy": o.expectancy,
                    "avg_holding_bars": o.avg_holding_bars,
                    "target_hit_rate": o.target_hit_rate,
                    "stop_hit_rate": o.stop_hit_rate,
                    "expire_rate": o.expire_rate,
                    "score": o.score,
                },
                "playbook": _make_playbook(m, top.strategy_name, i, o, i),
                # 样本内(调参时的表现，供对比过拟合)
                "in_sample": {
                    "trades": i.trades, "win_rate": i.win_rate,
                    "total_return": i.total_return, "score": i.score,
                },
                "ranking": [
                    {"strategy_code": sr.strategy_code, "strategy_name": sr.strategy_name,
                     "score": sr.best_oos.score, "win_rate": sr.best_oos.win_rate,
                     "total_return": sr.best_oos.total_return,
                     "is_win_rate": sr.best_is.win_rate,
                     "trades": sr.best_oos.trades}
                    for sr in ranked
                ],
            }
        else:
            per_market_best[m] = None

    report = OptimizationReport(
        created_at=datetime.now(timezone.utc).isoformat(),
        universe_size=uni_size,
        bars_loaded=len(bars_map),
        rounds=rounds,
        per_market_best=per_market_best,
        all_results=all_results,
        elapsed_sec=round(time.time() - t0, 1),
        notes=f"历史回放 {history_days} 日，每市场上限 {max_per_market} 只。"
              f"前65%调参/后35%样本外验证，展示为样本外真实战绩。",
    )
    if persist:
        _persist_report(report)
    logger.info(f"[优化] 完成，耗时 {report.elapsed_sec}s")
    return report


# ---------------------------------------------------------------------------
# 持久化(JSON 工件，无需迁移)
# ---------------------------------------------------------------------------

def _artifact_path() -> str:
    data_dir = os.environ.get("DATA_DIR", "./data")
    out_dir = os.path.join(data_dir, "backtest")
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, "optimization_latest.json")


def _persist_report(report: OptimizationReport) -> None:
    try:
        with open(_artifact_path(), "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[优化] 持久化失败: {e}")


def _sanitize(obj):
    """递归把 inf/-inf/nan 替换为安全值，保证 FastAPI 严格 JSON 序列化不报错。"""
    import math
    if isinstance(obj, float):
        if math.isinf(obj):
            return 99.0 if obj > 0 else -99.0
        if math.isnan(obj):
            return 0.0
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def load_latest_report() -> dict | None:
    try:
        path = _artifact_path()
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return _sanitize(json.load(f))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 最优参数读取(供模拟盘/信号生成调用) —— 按文件 mtime 缓存，新一轮优化后自动生效
# ---------------------------------------------------------------------------

_PARAMS_CACHE: dict = {"mtime": None, "by_market": {}}


def get_optimized_params(market: str) -> dict | None:
    """返回某市场的最优出场参数 + 最佳策略，无优化结果时返回 None。

    返回: {"strategy_code", "strategy_name", "stop_pct", "target_pct",
           "holding_days", "win_rate", "total_return"}
    按工件文件 mtime 缓存，无需重启即可在下一轮优化后自动刷新。
    """
    market = (market or "").upper()
    try:
        path = _artifact_path()
        if not os.path.exists(path):
            return None
        mtime = os.path.getmtime(path)
        if _PARAMS_CACHE["mtime"] != mtime:
            report = load_latest_report()
            by_market: dict = {}
            for m, best in (report or {}).get("per_market_best", {}).items():
                if not best:
                    continue
                p = best.get("params", {})
                mt = best.get("metrics", {})
                by_market[m] = {
                    "strategy_code": best.get("strategy_code"),
                    "strategy_name": best.get("strategy_name"),
                    "stop_pct": p.get("stop_pct"),
                    "target_pct": p.get("target_pct"),
                    "holding_days": p.get("holding_days"),
                    "win_rate": mt.get("win_rate"),
                    "total_return": mt.get("total_return"),
                }
            _PARAMS_CACHE["by_market"] = by_market
            _PARAMS_CACHE["mtime"] = mtime
        return _PARAMS_CACHE["by_market"].get(market)
    except Exception:
        return None
