"""推荐相关 API（入场候选榜）。"""

from datetime import datetime, timezone
import logging
import threading

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from src.core.entry_candidates import (
    evaluate_entry_candidate_outcomes,
    get_entry_candidate_stats,
    list_entry_candidates,
    refresh_entry_candidates,
    save_entry_candidate_feedback,
)
from src.core.strategy_catalog import list_strategy_catalog
from src.core.strategy_engine import (
    evaluate_strategy_outcomes,
    get_accuracy_trend,
    get_confidence_calibration,
    get_stock_signal_history,
    get_strategy_factor_snapshot,
    get_strategy_stats,
    list_market_regime_snapshots,
    list_portfolio_risk_snapshots,
    list_strategy_signals,
    list_strategy_weight_history,
    rebalance_strategy_weights,
    refresh_strategy_signals,
)
from src.core.factor_eval import evaluate_factor_ic
from src.core.signal_explain import enrich_signal
from src.web.database import SessionLocal
from src.web.models import (
    PaperTradingPosition,
    PaperTradingTrade,
    StrategyRuleInsight,
    StrategyOutcome,
    StrategySignalRun,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_refresh_state_lock = threading.Lock()
_refresh_state = {
    "running": False,
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "last_snapshot_date": "",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _latest_strategy_snapshot() -> str:
    db = SessionLocal()
    try:
        row = (
            db.query(StrategySignalRun.snapshot_date)
            .order_by(StrategySignalRun.snapshot_date.desc())
            .first()
        )
        return row[0] if row else ""
    finally:
        db.close()


def _set_refresh_state(**kwargs):
    with _refresh_state_lock:
        _refresh_state.update(kwargs)


def _get_refresh_state() -> dict:
    with _refresh_state_lock:
        return dict(_refresh_state)


def _refresh_worker(
    *,
    snapshot_date: str,
    rebuild_candidates: bool,
    max_inputs: int,
    market_scan_limit: int,
    max_kline_symbols: int,
    limit_candidates: int,
):
    _set_refresh_state(
        running=True,
        started_at=_now_iso(),
        finished_at="",
        last_error="",
    )
    try:
        result = refresh_strategy_signals(
            snapshot_date=snapshot_date,
            rebuild_candidates=rebuild_candidates,
            max_inputs=max_inputs,
            market_scan_limit=market_scan_limit,
            max_kline_symbols=max_kline_symbols,
            limit_candidates=limit_candidates,
        )
        _set_refresh_state(
            running=False,
            finished_at=_now_iso(),
            last_error="",
            last_snapshot_date=(result.get("snapshot_date") or ""),
        )
    except Exception as e:
        logger.exception("后台刷新策略信号失败: %s", e)
        _set_refresh_state(
            running=False,
            finished_at=_now_iso(),
            last_error=str(e),
        )


def _start_refresh_job(**kwargs) -> tuple[bool, dict]:
    with _refresh_state_lock:
        if _refresh_state.get("running"):
            return False, dict(_refresh_state)
        _refresh_state.update(
            running=True,
            started_at=_now_iso(),
            finished_at="",
            last_error="",
        )
    thread = threading.Thread(
        target=_refresh_worker,
        kwargs=kwargs,
        daemon=True,
        name="strategy-signals-refresh",
    )
    thread.start()
    return True, _get_refresh_state()


class CandidateFeedbackIn(BaseModel):
    snapshot_date: str = ""
    stock_symbol: str
    stock_market: str = "CN"
    useful: bool = True
    candidate_source: str = "watchlist"
    strategy_tags: list[str] = Field(default_factory=list)
    reason: str = ""


@router.get("/entry-candidates")
def get_entry_candidates(
    market: str = Query("", description="市场代码: CN/HK/US"),
    status: str = Query("active", description="状态: active/inactive/all"),
    min_score: float = Query(0, ge=0, le=100),
    limit: int = Query(20, ge=1, le=500),
    refresh: bool = Query(False, description="是否先刷新候选再返回"),
    snapshot_date: str = Query("", description="快照日期 YYYY-MM-DD，默认最新"),
    source: str = Query("", description="来源: market_scan/watchlist/mixed/all"),
    holding: str = Query("", description="持仓过滤: held/unheld/all"),
    strategy: str = Query("", description="策略标签过滤"),
):
    if refresh:
        refresh_entry_candidates()
    return list_entry_candidates(
        market=market,
        status=status,
        min_score=min_score,
        limit=limit,
        snapshot_date=snapshot_date,
        source=source,
        holding=holding,
        strategy=strategy,
    )


@router.post("/entry-candidates/refresh")
def refresh_candidates(
    max_inputs: int = Query(300, ge=10, le=1000),
    market_scan_limit: int = Query(60, ge=20, le=300),
):
    cand = refresh_entry_candidates(
        max_inputs=max_inputs,
        market_scan_limit=market_scan_limit,
    )
    # 同步刷新策略信号层，保持前端机会页一致。
    refresh_strategy_signals(
        snapshot_date=cand.get("snapshot_date", ""),
        rebuild_candidates=False,
    )
    return cand


@router.post("/entry-candidates/feedback")
def submit_candidate_feedback(payload: CandidateFeedbackIn):
    ok = save_entry_candidate_feedback(
        snapshot_date=payload.snapshot_date,
        stock_symbol=payload.stock_symbol,
        stock_market=payload.stock_market,
        useful=payload.useful,
        candidate_source=payload.candidate_source,
        strategy_tags=payload.strategy_tags,
        reason=payload.reason,
    )
    return {"ok": ok}


@router.get("/entry-candidates/stats")
def candidate_stats(days: int = Query(30, ge=1, le=365)):
    return get_entry_candidate_stats(days=days)


@router.post("/entry-candidates/outcomes/evaluate")
def evaluate_candidate_outcomes(
    limit: int = Query(400, ge=20, le=2000),
    snapshot_days: int = Query(45, ge=7, le=365),
):
    return evaluate_entry_candidate_outcomes(
        horizons=(1, 3, 5, 10),
        snapshot_days=snapshot_days,
        limit=limit,
    )


@router.get("/strategy-catalog")
def get_strategy_catalog(enabled_only: bool = Query(True, description="仅返回启用策略")):
    return {"items": list_strategy_catalog(enabled_only=enabled_only)}


@router.get("/strategy-signals")
def get_strategy_signal_list(
    market: str = Query("", description="市场代码: CN/HK/US"),
    status: str = Query("all", description="状态: active/inactive/all"),
    min_score: float = Query(0, ge=0, le=100),
    limit: int = Query(50, ge=1, le=500),
    snapshot_date: str = Query("", description="快照日期 YYYY-MM-DD，默认最新"),
    source_pool: str = Query("", description="来源池: market_scan/watchlist/mixed/all"),
    holding: str = Query("", description="持仓过滤: held/unheld/all"),
    strategy_code: str = Query("", description="策略代码"),
    risk_level: str = Query("", description="风险等级: low/medium/high/all"),
    include_payload: bool = Query(False, description="是否返回完整 payload（默认否，提升性能）"),
):
    result = list_strategy_signals(
        market=market,
        status=status,
        min_score=min_score,
        limit=limit,
        snapshot_date=snapshot_date,
        source_pool=source_pool,
        holding=holding,
        strategy_code=strategy_code,
        risk_level=risk_level,
        include_payload=include_payload,
    )
    # Phase 3: 注入 1-10 可解释评分 + 正负因子拆解
    for _it in result.get("items", []):
        enrich_signal(_it)
    return result


@router.get("/strategy-regimes")
def get_strategy_regimes(
    snapshot_date: str = Query("", description="快照日期 YYYY-MM-DD"),
    market: str = Query("", description="市场过滤: CN/HK/US"),
    limit: int = Query(100, ge=1, le=1000),
):
    return list_market_regime_snapshots(
        snapshot_date=snapshot_date,
        market=market,
        limit=limit,
    )


@router.get("/strategy-risk-snapshots")
def get_strategy_risk_snapshots(
    snapshot_date: str = Query("", description="快照日期 YYYY-MM-DD"),
    market: str = Query("", description="市场过滤: CN/HK/US"),
    limit: int = Query(100, ge=1, le=1000),
):
    return list_portfolio_risk_snapshots(
        snapshot_date=snapshot_date,
        market=market,
        limit=limit,
    )


@router.get("/strategy-factors/{signal_run_id}")
def get_strategy_factor(signal_run_id: int):
    return get_strategy_factor_snapshot(signal_run_id)


@router.post("/strategy-signals/refresh")
def refresh_strategy_signal_list(
    rebuild_candidates: bool = Query(True, description="是否先重算候选池"),
    snapshot_date: str = Query("", description="指定快照日期，不传则用最新"),
    max_inputs: int = Query(500, ge=20, le=2000),
    market_scan_limit: int = Query(80, ge=20, le=500),
    max_kline_symbols: int = Query(72, ge=0, le=300),
    limit_candidates: int = Query(2000, ge=50, le=10000),
    wait: bool = Query(False, description="是否同步等待刷新完成（默认后台执行）"),
):
    if wait:
        return refresh_strategy_signals(
            snapshot_date=snapshot_date,
            rebuild_candidates=rebuild_candidates,
            max_inputs=max_inputs,
            market_scan_limit=market_scan_limit,
            max_kline_symbols=max_kline_symbols,
            limit_candidates=limit_candidates,
        )

    started, state = _start_refresh_job(
        snapshot_date=snapshot_date,
        rebuild_candidates=rebuild_candidates,
        max_inputs=max_inputs,
        market_scan_limit=market_scan_limit,
        max_kline_symbols=max_kline_symbols,
        limit_candidates=limit_candidates,
    )
    latest_snapshot = _latest_strategy_snapshot()
    return {
        "queued": True,
        "running": True,
        "accepted": bool(started),
        "message": "已提交后台执行" if started else "刷新任务已在执行中",
        "snapshot_date": latest_snapshot or state.get("last_snapshot_date") or "",
        "count": 0,
        "items": [],
    }


@router.get("/strategy-signals/refresh-status")
def strategy_signal_refresh_status():
    state = _get_refresh_state()
    latest_snapshot = _latest_strategy_snapshot()
    return {
        "running": bool(state.get("running")),
        "started_at": state.get("started_at") or "",
        "finished_at": state.get("finished_at") or "",
        "last_error": state.get("last_error") or "",
        "last_snapshot_date": latest_snapshot or state.get("last_snapshot_date") or "",
    }


@router.post("/strategy-signals/outcomes/evaluate")
def evaluate_strategy_signal_outcomes(
    limit: int = Query(800, ge=20, le=5000),
    snapshot_days: int = Query(60, ge=7, le=365),
):
    return evaluate_strategy_outcomes(
        horizons=(1, 3, 5, 10),
        snapshot_days=snapshot_days,
        limit=limit,
    )


@router.post("/strategy-weights/rebalance")
def rebalance_strategy_weights_api(
    window_days: int = Query(45, ge=7, le=365),
    min_samples: int = Query(8, ge=3, le=500),
    alpha: float = Query(0.35, ge=0.05, le=0.95),
):
    return rebalance_strategy_weights(
        window_days=window_days,
        min_samples=min_samples,
        alpha=alpha,
        regime="default",
    )


@router.get("/strategy-stats")
def strategy_stats(days: int = Query(45, ge=1, le=365)):
    return get_strategy_stats(days=days)


@router.get("/strategy-factor-ic")
def strategy_factor_ic(
    days: int = Query(90, ge=7, le=365, description="回看快照天数"),
    horizon: int = Query(5, ge=1, le=60, description="持有期(交易日)"),
):
    """各因子的 IC/IR 有效性评估(StrategyFactorSnapshot × StrategyOutcome)。"""
    return evaluate_factor_ic(days=days, horizon=horizon)


@router.get("/strategy-weight-history")
def strategy_weight_history(
    strategy_code: str = Query("", description="策略代码过滤"),
    market: str = Query("", description="市场过滤"),
    limit: int = Query(200, ge=1, le=2000),
):
    return list_strategy_weight_history(
        strategy_code=strategy_code,
        market=market,
        limit=limit,
    )


@router.get("/strategy-accuracy-trend")
def strategy_accuracy_trend(
    days: int = Query(180, ge=7, le=730),
    horizon: int = Query(3, ge=1, le=60),
    strategy_code: str = Query(""),
    market: str = Query(""),
    granularity: str = Query("week", regex="^(week|month)$"),
):
    """按周/月粒度返回历史胜率趋势。"""
    return get_accuracy_trend(
        days=days,
        strategy_code=strategy_code,
        market=market,
        horizon=horizon,
        granularity=granularity,
    )


@router.get("/strategy-confidence-calibration")
def strategy_confidence_calibration(
    days: int = Query(180, ge=7, le=730),
    horizon: int = Query(3, ge=1, le=60),
    market: str = Query(""),
):
    """按信心度分桶，返回各桶实际胜率。"""
    return get_confidence_calibration(
        days=days,
        horizon=horizon,
        market=market,
    )


@router.get("/stock-signal-history")
def stock_signal_history(
    symbol: str = Query(..., description="股票代码"),
    market: str = Query("CN", description="市场 CN/HK/US"),
    days: int = Query(180, ge=7, le=730),
    horizon: int = Query(3, ge=1, le=60),
):
    """某只股票的历史信号及后验涨跌结果。"""
    return get_stock_signal_history(
        symbol=symbol,
        market=market,
        days=days,
        horizon=horizon,
    )


def _num(v, default=0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _plan_prices(signal: StrategySignalRun) -> dict:
    entry_low = signal.entry_low
    entry_high = signal.entry_high
    entry_mid = None
    if entry_low is not None and entry_high is not None:
        entry_mid = (float(entry_low) + float(entry_high)) / 2
    elif entry_low is not None:
        entry_mid = float(entry_low)
    elif entry_high is not None:
        entry_mid = float(entry_high)
    stop = signal.stop_loss
    target = signal.target_price
    risk_reward = None
    if entry_mid and stop and target:
        risk = entry_mid - float(stop)
        reward = float(target) - entry_mid
        if risk > 0 and reward > 0:
            risk_reward = round(reward / risk, 2)
    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "entry_mid": entry_mid,
        "stop_loss": stop,
        "target_price": target,
        "risk_reward": risk_reward,
        "holding_days": signal.holding_days or 3,
    }


def _strategy_operability(db, signal: StrategySignalRun, days: int, horizon: int) -> dict:
    since = datetime.now(timezone.utc).replace(tzinfo=None)
    # SQLite DateTime 可能是 naive，按 created_at 窗口过滤时用简单天数近似。
    from datetime import timedelta
    since = since - timedelta(days=int(days))
    q = (
        db.query(StrategyOutcome)
        .filter(
            StrategyOutcome.strategy_code == signal.strategy_code,
            StrategyOutcome.stock_market == signal.stock_market,
            StrategyOutcome.horizon_days == int(horizon),
            StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
            StrategyOutcome.created_at >= since,
        )
    )
    rows = q.all()
    total = len(rows)
    wins = sum(1 for r in rows if _num(r.outcome_return_pct) > 0)
    hit_target = sum(1 for r in rows if bool(r.hit_target))
    hit_stop = sum(1 for r in rows if bool(r.hit_stop))
    returns = [_num(r.outcome_return_pct) for r in rows if r.outcome_return_pct is not None]
    avg_ret = sum(returns) / len(returns) if returns else 0.0
    worst_ret = min(returns) if returns else 0.0
    best_ret = max(returns) if returns else 0.0

    stock_rows = (
        db.query(StrategyOutcome)
        .filter(
            StrategyOutcome.stock_symbol == signal.stock_symbol,
            StrategyOutcome.stock_market == signal.stock_market,
            StrategyOutcome.horizon_days == int(horizon),
            StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
            StrategyOutcome.created_at >= since,
        )
        .all()
    )
    stock_returns = [_num(r.outcome_return_pct) for r in stock_rows if r.outcome_return_pct is not None]
    stock_wins = sum(1 for r in stock_rows if _num(r.outcome_return_pct) > 0)
    return {
        "window_days": days,
        "horizon_days": horizon,
        "strategy_samples": total,
        "strategy_win_rate": round(wins / total, 4) if total else 0.0,
        "target_hit_rate": round(hit_target / total, 4) if total else 0.0,
        "stop_hit_rate": round(hit_stop / total, 4) if total else 0.0,
        "avg_return_pct": round(avg_ret, 2),
        "best_return_pct": round(best_ret, 2),
        "worst_return_pct": round(worst_ret, 2),
        "stock_samples": len(stock_rows),
        "stock_win_rate": round(stock_wins / len(stock_rows), 4) if stock_rows else 0.0,
        "stock_avg_return_pct": round(sum(stock_returns) / len(stock_returns), 2) if stock_returns else 0.0,
    }


def _debate(signal: StrategySignalRun) -> dict:
    payload = signal.payload if isinstance(signal.payload, dict) else {}
    breakdown = payload.get("score_breakdown") if isinstance(payload.get("score_breakdown"), dict) else {}
    news_metric = payload.get("news_metric") if isinstance(payload.get("news_metric"), dict) else {}
    market_regime = payload.get("market_regime") if isinstance(payload.get("market_regime"), dict) else {}
    cross_feature = payload.get("cross_feature") if isinstance(payload.get("cross_feature"), dict) else {}
    evidence = signal.evidence or []
    bulls: list[str] = []
    bears: list[str] = []
    if signal.signal:
        bulls.append(signal.signal)
    for ev in evidence[:3]:
        if ev:
            bulls.append(str(ev))
    if _num(breakdown.get("alpha_score")) >= 65:
        bulls.append(f"Alpha 得分较强: {breakdown.get('alpha_score')}")
    if _num(breakdown.get("catalyst_score")) >= 65:
        bulls.append(f"催化得分较强: {breakdown.get('catalyst_score')}")
    if _num(news_metric.get("event_score")) >= 60:
        bulls.append(f"新闻/事件偏强: {news_metric.get('event_score')}")
    if _num(cross_feature.get("relative_strength_pct")) >= 70:
        bulls.append(f"相对强弱处于高分位: {cross_feature.get('relative_strength_pct')}")
    if _num(breakdown.get("risk_penalty")) > 15:
        bears.append(f"风险惩罚偏高: {breakdown.get('risk_penalty')}")
    if signal.risk_level == "high":
        bears.append("风险等级较高，需降低仓位或等待确认")
    if market_regime.get("regime") == "bearish":
        bears.append(f"市场状态偏空: {market_regime.get('regime_label') or 'bearish'}")
    if not signal.entry_low and not signal.entry_high:
        bears.append("缺少明确入场区间，不宜追单")
    if signal.invalidation:
        bears.append(f"失效条件: {signal.invalidation}")
    return {
        "bulls": list(dict.fromkeys(bulls))[:6],
        "bears": list(dict.fromkeys(bears))[:6],
        "key_disagreement": "价格能否进入计划买入区间并守住止损线",
        "verdict": signal.action_label or signal.action or "观望",
    }


def _option_advice(
    db,
    signal: StrategySignalRun,
    plan: dict,
    operability: dict,
    *,
    current_price: float | None = None,
    iv_rank: float | None = None,
    holding_qty: int = 0,
    risk_budget_pct: float = 2.0,
) -> dict:
    """生成个股级股票+期权操作建议。

    没有真实期权链时只输出结构和筛选条件；接入链后再把条件落到具体合约。
    """
    entry_low = _num(plan.get("entry_low"), 0.0)
    entry_high = _num(plan.get("entry_high"), 0.0)
    entry_mid = _num(plan.get("entry_mid"), 0.0)
    stop = _num(plan.get("stop_loss"), 0.0)
    target = _num(plan.get("target_price"), 0.0)
    px = _num(current_price, 0.0) or entry_mid or entry_low or entry_high
    market = (signal.stock_market or "").upper()
    action = (signal.action or "").lower()
    confidence = _num(signal.confidence, _num(signal.rank_score, 0.0) / 100.0)
    win_rate = _num(operability.get("strategy_win_rate"), 0.0)
    avg_return = _num(operability.get("avg_return_pct"), 0.0)
    samples = int(_num(operability.get("strategy_samples"), 0.0))

    can_price = bool(px and stop and target and target > px > stop)
    reward = max(0.0, target - px) if px and target else 0.0
    risk = max(0.0, px - stop) if px and stop else 0.0
    rr = round(reward / risk, 2) if risk > 0 else None
    upside_pct = round(reward / px * 100, 2) if px and reward else 0.0
    downside_pct = round(risk / px * 100, 2) if px and risk else 0.0

    if action in ("sell", "reduce"):
        stance = "bearish"
    elif action in ("buy", "add") and confidence >= 0.6 and avg_return >= 0:
        stance = "bullish"
    elif action in ("buy", "add"):
        stance = "cautious_bullish"
    else:
        stance = "neutral"

    if entry_low and entry_high and px:
        if px < stop:
            stock_action = "avoid_or_exit"
            stock_text = "价格已跌破止损线，先退出/不新开仓。"
        elif px < entry_low:
            stock_action = "wait"
            stock_text = f"等待回到入场区间 {entry_low:g}-{entry_high:g}，不提前追。"
        elif px <= entry_high:
            stock_action = "buy_or_hold" if stance.startswith("bullish") else "small_probe"
            stock_text = "价格在计划区间内，可按风险预算分批执行。"
        elif px <= entry_high * 1.02:
            stock_action = "small_probe"
            stock_text = "略高于入场区间，只允许小仓试单，回落再补。"
        else:
            stock_action = "no_chase"
            stock_text = "价格明显高于入场区间，放弃追高，等回踩或新信号。"
    else:
        stock_action = "wait"
        stock_text = "缺少完整入场区间，先观察，等系统生成明确价格。"

    if samples < 8:
        conviction = "low"
    elif confidence >= 0.75 and win_rate >= 0.55 and avg_return > 0 and (rr or 0) >= 1.5:
        conviction = "high"
    elif confidence >= 0.6 and avg_return >= 0:
        conviction = "medium"
    else:
        conviction = "low"

    active_rules = (
        db.query(StrategyRuleInsight)
        .filter(
            StrategyRuleInsight.status == "active",
            StrategyRuleInsight.scope_type.in_(("strategy", "global")),
        )
        .order_by(StrategyRuleInsight.generated_at.desc())
        .limit(50)
        .all()
    )
    matched_rules: list[dict] = []
    for r in active_rules:
        strategy_match = (
            r.scope_type == "strategy"
            and (not r.strategy_code or r.strategy_code == signal.strategy_code)
            and (not r.stock_market or r.stock_market == signal.stock_market)
        )
        global_match = r.scope_type == "global"
        if strategy_match or global_match:
            matched_rules.append({
                "severity": r.severity,
                "title": r.title,
                "recommendation": r.recommendation,
            })
    if any(r["severity"] == "block" for r in matched_rules):
        conviction = "low"
        stock_action = "wait"
        stock_text = "学习规则触发拦截：该类信号近期表现偏弱，先等待右侧确认，不自动开仓。"
    elif any(r["severity"] == "warn" for r in matched_rules) and conviction == "high":
        conviction = "medium"

    optionable = market == "US"
    data_needed = [
        "标的实时价/最新成交价",
        "可交易到期日列表",
        "每个合约的 bid、ask、last、volume、open interest",
        "Delta、IV、IV Rank/Percentile",
        "财报/上市/解禁等事件日期",
        "你的账户期权权限、最大可亏损金额、是否允许价差单",
    ]
    if not optionable:
        data_needed.insert(1, "若是港股/韩股，请确认券商是否提供该标的期权；多数情况下需要用 ADR/美股替代品做期权腿")

    expiry = "45-75 天"
    if iv_rank is not None and iv_rank >= 60:
        bullish_structure = "Call Debit Spread"
        bullish_detail = "买入 Delta 0.45-0.60 的 Call，卖出目标价附近或 Delta 0.20-0.35 的 Call；最大亏损=净权利金。"
    else:
        bullish_structure = "Long Call 或 Call Debit Spread"
        bullish_detail = "若 IV 不贵可买入 Delta 0.45-0.60 Call；若权利金偏贵，改用 Call Debit Spread 降低成本。"

    if stance in ("bearish", "neutral"):
        primary = {
            "name": "Put Debit Spread",
            "direction": "看跌/保护",
            "expiry": expiry,
            "instruction": "买入接近平值 Put，卖出下方支撑/目标跌幅位置 Put；最大亏损=净权利金。",
            "use_when": "用于破位、减仓保护，或对冲同板块多头仓位。",
        }
    elif holding_qty > 0 and stance.startswith("bullish"):
        primary = {
            "name": "Covered Call + Protective Put 备兑增强/保护",
            "direction": "持仓管理",
            "expiry": "30-60 天",
            "instruction": "持有正股时，可在目标价上方卖 Delta 0.20-0.30 Call；若仓位过重，同时买入止损线附近 Put 做灾难保护。",
            "use_when": "适合已有持仓、想锁定一部分收益，同时保留核心仓位。",
        }
    else:
        primary = {
            "name": bullish_structure,
            "direction": "看涨",
            "expiry": expiry,
            "instruction": bullish_detail,
            "use_when": "价格进入入场区间，且相对强弱/成交量确认时执行。",
        }

    hedge = {
        "name": "Pair Hedge 配对对冲",
        "direction": "相对强弱",
        "instruction": "若逻辑是某只股票跑赢同产业链高估值标的，可用目标股票 Call Spread + 对手股票 Put Spread；两腿最大亏损按 1:1 或 2:1 风险预算匹配。",
        "use_when": "适合 HBM/存储这类同产业链估值差交易，避免只暴露行业 beta。",
    }

    return {
        "available": True,
        "data_status": "needs_option_chain",
        "optionable_guess": optionable,
        "stance": stance,
        "conviction": conviction,
        "price_snapshot": {
            "current_price": px or None,
            "entry_low": plan.get("entry_low"),
            "entry_high": plan.get("entry_high"),
            "stop_loss": plan.get("stop_loss"),
            "target_price": plan.get("target_price"),
            "upside_pct": upside_pct,
            "downside_pct": downside_pct,
            "risk_reward": rr,
            "can_price": can_price,
        },
        "stock_instruction": {
            "action": stock_action,
            "text": stock_text,
            "risk_budget_pct": min(max(float(risk_budget_pct or 2.0), 0.2), 5.0),
            "stop_rule": f"跌破 {stop:g} 退出/不补仓" if stop else "缺少止损价，禁止重仓",
            "target_rule": f"接近 {target:g} 分批止盈" if target else "缺少目标价，先按移动止盈",
        },
        "option_instruction": primary,
        "hedge_instruction": hedge,
        "checklist": [
            "先确认价格是否在入场区间，不追高。",
            "先算最大亏损，再决定合约数量。",
            "优先价差单，少用裸买极虚值期权。",
            "Bid/Ask 价差超过权利金 8%-10% 时不下单。",
            "事件日前后 IV 很高时，避免单纯买跨或裸买贵期权。",
        ],
        "learning_rules": matched_rules[:5],
        "data_needed": data_needed,
    }


def _paper_follow(db, signal: StrategySignalRun) -> dict:
    pos = (
        db.query(PaperTradingPosition)
        .filter(
            PaperTradingPosition.stock_symbol == signal.stock_symbol,
            PaperTradingPosition.stock_market == signal.stock_market,
            PaperTradingPosition.status == "open",
        )
        .order_by(PaperTradingPosition.opened_at.desc())
        .first()
    )
    trades = (
        db.query(PaperTradingTrade)
        .filter(
            PaperTradingTrade.stock_symbol == signal.stock_symbol,
            PaperTradingTrade.stock_market == signal.stock_market,
        )
        .order_by(PaperTradingTrade.closed_at.desc())
        .limit(10)
        .all()
    )
    closed = len(trades)
    wins = sum(1 for t in trades if _num(t.pnl) > 0)
    return {
        "open_position": {
            "id": pos.id,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "current_price": pos.current_price,
            "stop_loss": pos.stop_loss,
            "target_price": pos.target_price,
            "unrealized_pnl": round(_num(pos.unrealized_pnl), 2),
            "strategy_code": pos.strategy_code or "",
            "opened_at": pos.opened_at.isoformat() if pos.opened_at else "",
        } if pos else None,
        "recent_closed": closed,
        "recent_win_rate": round(wins / closed, 4) if closed else 0.0,
        "recent_avg_pnl_pct": round(sum(_num(t.pnl_pct) for t in trades) / closed, 2) if closed else 0.0,
    }


@router.get("/trade-plan")
def trade_plan(
    symbol: str = Query(..., description="股票代码"),
    market: str = Query("CN", description="市场 CN/HK/US"),
    days: int = Query(180, ge=7, le=730),
    horizon: int = Query(3, ge=1, le=60),
    current_price: float | None = Query(None, description="可选: 当前价，用于刷新个股指令"),
    iv_rank: float | None = Query(None, ge=0, le=100, description="可选: IV Rank/Percentile"),
    holding_qty: int = Query(0, ge=0, description="可选: 当前持仓数量"),
):
    """聚合交易计划、历史可操作性、多空博弈与模拟盘跟进。"""
    db = SessionLocal()
    try:
        signal = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.stock_symbol == symbol,
                StrategySignalRun.stock_market == market,
            )
            .order_by(StrategySignalRun.snapshot_date.desc(), StrategySignalRun.rank_score.desc())
            .first()
        )
        if not signal:
            return {"available": False, "symbol": symbol, "market": market}
        enrich_payload = {
            "id": signal.id,
            "snapshot_date": signal.snapshot_date,
            "stock_symbol": signal.stock_symbol,
            "stock_market": signal.stock_market,
            "stock_name": signal.stock_name,
            "strategy_code": signal.strategy_code,
            "strategy_name": signal.strategy_name,
            "rank_score": signal.rank_score,
            "action": signal.action,
            "action_label": signal.action_label,
            "signal": signal.signal,
            "reason": signal.reason,
            "risk_level": signal.risk_level,
            "invalidation": signal.invalidation,
        }
        enrich_signal(enrich_payload)
        plan = _plan_prices(signal)
        operability = _strategy_operability(db, signal, days, horizon)
        return {
            "available": True,
            "signal": enrich_payload,
            "plan": plan,
            "operability": operability,
            "debate": _debate(signal),
            "paper_follow": _paper_follow(db, signal),
            "options_advice": _option_advice(
                db,
                signal,
                plan,
                operability,
                current_price=current_price,
                iv_rank=iv_rank,
                holding_qty=holding_qty,
            ),
        }
    finally:
        db.close()


@router.get("/options-advice")
def options_advice(
    symbol: str = Query(..., description="股票代码"),
    market: str = Query("US", description="市场 CN/HK/US"),
    days: int = Query(180, ge=7, le=730),
    horizon: int = Query(10, ge=1, le=90),
    current_price: float | None = Query(None),
    iv_rank: float | None = Query(None, ge=0, le=100),
    holding_qty: int = Query(0, ge=0),
    risk_budget_pct: float = Query(2.0, ge=0.2, le=5.0),
):
    """个股级股票+期权执行建议。

    先使用系统已有交易计划和历史可操作性；没有真实期权链时输出可执行的合约筛选条件。
    """
    db = SessionLocal()
    try:
        signal = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.stock_symbol == symbol,
                StrategySignalRun.stock_market == market,
            )
            .order_by(StrategySignalRun.snapshot_date.desc(), StrategySignalRun.rank_score.desc())
            .first()
        )
        if not signal:
            return {
                "available": False,
                "symbol": symbol,
                "market": market,
                "message": "暂无该标的交易计划，请先在机会页刷新策略信号或手动补充价格。",
                "data_needed": [
                    "当前价",
                    "入场区间",
                    "止损价",
                    "目标价",
                    "期权链 bid/ask、Delta、IV、成交量、未平仓量",
                ],
            }
        plan = _plan_prices(signal)
        operability = _strategy_operability(db, signal, days, horizon)
        return {
            "available": True,
            "symbol": symbol,
            "market": market,
            "signal": {
                "id": signal.id,
                "stock_name": signal.stock_name,
                "strategy_code": signal.strategy_code,
                "strategy_name": signal.strategy_name,
                "rank_score": signal.rank_score,
                "confidence": signal.confidence,
                "action": signal.action,
                "action_label": signal.action_label,
                "reason": signal.reason,
            },
            "plan": plan,
            "operability": operability,
            "advice": _option_advice(
                db,
                signal,
                plan,
                operability,
                current_price=current_price,
                iv_rank=iv_rank,
                holding_qty=holding_qty,
                risk_budget_pct=risk_budget_pct,
            ),
        }
    finally:
        db.close()
