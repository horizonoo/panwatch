from src.web.api.trade_ideas import build_trade_idea_plan, _default_score_models, _normalize_weights


def test_build_trade_idea_plan_detects_hynix_micron_pair() -> None:
    """交易思路解析 — 海力士/美光长文生成配对期权计划。"""
    raw = """
一笔性感交易即将到来！
美光Forward PE大约11.7倍，而海力士只有5.15倍左右，两者估值相差56%。
做多海力士+做空美光科技，这是海力士上市后的套利交易。
7月10日，海力士登陆资本市场，你可以在7月9日精准的卡点布局。
假如海力士上涨55%，美光下跌10%，价差收益约65%。
"""

    idea = build_trade_idea_plan(raw)

    assert idea["strategy_type"] == "pair_options"
    assert idea["event_date"].endswith("-07-10")
    assert idea["entry_start"].endswith("-07-09")
    assert idea["metrics"]["valuation_gap_pct"] == 56
    assert idea["metrics"]["long_target_upside_pct"] == 55

    legs = idea["legs"]
    assert legs[0]["direction"] == "long"
    assert "Hynix" in legs[0]["name"]
    assert legs[1]["direction"] == "short"
    assert legs[1]["symbol"] == "MU"
    assert any("call spread" in x for x in idea["plan"]["construction"])
    assert any("Micron Investor Relations" == x["name"] for x in idea["data_sources"])
    assert idea["plan"]["scorecard"]["overall"] >= 65
    assert idea["plan"]["scorecard"]["grade"] in {"A", "B"}
    assert idea["plan"]["scorecard"]["model"] == "PAIR"
    assert idea["plan"]["scorecard"]["model_label"] == "跨市场配对模型"
    assert idea["plan"]["scorecard"]["weights"]["logic_strength"] > 0
    assert any("估值差" in x for x in idea["plan"]["scorecard"]["evidence"]["logic_strength"])
    assert len(idea["plan"]["reasoning_steps"]) >= 3
    assert any("配对交易" in x for x in idea["plan"]["learning_notes"])
    assert any("是否按计划分批" in x for x in idea["plan"]["review_template"])


def test_score_model_weights_are_normalized() -> None:
    """交易思路评分模型 — 权重保存前归一化。"""
    weights = _normalize_weights({"logic_strength": 3, "discipline_fit": 1})
    assert round(sum(weights.values()), 4) == 1
    assert weights["logic_strength"] == 0.75
    assert weights["discipline_fit"] == 0.25


def test_custom_pair_score_model_changes_overall_score() -> None:
    """交易思路评分模型 — 自定义权重会影响总分。"""
    raw = """
美光Forward PE大约11.7倍，而海力士只有5.15倍左右，两者估值相差56%。
做多海力士+做空美光科技。7月10日催化，7月9日布局。
"""
    base = build_trade_idea_plan(raw)
    custom_models = _default_score_models()
    custom_models["PAIR"] = {
        **custom_models["PAIR"],
        "weights": _normalize_weights({
            "logic_strength": 0,
            "catalyst_strength": 0,
            "data_reliability": 1,
            "payoff_quality": 0,
            "discipline_fit": 0,
        }),
    }
    custom = build_trade_idea_plan(raw, score_models=custom_models)

    assert custom["plan"]["scorecard"]["overall"] == custom["plan"]["scorecard"]["data_reliability"]
    assert custom["plan"]["scorecard"]["overall"] != base["plan"]["scorecard"]["overall"]
