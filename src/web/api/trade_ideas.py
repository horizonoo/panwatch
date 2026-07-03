"""Trade idea capture API.

Turns long-form theses into structured watchable trade plans.
"""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.core.json_safe import to_jsonable
from src.web.database import SessionLocal
from src.web.models import TradeIdea, TradeIdeaScoreModel

router = APIRouter()


class TradeIdeaCreate(BaseModel):
    raw_text: str = Field(..., min_length=10)
    title: str = ""
    source: str = "manual"


class TradeIdeaStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(watching|ready|open|closed|archived)$")


class ScoreModelUpdate(BaseModel):
    label: str = ""
    weights: dict[str, float]
    enabled: bool = True


def _iso_month_day(text: str, *, year: int | None = None) -> str:
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text or "")
    if not m:
        return ""
    y = year or date.today().year
    try:
        return date(y, int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return ""


def _first_title(text: str) -> str:
    for line in (text or "").splitlines():
        clean = line.strip(" #\t")
        if clean and clean.lower() != "image":
            return clean[:80]
    return "未命名交易思路"


def _extract_metric(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text or "", re.I)
    if not m:
        return None
    try:
        return round(float(m.group(1)), 2)
    except (TypeError, ValueError):
        return None


def _detect_memory_pair(text: str) -> tuple[list[dict], dict]:
    low = text.lower()
    has_mu = "美光" in text or "micron" in low or re.search(r"\bMU\b", text)
    has_hynix = "海力士" in text or "hynix" in low or "000660" in text
    if has_mu and has_hynix:
        legs = [
            {
                "symbol": "000660.KS",
                "name": "SK Hynix / 海力士",
                "market": "KR",
                "direction": "long",
                "instrument": "stock_or_call_spread",
                "role": "估值折价修复腿",
            },
            {
                "symbol": "MU",
                "name": "Micron / 美光科技",
                "market": "US",
                "direction": "short",
                "instrument": "put_spread_or_short_stock",
                "role": "估值溢价回落对冲腿",
            },
        ]
        metrics = {
            "valuation_gap_pct": _extract_metric(r"估值(?:溢价|相差).*?(\d+(?:\.\d+)?)%", text),
            "long_target_upside_pct": _extract_metric(r"海力士上涨\s*(\d+(?:\.\d+)?)%", text),
            "short_gap_fill_downside_pct": _extract_metric(r"回撤约\s*(\d+(?:\.\d+)?)%", text),
            "spread_rebalance_thesis": "long_hynix_short_micron",
        }
        return legs, metrics
    return [], {}


SCORE_DIMENSIONS = (
    "logic_strength",
    "catalyst_strength",
    "data_reliability",
    "payoff_quality",
    "discipline_fit",
)

MARKET_SCORE_MODELS = {
    "US": {
        "label": "美股模型",
        "weights": {
            "logic_strength": 0.22,
            "catalyst_strength": 0.22,
            "data_reliability": 0.20,
            "payoff_quality": 0.18,
            "discipline_fit": 0.18,
        },
    },
    "HK": {
        "label": "港股模型",
        "weights": {
            "logic_strength": 0.24,
            "catalyst_strength": 0.18,
            "data_reliability": 0.18,
            "payoff_quality": 0.24,
            "discipline_fit": 0.16,
        },
    },
    "CN": {
        "label": "A股模型",
        "weights": {
            "logic_strength": 0.18,
            "catalyst_strength": 0.24,
            "data_reliability": 0.16,
            "payoff_quality": 0.16,
            "discipline_fit": 0.26,
        },
    },
    "PAIR": {
        "label": "跨市场配对模型",
        "weights": {
            "logic_strength": 0.24,
            "catalyst_strength": 0.18,
            "data_reliability": 0.20,
            "payoff_quality": 0.22,
            "discipline_fit": 0.16,
        },
    },
}


def _normalize_weights(weights: dict | None) -> dict[str, float]:
    raw: dict[str, float] = {}
    for key in SCORE_DIMENSIONS:
        try:
            raw[key] = max(0.0, float((weights or {}).get(key, 0.0)))
        except (TypeError, ValueError):
            raw[key] = 0.0
    total = sum(raw.values())
    if total <= 0:
        return {key: round(1 / len(SCORE_DIMENSIONS), 4) for key in SCORE_DIMENSIONS}
    return {key: round(raw[key] / total, 4) for key in SCORE_DIMENSIONS}


def _default_score_models() -> dict[str, dict]:
    return {
        key: {
            "model_key": key,
            "label": str(value.get("label") or key),
            "weights": _normalize_weights(value.get("weights")),
            "enabled": True,
        }
        for key, value in MARKET_SCORE_MODELS.items()
    }


def _load_score_models(db) -> dict[str, dict]:
    models = _default_score_models()
    rows = db.query(TradeIdeaScoreModel).filter(TradeIdeaScoreModel.enabled.is_(True)).all()
    for row in rows:
        key = (row.model_key or "").strip().upper()
        if key not in models:
            continue
        models[key] = {
            "model_key": key,
            "label": row.label or models[key]["label"],
            "weights": _normalize_weights(row.weights or models[key]["weights"]),
            "enabled": bool(row.enabled),
        }
    return models


def ensure_score_models(db) -> list[TradeIdeaScoreModel]:
    defaults = _default_score_models()
    for key, cfg in defaults.items():
        existing = (
            db.query(TradeIdeaScoreModel)
            .filter(TradeIdeaScoreModel.model_key == key)
            .first()
        )
        if not existing:
            db.add(
                TradeIdeaScoreModel(
                    model_key=key,
                    label=cfg["label"],
                    weights=cfg["weights"],
                    enabled=True,
                )
            )
    db.commit()
    return db.query(TradeIdeaScoreModel).order_by(TradeIdeaScoreModel.model_key).all()


def _score_market_model(legs: list[dict]) -> str:
    markets = {str(x.get("market") or "").upper() for x in legs if x.get("market")}
    directions = {str(x.get("direction") or "").lower() for x in legs}
    if len(markets) > 1 or {"long", "short"}.issubset(directions):
        return "PAIR"
    if "US" in markets:
        return "US"
    if "HK" in markets:
        return "HK"
    if "CN" in markets:
        return "CN"
    return "US"


def _score_trade_idea(
    *,
    legs: list[dict],
    metrics: dict,
    event_date: str,
    entry_date: str,
    score_models: dict[str, dict] | None = None,
) -> dict:
    scores = {
        "logic_strength": 78 if legs else 55,
        "catalyst_strength": 82 if event_date else 50,
        "data_reliability": 62,
        "payoff_quality": 70,
        "discipline_fit": 58,
    }
    evidence: dict[str, list[str]] = {k: [] for k in SCORE_DIMENSIONS}
    if legs:
        evidence["logic_strength"].append("已拆出可执行交易腿，逻辑可映射到仓位。")
    if event_date:
        evidence["catalyst_strength"].append(f"识别到明确催化日期 {event_date}。")

    gap = metrics.get("valuation_gap_pct")
    upside = metrics.get("long_target_upside_pct")
    downside = metrics.get("short_gap_fill_downside_pct")
    if isinstance(gap, (int, float)) and gap >= 40:
        scores["logic_strength"] += 8
        scores["payoff_quality"] += 8
        evidence["logic_strength"].append(f"估值差 {gap:.0f}% 足够大，具备重新定价空间。")
        evidence["payoff_quality"].append("估值差提供价差收敛赔率。")
    if isinstance(upside, (int, float)) and upside >= 30:
        scores["payoff_quality"] += 6
        evidence["payoff_quality"].append(f"long 腿目标上行 {upside:.0f}% 大于常规波段阈值。")
    if isinstance(downside, (int, float)) and downside >= 20:
        scores["payoff_quality"] += 4
        evidence["payoff_quality"].append(f"short 腿潜在回撤 {downside:.0f}% 可提供对冲收益。")
    if entry_date:
        scores["discipline_fit"] += 8
        evidence["discipline_fit"].append(f"识别到预布局日期 {entry_date}，便于事前纪律化。")
    if legs and len(legs) >= 2:
        scores["discipline_fit"] += 6
        evidence["discipline_fit"].append("配对腿天然要求按相对强弱执行，减少单边情绪交易。")

    model_key = _score_market_model(legs)
    model = (score_models or _default_score_models()).get(model_key) or _default_score_models()[model_key]
    scores = {k: min(100, round(float(v), 1)) for k, v in scores.items()}
    weights = model["weights"]
    overall = round(sum(scores[k] * weights[k] for k in SCORE_DIMENSIONS), 1)
    evidence["data_reliability"].append("仍需用官方财报、交易所价格和期权链验证原文数字。")
    evidence["discipline_fit"].append("未满足右侧确认前保持 watching，不直接升为 open。")
    return {
        **scores,
        "overall": overall,
        "grade": "A" if overall >= 80 else "B" if overall >= 65 else "C",
        "model": model_key,
        "model_label": model["label"],
        "weights": weights,
        "evidence": evidence,
        "summary": (
            f"{model['label']}下，逻辑和赔率具备跟踪价值，但仍需用财报口径、相对强弱和期权链流动性验证。"
            if legs
            else f"{model['label']}下，思路可记录，但当前可回测条件不足，应先补齐标的、价格和催化数据。"
        ),
    }


def build_trade_idea_plan(
    raw_text: str,
    title: str = "",
    source: str = "manual",
    score_models: dict[str, dict] | None = None,
) -> dict:
    text = raw_text or ""
    idea_title = title.strip() or _first_title(text)
    event_date = _iso_month_day(text)
    entry_date = ""
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日[^。\n]{0,20}(?:布局|建仓|卡点)", text)
    if m:
        try:
            entry_date = date(date.today().year, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            entry_date = ""

    legs, metrics = _detect_memory_pair(text)
    strategy_type = "pair_options" if legs else "event_options"
    thesis = (
        "押注同一 AI 存储链内部估值再平衡：做多估值折价且 HBM 弹性更强的腿，"
        "用美光空头/看跌结构对冲行业 beta 和已交易过的估值溢价。"
        if legs
        else "事件驱动交易思路，等待价格、成交量和相对强弱确认后再进入。"
    )

    plan = {
        "summary": "先记录为观察单，只有相对强弱和催化日期同时确认后才升为 ready。",
        "scorecard": _score_trade_idea(
            legs=legs,
            metrics=metrics,
            event_date=event_date,
            entry_date=entry_date,
            score_models=score_models,
        ),
        "reasoning_steps": [
            "先确认交易不是单边预测，而是同一产业链内部的相对定价错误。",
            "再拆分利润弹性、估值差和市场流动性三个变量，避免只被故事吸引。",
            "最后用相对强弱和成交量作为右侧确认，未确认前只保留观察单。",
        ],
        "learning_notes": [
            "配对交易的核心不是判断两家公司绝对涨跌，而是判断 long 腿能否持续跑赢 short 腿。",
            "估值差不是天然会收敛；必须出现催化、资金迁移或预期修正，折价才可能被重新定价。",
            "期权表达要先控制最大亏损，再谈赔率；裸买短期期权容易被时间价值磨损。",
        ],
        "construction": [
            "优先用配对仓位表达：long 腿名义金额 ≈ short 腿名义金额，单笔组合风险控制在账户 1%-2%。",
            "若两腿期权链可用：long 腿用 30-60D call spread，short 腿用 30-60D put spread，避免裸卖期权。",
            "若 long 腿没有可交易期权：使用股票/ADR/可交易载体做多，MU 用 put spread 或小仓位卖空对冲。",
        ],
        "time_plan": [
            f"观察窗口：现在至 {event_date or '催化日前'}，只记录不追涨。",
            f"预布局：{entry_date or '催化前 1 个交易日'} 若 long/short 相对强弱转正，可先建 30%-40%。",
            f"确认加仓：{event_date or '催化日'} 后 long 腿放量突破且 MU 情绪退潮，再补至计划仓位。",
        ],
        "entry_triggers": [
            "long 腿连续 2 个交易日跑赢 MU，或 5 日相对强弱斜率转正。",
            "long 腿突破 5/20 日均线且成交量大于 20 日均量 1.3 倍。",
            "市场开始出现 HBM 份额、AI 存储利润弹性、估值折价收敛的二次传播。",
            "MU 财报后跳空情绪退潮，不能继续创新高或跌回跳空区间。",
        ],
        "exit_rules": [
            "价差收益达到 20%-30% 先减半，达到 40%+ 或估值差明显收敛后退出。",
            "组合亏损达到总敞口 6%-8%，或 long 腿连续 3 日弱于 MU，退出。",
            "若出现韩国半导体出口/采购限制、HBM 订单证伪、财报口径不一致，立即降级。",
        ],
        "option_checks": [
            "只选择 bid/ask spread < 8%-10%、成交量和 OI 足够的合约。",
            "call spread/put spread 到期尽量覆盖事件后 2-4 周，避免只买极短期期权。",
            "单腿最大权利金亏损必须预先固定；组合净 delta 不要过度暴露行业 beta。",
        ],
        "review_template": [
            "入场时 long/short 5日与20日相对强弱是否转正？",
            "原始估值差、利润弹性、HBM 份额假设哪一项被验证或证伪？",
            "实际执行是否追涨、是否按计划分批、是否遵守组合止损？",
            "交易结束后，收益来自估值收敛、行业 beta，还是运气暴露？",
        ],
    }
    catalysts = [
        "海力士上市/可交易载体出现或流动性改善",
        "HBM 订单、份额、ASP 或产能能见度更新",
        "美光财报后跳空缺口回补或估值扩张动能衰减",
        "市场叙事从单一美股 AI 硬件溢价扩散到亚洲存储龙头",
    ]
    risk_checks = [
        "先核对财报口径、币种、拆股/股价单位，避免把传闻数字直接当作估值输入。",
        "韩国市场折价可能长期存在，不能只靠 PE 差做满仓套利。",
        "监管/采购限制会破坏 long 海力士逻辑。",
        "配对交易可能两边同向上涨或同向下跌，需看相对强弱而非单边涨跌。",
    ]
    data_sources = [
        {"name": "Micron Investor Relations", "use": "财报、指引、HBM 收入和毛利口径"},
        {"name": "SK Hynix Investor Relations", "use": "季度财报、HBM 份额、产能和订单能见度"},
        {"name": "SEC EDGAR / Nasdaq", "use": "MU 披露、期权链、成交量、IV"},
        {"name": "KRX / 韩国交易所", "use": "海力士价格、成交量、公告"},
        {"name": "PanWatch 授权新闻网关", "use": "wallstreetcn、jin10、tradingview、investing、futu 新闻催化"},
    ]
    return {
        "title": idea_title,
        "source": source,
        "raw_text": text,
        "thesis": thesis,
        "strategy_type": strategy_type,
        "status": "watching",
        "conviction": "medium",
        "event_date": event_date,
        "entry_start": entry_date,
        "entry_end": event_date or entry_date,
        "legs": legs,
        "plan": plan,
        "catalysts": catalysts,
        "risk_checks": risk_checks,
        "data_sources": data_sources,
        "metrics": metrics,
    }


def _serialize(row: TradeIdea) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "source": row.source,
        "raw_text": row.raw_text,
        "thesis": row.thesis,
        "strategy_type": row.strategy_type,
        "status": row.status,
        "conviction": row.conviction,
        "event_date": row.event_date,
        "entry_start": row.entry_start,
        "entry_end": row.entry_end,
        "legs": row.legs or [],
        "plan": row.plan or {},
        "catalysts": row.catalysts or [],
        "risk_checks": row.risk_checks or [],
        "data_sources": row.data_sources or [],
        "metrics": row.metrics or {},
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _serialize_score_model(row: TradeIdeaScoreModel) -> dict:
    return {
        "id": row.id,
        "model_key": row.model_key,
        "label": row.label,
        "weights": _normalize_weights(row.weights or {}),
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.get("/score-models")
def list_score_models():
    db = SessionLocal()
    try:
        rows = ensure_score_models(db)
        return {"items": [_serialize_score_model(x) for x in rows]}
    finally:
        db.close()


@router.put("/score-models/{model_key}")
def update_score_model(model_key: str, data: ScoreModelUpdate):
    key = (model_key or "").strip().upper()
    defaults = _default_score_models()
    if key not in defaults:
        raise HTTPException(status_code=404, detail="评分模型不存在")
    db = SessionLocal()
    try:
        ensure_score_models(db)
        row = (
            db.query(TradeIdeaScoreModel)
            .filter(TradeIdeaScoreModel.model_key == key)
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="评分模型不存在")
        row.label = data.label.strip() or defaults[key]["label"]
        row.weights = _normalize_weights(data.weights)
        row.enabled = bool(data.enabled)
        db.commit()
        db.refresh(row)
        return _serialize_score_model(row)
    finally:
        db.close()


@router.get("")
def list_trade_ideas(status: str = Query("active"), limit: int = Query(30, ge=1, le=200)):
    db = SessionLocal()
    try:
        q = db.query(TradeIdea)
        if status == "active":
            q = q.filter(TradeIdea.status.in_(("watching", "ready", "open")))
        elif status != "all":
            q = q.filter(TradeIdea.status == status)
        rows = q.order_by(TradeIdea.created_at.desc(), TradeIdea.id.desc()).limit(limit).all()
        return {"items": [_serialize(x) for x in rows]}
    finally:
        db.close()


@router.post("")
def create_trade_idea(data: TradeIdeaCreate):
    db = SessionLocal()
    try:
        ensure_score_models(db)
        parsed = build_trade_idea_plan(
            data.raw_text,
            data.title,
            data.source,
            score_models=_load_score_models(db),
        )
        row = TradeIdea(**to_jsonable(parsed))
        db.add(row)
        db.commit()
        db.refresh(row)
        return _serialize(row)
    finally:
        db.close()


@router.get("/{idea_id}")
def get_trade_idea(idea_id: int):
    db = SessionLocal()
    try:
        row = db.query(TradeIdea).filter(TradeIdea.id == idea_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="交易思路不存在")
        return _serialize(row)
    finally:
        db.close()


@router.put("/{idea_id}/status")
def update_trade_idea_status(idea_id: int, data: TradeIdeaStatusUpdate):
    db = SessionLocal()
    try:
        row = db.query(TradeIdea).filter(TradeIdea.id == idea_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="交易思路不存在")
        row.status = data.status
        db.commit()
        db.refresh(row)
        return _serialize(row)
    finally:
        db.close()
