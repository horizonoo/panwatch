"""交易成本模型 —— 回测(Phase 0)与模拟盘(Phase 1)共用。

A 股成本口径(2023-08-28 印花税下调后):
- 印花税: 卖出单边 0.05%(万 5)
- 佣金: 双边，默认万 2.5，单笔最低 5 元
- 过户费: 双边，成交额 0.001%(十万分之一)
- 滑点: 可配置基点(默认 5bps)

港股成本口径:
- 印花税: 双边 0.1%(千 1)
- 佣金: 双边，默认 0.03%，单笔最低 HKD 50（折 CNY 约 45 元）
- 交易征费(SFC): 双边 0.0027%
- 交易所费: 双边 0.005%
- 结算费: 双边 0.002%
- 滑点: 默认 8bps（港股流动性略低于 A 股）

美股成本口径:
- 印花税: 无
- 佣金: 默认 0（富途/老虎免佣），或按 $0.005/股计
- SEC 费(卖出): 0.00278% of 成交额
- FINRA 费(卖出): $0.000145/股，最高 $7.27
- 平台费: 默认 0（已含在免佣里）
- 滑点: 默认 5bps
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    """A 股成本参数（默认值贴近散户实际）。"""
    commission_rate: float = 0.00025    # 佣金费率(双边) 万 2.5
    min_commission: float = 5.0         # 单笔最低佣金(元)
    stamp_duty_rate: float = 0.0005     # 印花税(仅卖出) 万 5
    transfer_fee_rate: float = 0.00001  # 过户费(双边) 十万分之 1
    slippage_bps: float = 5.0           # 滑点(基点，双边；5bps = 0.05%)


@dataclass(frozen=True)
class HKCostConfig:
    """港股成本参数。"""
    commission_rate: float = 0.0003     # 佣金(双边) 万 3
    min_commission: float = 45.0        # 单笔最低佣金(元，约 HKD 50)
    stamp_duty_rate: float = 0.001      # 印花税(双边) 千 1
    sfc_levy_rate: float = 0.000027     # 交易征费(双边) 0.0027%
    exchange_fee_rate: float = 0.00005  # 交易所费(双边) 0.005%
    settlement_fee_rate: float = 0.00002  # 结算费(双边) 0.002%
    slippage_bps: float = 8.0           # 滑点(bps，港股流动性略低)


@dataclass(frozen=True)
class USCostConfig:
    """美股成本参数（富途/老虎免佣模式）。"""
    commission_rate: float = 0.0        # 佣金(双边) 免佣
    commission_per_share: float = 0.0   # 按股收费 0（免佣）
    sec_fee_rate: float = 0.0000278     # SEC 费(仅卖出) 0.00278%
    finra_fee_per_share: float = 0.000145  # FINRA 费(仅卖出) $0.000145/股
    finra_fee_max: float = 7.27         # FINRA 最高 $7.27
    slippage_bps: float = 5.0           # 滑点(bps)


@dataclass(frozen=True)
class Fill:
    """一次成交的净结果（含成本拆解，便于展示与审计）。"""
    side: str            # "buy" | "sell"
    price: float         # 名义价(信号/行情价，未含滑点)
    fill_price: float    # 实际成交价(含滑点)
    quantity: int
    gross: float         # 实际成交额 = fill_price * quantity
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float  # 滑点损耗
    explicit_fees: float  # 显式规费 = commission + stamp_duty + transfer_fee + 其他
    friction: float       # 总摩擦 = explicit_fees + slippage_cost
    cash_delta: float     # 现金变动：buy 为负，sell 为正


class CostModel:
    """A 股交易成本计算器。"""

    def __init__(self, config: CostConfig | None = None) -> None:
        self.cfg = config or CostConfig()

    def _apply_slippage(self, price: float, side: str) -> float:
        adj = price * self.cfg.slippage_bps / 10000.0
        return price + adj if side == "buy" else max(0.0, price - adj)

    def fill(self, side: str, price: float, quantity: int) -> Fill:
        side = (side or "").strip().lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side 必须是 buy/sell，得到 {side!r}")
        qty = int(quantity)
        if qty <= 0 or price <= 0:
            raise ValueError(f"price/quantity 必须为正，得到 price={price} qty={quantity}")

        fill_price = self._apply_slippage(price, side)
        gross = fill_price * qty
        commission = max(gross * self.cfg.commission_rate, self.cfg.min_commission)
        stamp_duty = gross * self.cfg.stamp_duty_rate if side == "sell" else 0.0
        transfer_fee = gross * self.cfg.transfer_fee_rate
        slippage_cost = abs(fill_price - price) * qty
        explicit_fees = commission + stamp_duty + transfer_fee
        cash_delta = -(gross + explicit_fees) if side == "buy" else gross - explicit_fees

        return Fill(
            side=side, price=float(price), fill_price=round(fill_price, 6),
            quantity=qty, gross=round(gross, 4), commission=round(commission, 4),
            stamp_duty=round(stamp_duty, 4), transfer_fee=round(transfer_fee, 4),
            slippage_cost=round(slippage_cost, 4), explicit_fees=round(explicit_fees, 4),
            friction=round(explicit_fees + slippage_cost, 4), cash_delta=round(cash_delta, 4),
        )

    def round_trip_pnl(self, entry_price: float, exit_price: float, quantity: int) -> dict:
        buy = self.fill("buy", entry_price, quantity)
        sell = self.fill("sell", exit_price, quantity)
        invested = -buy.cash_delta
        proceeds = sell.cash_delta
        pnl = proceeds - invested
        pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
        return {
            "entry_price": float(entry_price), "exit_price": float(exit_price),
            "quantity": int(quantity), "invested": round(invested, 4),
            "proceeds": round(proceeds, 4), "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "total_cost": round(buy.friction + sell.friction, 4),
            "buy": buy, "sell": sell,
        }


class HKCostModel:
    """港股交易成本计算器。"""

    def __init__(self, config: HKCostConfig | None = None) -> None:
        self.cfg = config or HKCostConfig()

    def _apply_slippage(self, price: float, side: str) -> float:
        adj = price * self.cfg.slippage_bps / 10000.0
        return price + adj if side == "buy" else max(0.0, price - adj)

    def fill(self, side: str, price: float, quantity: int) -> Fill:
        side = (side or "").strip().lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side 必须是 buy/sell，得到 {side!r}")
        qty = int(quantity)
        if qty <= 0 or price <= 0:
            raise ValueError(f"price/quantity 必须为正")

        fill_price = self._apply_slippage(price, side)
        gross = fill_price * qty
        commission = max(gross * self.cfg.commission_rate, self.cfg.min_commission)
        stamp_duty = gross * self.cfg.stamp_duty_rate          # 双边
        sfc_levy = gross * self.cfg.sfc_levy_rate              # 双边
        exchange_fee = gross * self.cfg.exchange_fee_rate      # 双边
        settlement_fee = gross * self.cfg.settlement_fee_rate  # 双边
        slippage_cost = abs(fill_price - price) * qty
        explicit_fees = commission + stamp_duty + sfc_levy + exchange_fee + settlement_fee
        cash_delta = -(gross + explicit_fees) if side == "buy" else gross - explicit_fees

        return Fill(
            side=side, price=float(price), fill_price=round(fill_price, 6),
            quantity=qty, gross=round(gross, 4), commission=round(commission, 4),
            stamp_duty=round(stamp_duty, 4),
            transfer_fee=round(sfc_levy + exchange_fee + settlement_fee, 4),
            slippage_cost=round(slippage_cost, 4), explicit_fees=round(explicit_fees, 4),
            friction=round(explicit_fees + slippage_cost, 4), cash_delta=round(cash_delta, 4),
        )

    def round_trip_pnl(self, entry_price: float, exit_price: float, quantity: int) -> dict:
        buy = self.fill("buy", entry_price, quantity)
        sell = self.fill("sell", exit_price, quantity)
        invested = -buy.cash_delta
        proceeds = sell.cash_delta
        pnl = proceeds - invested
        pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
        return {
            "entry_price": float(entry_price), "exit_price": float(exit_price),
            "quantity": int(quantity), "invested": round(invested, 4),
            "proceeds": round(proceeds, 4), "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "total_cost": round(buy.friction + sell.friction, 4),
            "buy": buy, "sell": sell,
        }


class USCostModel:
    """美股交易成本计算器（富途/老虎免佣模式）。"""

    def __init__(self, config: USCostConfig | None = None) -> None:
        self.cfg = config or USCostConfig()

    def _apply_slippage(self, price: float, side: str) -> float:
        adj = price * self.cfg.slippage_bps / 10000.0
        return price + adj if side == "buy" else max(0.0, price - adj)

    def fill(self, side: str, price: float, quantity: int) -> Fill:
        side = (side or "").strip().lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side 必须是 buy/sell，得到 {side!r}")
        qty = int(quantity)
        if qty <= 0 or price <= 0:
            raise ValueError(f"price/quantity 必须为正")

        fill_price = self._apply_slippage(price, side)
        gross = fill_price * qty
        commission = gross * self.cfg.commission_rate + self.cfg.commission_per_share * qty
        # SEC 费和 FINRA 费仅卖出时收取
        sec_fee = gross * self.cfg.sec_fee_rate if side == "sell" else 0.0
        finra_fee = min(self.cfg.finra_fee_per_share * qty, self.cfg.finra_fee_max) if side == "sell" else 0.0
        stamp_duty = sec_fee  # 复用 stamp_duty 字段存 SEC 费
        transfer_fee = finra_fee  # 复用 transfer_fee 字段存 FINRA 费
        slippage_cost = abs(fill_price - price) * qty
        explicit_fees = commission + sec_fee + finra_fee
        cash_delta = -(gross + explicit_fees) if side == "buy" else gross - explicit_fees

        return Fill(
            side=side, price=float(price), fill_price=round(fill_price, 6),
            quantity=qty, gross=round(gross, 4), commission=round(commission, 4),
            stamp_duty=round(stamp_duty, 4), transfer_fee=round(transfer_fee, 4),
            slippage_cost=round(slippage_cost, 4), explicit_fees=round(explicit_fees, 4),
            friction=round(explicit_fees + slippage_cost, 4), cash_delta=round(cash_delta, 4),
        )

    def round_trip_pnl(self, entry_price: float, exit_price: float, quantity: int) -> dict:
        buy = self.fill("buy", entry_price, quantity)
        sell = self.fill("sell", exit_price, quantity)
        invested = -buy.cash_delta
        proceeds = sell.cash_delta
        pnl = proceeds - invested
        pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
        return {
            "entry_price": float(entry_price), "exit_price": float(exit_price),
            "quantity": int(quantity), "invested": round(invested, 4),
            "proceeds": round(proceeds, 4), "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "total_cost": round(buy.friction + sell.friction, 4),
            "buy": buy, "sell": sell,
        }


def get_cost_model(market: str) -> "CostModel | HKCostModel | USCostModel":
    """根据市场返回对应的成本模型。"""
    m = (market or "").strip().upper()
    if m == "HK":
        return HKCostModel()
    if m == "US":
        return USCostModel()
    return CostModel()


# 全局默认实例
DEFAULT_COST_MODEL = CostModel()
DEFAULT_HK_COST_MODEL = HKCostModel()
DEFAULT_US_COST_MODEL = USCostModel()
