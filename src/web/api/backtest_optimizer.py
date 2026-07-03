"""回测优化 API —— 触发多轮优化(后台执行) + 查询最新结果。

端点:
  POST /api/backtest-optimizer/run     触发优化(后台线程)
  GET  /api/backtest-optimizer/status  查询运行状态
  GET  /api/backtest-optimizer/latest  获取最新优化报告
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from src.core.backtest.optimizer import run_optimization, load_latest_report

logger = logging.getLogger(__name__)

router = APIRouter()

_state_lock = threading.Lock()
_state = {
    "running": False,
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "progress": "",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set(**kw):
    with _state_lock:
        _state.update(kw)


def _get() -> dict:
    with _state_lock:
        return dict(_state)


def _worker(rounds: int, max_per_market: int, history_days: int, min_trades: int):
    _set(running=True, started_at=_now_iso(), finished_at="", last_error="",
         progress="加载历史数据...")
    try:
        report = run_optimization(
            rounds=rounds, max_per_market=max_per_market,
            history_days=history_days, min_trades=min_trades, persist=True,
        )
        _set(running=False, finished_at=_now_iso(), last_error="",
             progress=f"完成: 加载 {report.bars_loaded} 只，耗时 {report.elapsed_sec}s")
    except Exception as e:
        logger.exception("回测优化失败: %s", e)
        _set(running=False, finished_at=_now_iso(), last_error=str(e), progress="失败")


@router.post("/run")
def trigger_optimization(
    rounds: int = Query(3, ge=1, le=8, description="优化轮次(每轮在上轮最优点邻域细化)"),
    max_per_market: int = Query(40, ge=5, le=100, description="每市场参与回测的股票上限"),
    history_days: int = Query(750, ge=120, le=1500, description="历史K线天数"),
    min_trades: int = Query(8, ge=3, le=50, description="一组参数最少成交笔数(低于则忽略)"),
):
    with _state_lock:
        if _state.get("running"):
            return {"accepted": False, "message": "优化任务正在执行中", **_state}
        _state.update(running=True, started_at=_now_iso(), finished_at="",
                      last_error="", progress="启动中...")
    threading.Thread(
        target=_worker,
        kwargs=dict(rounds=rounds, max_per_market=max_per_market,
                    history_days=history_days, min_trades=min_trades),
        daemon=True, name="backtest-optimizer",
    ).start()
    return {"accepted": True, "message": "已提交后台优化，预计数分钟", **_get()}


@router.get("/status")
def optimization_status():
    return _get()


@router.get("/latest")
def latest_report():
    report = load_latest_report()
    if not report:
        return {"available": False, "report": None}
    return {"available": True, "report": report}
