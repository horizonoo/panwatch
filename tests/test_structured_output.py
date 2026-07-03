from src.core.signals.structured_output import normalize_action, try_parse_action_json


def test_try_parse_action_json_plain_json_prefix() -> None:
    """LLM 输出解析 — json 前缀格式"""
    text = '\njson\n{"action":"add","action_label":"建仓","reason":"突破"}\n'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "add"
    assert obj.get("action_label") == "建仓"


def test_try_parse_action_json_fenced_json() -> None:
    """LLM 输出解析 — 代码块格式"""
    text = '```json\n{"action":"reduce","action_label":"减仓"}\n```'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "reduce"


def test_try_parse_action_json_action_alias_build_to_add() -> None:
    """LLM 输出解析 — build 别名自动映射为 add"""
    text = '\njson\n{"action":"build","action_label":"建仓","reason":"突破"}\n'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "add"


def test_try_parse_action_json_chinese_action_alias() -> None:
    """LLM 输出解析 — 中文动作自动映射为标准 action"""
    text = '{"action":"减持","reason":"跌破支撑"}'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "reduce"
    assert obj.get("action_label") == "减仓"


def test_normalize_action_common_aliases() -> None:
    """LLM 输出解析 — 常见英文别名归一化"""
    assert normalize_action("take-profit") == "reduce"
    assert normalize_action("stop loss") == "sell"
    assert normalize_action("neutral") == "hold"


def test_try_parse_action_json_rejects_unknown_action() -> None:
    """LLM 输出解析 — 未知动作拒绝进入结构化结果"""
    assert try_parse_action_json('{"action":"moon","reason":"情绪"}') is None
