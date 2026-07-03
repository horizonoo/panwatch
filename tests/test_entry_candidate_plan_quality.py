from src.core.entry_candidates import _plan_quality, _sanitize_plan


def test_sanitize_plan_swaps_entry_range() -> None:
    """交易计划清洗 — 入场区间上下限反了会自动交换。"""
    plan = _sanitize_plan(
        "buy",
        {"entry_low": 11, "entry_high": 10, "stop_loss": 9, "target_price": 13},
    )
    assert plan["entry_low"] == 10
    assert plan["entry_high"] == 11
    assert "入场区间上下限已自动交换" in plan["warnings"]


def test_sanitize_plan_removes_invalid_buy_levels() -> None:
    """交易计划清洗 — 买入计划中无效止损/目标价不计入。"""
    plan = _sanitize_plan(
        "buy",
        {"entry_low": 10, "entry_high": 11, "stop_loss": 10.5, "target_price": 10.8},
    )
    assert plan["stop_loss"] is None
    assert plan["target_price"] is None
    assert _plan_quality(plan) == 30


def test_plan_quality_scores_valid_risk_reward_levels() -> None:
    """交易计划质量 — 合理入场/止损/目标/失效条件可拿满分。"""
    plan = {
        "entry_low": 10,
        "entry_high": 10.5,
        "stop_loss": 9.5,
        "target_price": 12,
        "invalidation": "跌破 9.5 失效",
    }
    assert _plan_quality(plan) == 100
