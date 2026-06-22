"""Lumen 主动行动 — 时间触发模块。

与 lib/triggers(事件触发)对称,两者构成「主动行动能力」家族,统一概念模型:

        触发源              动作
  ┌──────────────┐    ┌─────────────────────┐
  │ 时间(本模块)  │    │ silent  → 跑工具,写日志 |
  │ 事件(triggers)│    │ notify  → 注入对话,agent 经 Telegram 主动找你│
  └──────────────┘    └─────────────────────┘

本模块触发源=时间(APScheduler);动作双模式:notify=True 跑完主动告诉用户,
notify=False(默认)后台静默。notify 动作与 triggers 共享同一条
「注入 InboundMessage → AgentRunner → OutboundMessage → Telegram 推送」路径。

分层:
  store     — 任务元数据持久化(JSON),含 notify_* 目标字段
  executor  — 到点执行(调 ToolRegistry + 写结果 + 可选 notify 注入)
  engine    — APScheduler 调度封装(持有 MessageBus 供 notify 用)
  tools     — agent 工具三件套(schedule/list/cancel)

用法(startup):
    from lib.scheduler import get_scheduler_engine
    engine = get_scheduler_engine(bus)   # 注入 MessageBus(notify 动作需要)
    await engine.start()                 # 启动(恢复持久化任务)
    await engine.stop()                  # 关闭

agent 侧用 schedule / list_schedules / cancel_schedule 工具。
"""

from __future__ import annotations

import os
from pathlib import Path

from lib.scheduler.engine import SchedulerEngine
from lib.scheduler.store import TaskSpec, TaskStore

__all__ = [
    "SchedulerEngine",
    "TaskSpec",
    "TaskStore",
    "get_engine",
    "get_scheduler_engine",
]

_engine: SchedulerEngine | None = None


def get_scheduler_engine(bus=None) -> SchedulerEngine:
    """全局单例。bus 参数已废弃(notify 动作经送达层自取 get_bus() 单例),保留仅为向后兼容。

    命名为 get_scheduler_engine 以避免与 core.db.get_engine(SQLAlchemy)冲突。
    保留 get_engine 别名仅为向后兼容,新代码请用 get_scheduler_engine。
    """
    global _engine
    if _engine is None:
        home = Path(os.environ.get("LUMEN_HOME", str(Path.home() / ".lumen")))
        _engine = SchedulerEngine(TaskStore(home / "scheduler" / "jobs.json"))
    return _engine


# 向后兼容别名(不推荐使用)
get_engine = get_scheduler_engine
