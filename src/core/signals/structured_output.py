from __future__ import annotations

import json


ALLOWED_ACTIONS = {
    "buy",
    "add",
    "reduce",
    "sell",
    "hold",
    "watch",
    "alert",
    "avoid",
}

ACTION_ALIASES = {
    "build": "add",
    "long": "buy",
    "accumulate": "add",
    "trim": "reduce",
    "take_profit": "reduce",
    "stop_loss": "sell",
    "wait": "watch",
    "neutral": "hold",
    "ignore": "avoid",
    "买入": "buy",
    "买": "buy",
    "建仓": "buy",
    "准备建仓": "buy",
    "加仓": "add",
    "增持": "add",
    "减仓": "reduce",
    "减持": "reduce",
    "止盈": "reduce",
    "卖出": "sell",
    "卖": "sell",
    "清仓": "sell",
    "止损": "sell",
    "持有": "hold",
    "继续持有": "hold",
    "观望": "watch",
    "关注": "watch",
    "预警": "alert",
    "设置预警": "alert",
    "回避": "avoid",
    "暂时回避": "avoid",
}

ACTION_LABELS = {
    "buy": "买入",
    "add": "加仓",
    "reduce": "减仓",
    "sell": "卖出",
    "hold": "持有",
    "watch": "观望",
    "alert": "设置预警",
    "avoid": "暂时回避",
}


TAG_START = "<!--PANWATCH_JSON-->"
TAG_END = "<!--/PANWATCH_JSON-->"


def normalize_action(action: object) -> str:
    """Normalize LLM action aliases to PanWatch action codes."""
    raw = str(action or "").strip()
    if not raw:
        return ""
    key = raw.lower().replace("-", "_").replace(" ", "_")
    normalized = ACTION_ALIASES.get(key) or ACTION_ALIASES.get(raw) or key
    return normalized if normalized in ALLOWED_ACTIONS else ""


def try_parse_action_json(text: str) -> dict | None:
    """Parse JSON-only output. Returns dict on success."""
    raw = (text or "").strip()
    if not raw:
        return None

    # Allow fenced code blocks (```json ... ```)
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[0].lstrip().startswith("```"):
            if lines[-1].strip().startswith("```"):
                raw = "\n".join(lines[1:-1]).strip()
        else:
            raw = raw.strip("`").strip()
    # Allow "json" prefix line without code fences.
    # Example:
    # json
    # {"action":"buy", ...}
    lines = raw.splitlines()
    if lines and lines[0].strip().lower() == "json":
        raw = "\n".join(lines[1:]).strip()
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    action = normalize_action(obj.get("action"))
    if obj.get("action") and not action:
        return None
    if action:
        obj["action"] = action
        obj.setdefault("action_label", ACTION_LABELS.get(action, action))
    return obj


def try_extract_tagged_json(
    text: str, *, start: str = TAG_START, end: str = TAG_END
) -> dict | None:
    """Extract a tagged JSON object from a larger text.

    Expected format at the end of the response:
    <!--PANWATCH_JSON-->
    { ... }
    <!--/PANWATCH_JSON-->
    """

    raw = text or ""
    i = raw.rfind(start)
    if i < 0:
        return None
    j = raw.rfind(end)
    if j < 0 or j <= i:
        return None
    payload = raw[i + len(start) : j].strip()
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def strip_tagged_json(text: str, *, start: str = TAG_START, end: str = TAG_END) -> str:
    """Remove tagged JSON block from text (if present)."""
    raw = text or ""
    i = raw.rfind(start)
    if i < 0:
        return raw
    j = raw.rfind(end)
    if j < 0 or j <= i:
        return raw
    return (raw[:i] + raw[j + len(end) :]).strip()
