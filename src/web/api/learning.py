"""学习闭环 API：交易复盘、规则洞察和自我改进摘要。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from src.web.database import SessionLocal
from src.web.models import (
    PaperTradingTrade,
    RealTradeSell,
    StrategyOutcome,
    StrategyRuleInsight,
    TradeReview,
)

router = APIRouter()


class TradeReviewIn(BaseModel):
    source: str = Field("manual", pattern="^(paper|real|manual)$")
    source_id: int | None = None
    stock_symbol: str
    stock_market: str = "US"
    stock_name: str = ""
    strategy_code: str = ""
    strategy_name: str = ""
    action_taken: str = ""
    thesis: str = ""
    result: str = Field("unknown", pattern="^(win|loss|flat|unknown)$")
    pnl_pct: float | None = None
    mistake_tags: list[str] = Field(default_factory=list)
    improvement: str = ""
    confidence_before: float | None = None
    confidence_after: float | None = None


def _num(v, default=0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _avg(vals: list[float]) -> float:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _win_rate(vals: list[float]) -> float:
    vals = [v for v in vals if v is not None]
    return round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else 0.0


def _serialize_review(r: TradeReview) -> dict:
    return {
        "id": r.id,
        "source": r.source,
        "source_id": r.source_id,
        "stock_symbol": r.stock_symbol,
        "stock_market": r.stock_market,
        "stock_name": r.stock_name,
        "strategy_code": r.strategy_code,
        "strategy_name": r.strategy_name,
        "action_taken": r.action_taken,
        "thesis": r.thesis,
        "result": r.result,
        "pnl_pct": r.pnl_pct,
        "mistake_tags": r.mistake_tags or [],
        "improvement": r.improvement,
        "confidence_before": r.confidence_before,
        "confidence_after": r.confidence_after,
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }


def _serialize_rule(r: StrategyRuleInsight) -> dict:
    return {
        "id": r.id,
        "scope_type": r.scope_type,
        "scope_key": r.scope_key,
        "stock_market": r.stock_market,
        "strategy_code": r.strategy_code,
        "severity": r.severity,
        "title": r.title,
        "recommendation": r.recommendation,
        "evidence": r.evidence or {},
        "status": r.status,
        "generated_at": r.generated_at.isoformat() if r.generated_at else "",
    }


def _build_summary(db, days: int) -> dict:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    paper = (
        db.query(PaperTradingTrade)
        .filter(PaperTradingTrade.closed_at >= since)
        .all()
    )
    real = (
        db.query(RealTradeSell)
        .filter(RealTradeSell.sold_at >= since)
        .all()
    )
    outcomes = (
        db.query(StrategyOutcome)
        .filter(
            StrategyOutcome.created_at >= since,
            StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
        )
        .all()
    )
    reviews = (
        db.query(TradeReview)
        .filter(TradeReview.created_at >= since)
        .order_by(TradeReview.created_at.desc())
        .limit(50)
        .all()
    )

    paper_returns = [_num(t.pnl_pct) for t in paper]
    real_returns = [_num(t.pnl_pct) for t in real]
    outcome_returns = [_num(o.outcome_return_pct) for o in outcomes if o.outcome_return_pct is not None]

    by_strategy: dict[str, dict] = defaultdict(lambda: {
        "strategy_code": "",
        "market": "",
        "samples": 0,
        "returns": [],
        "target_hits": 0,
        "stop_hits": 0,
    })
    for o in outcomes:
        key = f"{o.strategy_code or 'unknown'}:{o.stock_market or ''}"
        row = by_strategy[key]
        row["strategy_code"] = o.strategy_code or "unknown"
        row["market"] = o.stock_market or ""
        row["samples"] += 1
        row["returns"].append(_num(o.outcome_return_pct))
        row["target_hits"] += 1 if o.hit_target else 0
        row["stop_hits"] += 1 if o.hit_stop else 0

    strategy_rows = []
    for row in by_strategy.values():
        samples = int(row["samples"] or 0)
        rets = row["returns"]
        strategy_rows.append({
            "strategy_code": row["strategy_code"],
            "market": row["market"],
            "samples": samples,
            "win_rate": _win_rate(rets),
            "avg_return_pct": _avg(rets),
            "target_hit_rate": round(row["target_hits"] / samples, 4) if samples else 0.0,
            "stop_hit_rate": round(row["stop_hits"] / samples, 4) if samples else 0.0,
        })
    strategy_rows.sort(key=lambda x: (x["avg_return_pct"], x["win_rate"]), reverse=True)

    review_tags: dict[str, int] = defaultdict(int)
    for r in reviews:
        for tag in (r.mistake_tags or []):
            review_tags[str(tag)] += 1

    return {
        "window_days": days,
        "overview": {
            "paper_trades": len(paper),
            "paper_win_rate": _win_rate(paper_returns),
            "paper_avg_return_pct": _avg(paper_returns),
            "real_sells": len(real),
            "real_win_rate": _win_rate(real_returns),
            "real_avg_return_pct": _avg(real_returns),
            "strategy_outcomes": len(outcomes),
            "strategy_win_rate": _win_rate(outcome_returns),
            "strategy_avg_return_pct": _avg(outcome_returns),
            "manual_reviews": len(reviews),
        },
        "strategies": strategy_rows[:30],
        "weak_strategies": [
            x for x in strategy_rows
            if x["samples"] >= 6 and (x["avg_return_pct"] < 0 or x["stop_hit_rate"] >= 0.35)
        ][:10],
        "strong_strategies": [
            x for x in strategy_rows
            if x["samples"] >= 6 and x["avg_return_pct"] > 0 and x["win_rate"] >= 0.55
        ][:10],
        "review_tags": sorted(
            [{"tag": k, "count": v} for k, v in review_tags.items()],
            key=lambda x: x["count"],
            reverse=True,
        ),
        "recent_reviews": [_serialize_review(r) for r in reviews[:10]],
    }


def _rule_candidates(summary: dict) -> list[dict]:
    out: list[dict] = []
    overview = summary.get("overview", {})
    if overview.get("paper_trades", 0) >= 5 and overview.get("paper_avg_return_pct", 0) < 0:
        out.append({
            "scope_type": "global",
            "scope_key": "paper_trading",
            "stock_market": "",
            "strategy_code": "",
            "severity": "warn",
            "title": "模拟盘近期为负收益",
            "recommendation": "新信号默认降低仓位，优先选择风险收益比大于 1.5 且有明确止损的计划。",
            "evidence": overview,
        })
    for row in summary.get("weak_strategies", []):
        severity = "block" if row.get("avg_return_pct", 0) < -3 or row.get("stop_hit_rate", 0) >= 0.5 else "warn"
        out.append({
            "scope_type": "strategy",
            "scope_key": f"{row.get('strategy_code')}:{row.get('market')}",
            "stock_market": row.get("market") or "",
            "strategy_code": row.get("strategy_code") or "",
            "severity": severity,
            "title": f"{row.get('market') or '全市场'} {row.get('strategy_code')} 近期表现偏弱",
            "recommendation": "降低该策略权重；除非个股有更强催化和更好的入场价，否则不自动加仓。",
            "evidence": row,
        })
    for row in summary.get("strong_strategies", [])[:5]:
        out.append({
            "scope_type": "strategy",
            "scope_key": f"{row.get('strategy_code')}:{row.get('market')}",
            "stock_market": row.get("market") or "",
            "strategy_code": row.get("strategy_code") or "",
            "severity": "info",
            "title": f"{row.get('market') or '全市场'} {row.get('strategy_code')} 近期可优先观察",
            "recommendation": "同类信号可保留正常仓位，但仍需满足价格进入入场区间和止损明确。",
            "evidence": row,
        })
    for item in summary.get("review_tags", [])[:5]:
        out.append({
            "scope_type": "global",
            "scope_key": f"review_tag:{item.get('tag')}",
            "stock_market": "",
            "strategy_code": "",
            "severity": "warn",
            "title": f"人工复盘高频问题: {item.get('tag')}",
            "recommendation": "生成新建议时优先检查该问题，必要时把动作从买入降为等待确认。",
            "evidence": item,
        })
    return out


@router.get("/summary")
def learning_summary(days: int = Query(90, ge=7, le=730)):
    db = SessionLocal()
    try:
        summary = _build_summary(db, days)
        rules = (
            db.query(StrategyRuleInsight)
            .filter(StrategyRuleInsight.status == "active")
            .order_by(StrategyRuleInsight.generated_at.desc())
            .limit(20)
            .all()
        )
        summary["active_rules"] = [_serialize_rule(r) for r in rules]
        summary["candidate_rules"] = _rule_candidates(summary)
        return summary
    finally:
        db.close()


@router.post("/reviews")
def create_review(body: TradeReviewIn):
    db = SessionLocal()
    try:
        row = TradeReview(
            source=body.source,
            source_id=body.source_id,
            stock_symbol=body.stock_symbol.strip().upper(),
            stock_market=body.stock_market.strip().upper() or "US",
            stock_name=body.stock_name,
            strategy_code=body.strategy_code,
            strategy_name=body.strategy_name,
            action_taken=body.action_taken,
            thesis=body.thesis,
            result=body.result,
            pnl_pct=body.pnl_pct,
            mistake_tags=body.mistake_tags,
            improvement=body.improvement,
            confidence_before=body.confidence_before,
            confidence_after=body.confidence_after,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _serialize_review(row)
    finally:
        db.close()


@router.get("/reviews")
def list_reviews(limit: int = Query(50, ge=1, le=200)):
    db = SessionLocal()
    try:
        rows = db.query(TradeReview).order_by(TradeReview.created_at.desc()).limit(limit).all()
        return {"items": [_serialize_review(r) for r in rows]}
    finally:
        db.close()


@router.post("/rules/rebuild")
def rebuild_rules(days: int = Query(90, ge=7, le=730)):
    db = SessionLocal()
    try:
        summary = _build_summary(db, days)
        candidates = _rule_candidates(summary)
        db.query(StrategyRuleInsight).update({"status": "archived"})
        for item in candidates:
            db.add(StrategyRuleInsight(
                scope_type=item["scope_type"],
                scope_key=item["scope_key"],
                stock_market=item["stock_market"],
                strategy_code=item["strategy_code"],
                severity=item["severity"],
                title=item["title"],
                recommendation=item["recommendation"],
                evidence=item["evidence"],
                status="active",
            ))
        db.commit()
        return {"count": len(candidates), "items": candidates}
    finally:
        db.close()


@router.get("/rules")
def list_rules(status: str = Query("active", pattern="^(active|archived|all)$")):
    db = SessionLocal()
    try:
        q = db.query(StrategyRuleInsight)
        if status != "all":
            q = q.filter(StrategyRuleInsight.status == status)
        rows = q.order_by(StrategyRuleInsight.generated_at.desc()).limit(100).all()
        return {"items": [_serialize_rule(r) for r in rows]}
    finally:
        db.close()
