"""真实账户交易记录 API"""

import time
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.web.database import SessionLocal
from src.web.models import RealTradeLot, RealTradeSell

router = APIRouter()

# ---------- 汇率缓存（5 分钟） ----------
_fx_cache: dict = {"rates": {}, "ts": 0.0}
_FX_TTL = 300  # seconds


def _fetch_fx_rates() -> dict:
    """从 open.er-api.com 拉取以 USD 为基准的汇率，返回 {CNY, HKD, USD}"""
    import httpx, os
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or None
    try:
        client = httpx.Client(proxy=proxy, timeout=8)
        r = client.get("https://open.er-api.com/v6/latest/USD")
        data = r.json()
        rates = data.get("rates", {})
        return {
            "USD": 1.0,
            "CNY": float(rates.get("CNY", 7.25)),
            "HKD": float(rates.get("HKD", 7.83)),
        }
    except Exception:
        return {"USD": 1.0, "CNY": 7.25, "HKD": 7.83}


def get_fx_rates() -> dict:
    now = time.time()
    if now - _fx_cache["ts"] > _FX_TTL or not _fx_cache["rates"]:
        _fx_cache["rates"] = _fetch_fx_rates()
        _fx_cache["ts"] = now
    return _fx_cache["rates"]


@router.get("/fx-rates")
def fx_rates():
    """返回 USD 基准汇率 + 常用换算对"""
    rates = get_fx_rates()
    usd_cny = rates["CNY"]
    usd_hkd = rates["HKD"]
    return {
        "base": "USD",
        "rates": rates,
        "pairs": {
            "USD_CNY": round(usd_cny, 4),
            "USD_HKD": round(usd_hkd, 4),
            "HKD_CNY": round(usd_cny / usd_hkd, 4),
            "CNY_USD": round(1 / usd_cny, 6),
            "HKD_USD": round(1 / usd_hkd, 6),
            "CNY_HKD": round(usd_hkd / usd_cny, 4),
        },
        "updated_at": datetime.utcnow().isoformat(),
    }


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Pydantic schemas ----------

class LotCreate(BaseModel):
    stock_symbol: str
    stock_market: str = "CN"
    stock_name: str = ""
    quantity: float
    buy_price: float
    commission: float = 0.0
    bought_at: datetime
    note: str = ""


class LotUpdate(BaseModel):
    stock_name: Optional[str] = None
    quantity: Optional[float] = None
    buy_price: Optional[float] = None
    commission: Optional[float] = None
    bought_at: Optional[datetime] = None
    note: Optional[str] = None


class SellCreate(BaseModel):
    lot_id: int
    quantity: float
    sell_price: float
    sell_currency: str = ""
    commission: float = 0.0
    sold_at: datetime
    note: str = ""


class SellUpdate(BaseModel):
    quantity: Optional[float] = None
    sell_price: Optional[float] = None
    sell_currency: Optional[str] = None
    commission: Optional[float] = None
    sold_at: Optional[datetime] = None
    note: Optional[str] = None


# ---------- 内部工具 ----------

def _calc_pnl(lot: RealTradeLot, sell_qty: float, sell_price: float, sell_commission: float):
    cost = lot.buy_price * sell_qty + lot.commission * (sell_qty / lot.quantity) + sell_commission
    revenue = sell_price * sell_qty
    pnl = revenue - cost
    pnl_pct = pnl / cost * 100 if cost else 0.0
    return round(pnl, 4), round(pnl_pct, 4)


def _lot_to_dict(lot: RealTradeLot) -> dict:
    sells = lot.sells or []
    sold_qty = sum(s.quantity for s in sells)
    realized_pnl = sum(s.pnl for s in sells)
    return {
        "id": lot.id,
        "stock_symbol": lot.stock_symbol,
        "stock_market": lot.stock_market,
        "stock_name": lot.stock_name,
        "quantity": lot.quantity,
        "buy_price": lot.buy_price,
        "commission": lot.commission,
        "bought_at": lot.bought_at.isoformat() if lot.bought_at else None,
        "note": lot.note,
        "status": lot.status,
        "remaining_qty": lot.remaining_qty,
        "sold_qty": round(sold_qty, 4),
        "realized_pnl": round(realized_pnl, 4),
        "sell_count": len(sells),
        "created_at": lot.created_at.isoformat() if lot.created_at else None,
    }


_MARKET_CURRENCY = {"CN": "CNY", "HK": "HKD", "US": "USD"}


def _sell_to_dict(sell: RealTradeSell) -> dict:
    return {
        "id": sell.id,
        "lot_id": sell.lot_id,
        "stock_symbol": sell.stock_symbol,
        "stock_market": sell.stock_market,
        "stock_name": sell.stock_name,
        "quantity": sell.quantity,
        "sell_price": sell.sell_price,
        "sell_currency": sell.sell_currency or _MARKET_CURRENCY.get(sell.stock_market, "USD"),
        "commission": sell.commission,
        "sold_at": sell.sold_at.isoformat() if sell.sold_at else None,
        "note": sell.note,
        "pnl": sell.pnl,
        "pnl_pct": sell.pnl_pct,
        "holding_days": sell.holding_days,
        "created_at": sell.created_at.isoformat() if sell.created_at else None,
    }


def _update_lot_status(lot: RealTradeLot):
    sold = sum(s.quantity for s in (lot.sells or []))
    remaining = lot.quantity - sold
    lot.remaining_qty = max(0.0, round(remaining, 4))
    if remaining <= 0:
        lot.status = "closed"
    elif sold > 0:
        lot.status = "partial"
    else:
        lot.status = "open"


# ---------- 买入分仓 CRUD ----------

@router.get("/lots")
def list_lots(
    market: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(RealTradeLot)
    if market:
        q = q.filter(RealTradeLot.stock_market == market)
    if symbol:
        q = q.filter(RealTradeLot.stock_symbol == symbol.upper())
    if status:
        q = q.filter(RealTradeLot.status == status)
    lots = q.order_by(RealTradeLot.bought_at.desc()).all()
    return [_lot_to_dict(lot) for lot in lots]


@router.post("/lots")
def create_lot(body: LotCreate, db: Session = Depends(get_db)):
    lot = RealTradeLot(
        stock_symbol=body.stock_symbol.upper(),
        stock_market=body.stock_market.upper(),
        stock_name=body.stock_name,
        quantity=body.quantity,
        buy_price=body.buy_price,
        commission=body.commission,
        bought_at=body.bought_at,
        note=body.note,
        status="open",
        remaining_qty=body.quantity,
    )
    db.add(lot)
    db.commit()
    db.refresh(lot)
    return _lot_to_dict(lot)


@router.put("/lots/{lot_id}")
def update_lot(lot_id: int, body: LotUpdate, db: Session = Depends(get_db)):
    lot = db.query(RealTradeLot).filter(RealTradeLot.id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="买入记录不存在")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(lot, field, val)
    _update_lot_status(lot)
    db.commit()
    db.refresh(lot)
    return _lot_to_dict(lot)


@router.delete("/lots/{lot_id}")
def delete_lot(lot_id: int, db: Session = Depends(get_db)):
    lot = db.query(RealTradeLot).filter(RealTradeLot.id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="买入记录不存在")
    if lot.sells:
        raise HTTPException(status_code=400, detail="该仓位存在卖出记录，请先删除卖出记录")
    db.delete(lot)
    db.commit()
    return {"ok": True}


# ---------- 卖出记录 CRUD ----------

@router.get("/sells")
def list_sells(
    market: Optional[str] = None,
    symbol: Optional[str] = None,
    lot_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(RealTradeSell)
    if lot_id:
        q = q.filter(RealTradeSell.lot_id == lot_id)
    if market:
        q = q.filter(RealTradeSell.stock_market == market)
    if symbol:
        q = q.filter(RealTradeSell.stock_symbol == symbol.upper())
    sells = q.order_by(RealTradeSell.sold_at.desc()).all()
    return [_sell_to_dict(s) for s in sells]


@router.post("/sells")
def create_sell(body: SellCreate, db: Session = Depends(get_db)):
    lot = db.query(RealTradeLot).filter(RealTradeLot.id == body.lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="买入记录不存在")
    if body.quantity > lot.remaining_qty + 1e-6:
        raise HTTPException(status_code=400, detail=f"卖出数量 {body.quantity} 超过剩余仓位 {lot.remaining_qty}")

    pnl, pnl_pct = _calc_pnl(lot, body.quantity, body.sell_price, body.commission)
    holding_days = (body.sold_at.date() - lot.bought_at.date()).days if lot.bought_at else 0

    sell_currency = body.sell_currency or _MARKET_CURRENCY.get(lot.stock_market, "USD")
    sell = RealTradeSell(
        lot_id=lot.id,
        stock_symbol=lot.stock_symbol,
        stock_market=lot.stock_market,
        stock_name=lot.stock_name,
        quantity=body.quantity,
        sell_price=body.sell_price,
        sell_currency=sell_currency,
        commission=body.commission,
        sold_at=body.sold_at,
        note=body.note,
        pnl=pnl,
        pnl_pct=pnl_pct,
        holding_days=holding_days,
    )
    db.add(sell)
    db.flush()

    # 重新加载 sells 以更新状态
    db.refresh(lot)
    _update_lot_status(lot)
    db.commit()
    db.refresh(sell)
    return _sell_to_dict(sell)


@router.put("/sells/{sell_id}")
def update_sell(sell_id: int, body: SellUpdate, db: Session = Depends(get_db)):
    sell = db.query(RealTradeSell).filter(RealTradeSell.id == sell_id).first()
    if not sell:
        raise HTTPException(status_code=404, detail="卖出记录不存在")
    lot = sell.lot
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(sell, field, val)
    if not sell.sell_currency:
        sell.sell_currency = _MARKET_CURRENCY.get(lot.stock_market, "USD")
    pnl, pnl_pct = _calc_pnl(lot, sell.quantity, sell.sell_price, sell.commission)
    sell.pnl = pnl
    sell.pnl_pct = pnl_pct
    if lot.bought_at and sell.sold_at:
        sell.holding_days = (sell.sold_at.date() - lot.bought_at.date()).days
    db.refresh(lot)
    _update_lot_status(lot)
    db.commit()
    db.refresh(sell)
    return _sell_to_dict(sell)


@router.delete("/sells/{sell_id}")
def delete_sell(sell_id: int, db: Session = Depends(get_db)):
    sell = db.query(RealTradeSell).filter(RealTradeSell.id == sell_id).first()
    if not sell:
        raise HTTPException(status_code=404, detail="卖出记录不存在")
    lot = sell.lot
    db.delete(sell)
    db.flush()
    db.refresh(lot)
    _update_lot_status(lot)
    db.commit()
    return {"ok": True}


# ---------- 统计汇总 ----------

@router.get("/summary")
def get_summary(market: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(RealTradeSell)
    if market:
        q = q.filter(RealTradeSell.stock_market == market)
    sells = q.all()

    total_pnl = sum(s.pnl for s in sells)
    winners = [s for s in sells if s.pnl > 0]
    losers = [s for s in sells if s.pnl < 0]
    win_rate = len(winners) / len(sells) * 100 if sells else 0.0
    avg_pnl_pct = sum(s.pnl_pct for s in sells) / len(sells) if sells else 0.0
    avg_holding = sum(s.holding_days for s in sells) / len(sells) if sells else 0.0

    # 按股票汇总
    by_symbol: dict = {}
    for s in sells:
        key = (s.stock_symbol, s.stock_market, s.stock_name)
        if key not in by_symbol:
            by_symbol[key] = {"pnl": 0.0, "count": 0, "wins": 0}
        by_symbol[key]["pnl"] += s.pnl
        by_symbol[key]["count"] += 1
        if s.pnl > 0:
            by_symbol[key]["wins"] += 1

    by_stock = sorted(
        [
            {
                "symbol": k[0], "market": k[1], "name": k[2],
                "total_pnl": round(v["pnl"], 4),
                "trade_count": v["count"],
                "win_rate": round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0,
            }
            for k, v in by_symbol.items()
        ],
        key=lambda x: x["total_pnl"],
        reverse=True,
    )

    # 持仓中（open/partial）
    open_lots_q = db.query(RealTradeLot).filter(RealTradeLot.status != "closed")
    if market:
        open_lots_q = open_lots_q.filter(RealTradeLot.stock_market == market)
    open_lots = open_lots_q.all()

    return {
        "total_closed_trades": len(sells),
        "total_pnl": round(total_pnl, 4),
        "win_count": len(winners),
        "lose_count": len(losers),
        "win_rate": round(win_rate, 2),
        "avg_pnl_pct": round(avg_pnl_pct, 2),
        "avg_holding_days": round(avg_holding, 1),
        "best_trade": max(sells, key=lambda s: s.pnl_pct, default=None) and _sell_to_dict(max(sells, key=lambda s: s.pnl_pct)),
        "worst_trade": min(sells, key=lambda s: s.pnl_pct, default=None) and _sell_to_dict(min(sells, key=lambda s: s.pnl_pct)),
        "by_stock": by_stock,
        "open_lots_count": len(open_lots),
    }
