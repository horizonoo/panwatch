from src.core.suggestion_pool import (
    apply_quality_guardrail,
    is_actionable_alert,
    normalize_suggestion_action,
)


def test_normalize_suggestion_action_chinese_alias() -> None:
    """建议池动作归一化 — 中文别名落到标准 action。"""
    action, label = normalize_suggestion_action("止损", "")
    assert action == "sell"
    assert label == "卖出"


def test_normalize_suggestion_action_unknown_defaults_to_watch() -> None:
    """建议池动作归一化 — 未知动作保守降为观望。"""
    action, label = normalize_suggestion_action("moon", "")
    assert action == "watch"
    assert label == "观望"


def test_is_actionable_alert_includes_entry_actions() -> None:
    """建议池提醒标记 — 买入/加仓同样属于可行动作。"""
    assert is_actionable_alert("买入") is True
    assert is_actionable_alert("add") is True
    assert is_actionable_alert("hold") is False
    assert is_actionable_alert("观望") is False


def test_quality_guardrail_downgrades_low_quality_entry() -> None:
    """建议池质量门槛 — 低质量上下文不直接落进攻性建议。"""
    action, label, reason, meta = apply_quality_guardrail(
        "buy",
        "买入",
        "突破压力位",
        {"context_quality_score": 42},
    )
    assert action == "watch"
    assert label == "观望"
    assert "进攻性建议降级为观望" in reason
    assert meta["quality_guardrail"]["from_action"] == "buy"


def test_quality_guardrail_keeps_high_quality_entry() -> None:
    """建议池质量门槛 — 高质量上下文保留进攻性建议。"""
    action, label, reason, meta = apply_quality_guardrail(
        "add",
        "加仓",
        "趋势延续",
        {"context_quality_score": 72},
    )
    assert (action, label, reason) == ("add", "加仓", "趋势延续")
    assert "quality_guardrail" not in meta


def test_quality_guardrail_keeps_low_quality_exit() -> None:
    """建议池质量门槛 — 风控类卖出不被质量门槛拦截。"""
    action, label, reason, meta = apply_quality_guardrail(
        "sell",
        "卖出",
        "跌破止损",
        {"context_quality_score": 35},
    )
    assert (action, label, reason) == ("sell", "卖出", "跌破止损")
    assert "quality_guardrail" not in meta
