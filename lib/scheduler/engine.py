"""定时任务 — APScheduler 调度引擎封装。

依赖:APScheduler(trigger 调度) + store(持久化) + executor(到点执行)。
**不依赖具体工具** —— 到点什么工具由 TaskSpec.tool 决定,执行交给 executor。
"""

from __future__ import annotations

import contextlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from lib.scheduler.executor import run_task
from lib.scheduler.store import TaskSpec, TaskStore
from shared.logging import get_logger

logger = get_logger(__name__)

_DURATION_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


def parse_duration(s: str) -> int:
    """'30s' / '5m' / '2h' / '1h30m' → 秒。"""
    m = _DURATION_RE.match(s.strip())
    if not m or not any(m.groups()):
        raise ValueError(f"无效的间隔: {s!r},示例 '30s'/'5m'/'2h'")
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _parse_when_at(s: str) -> datetime:
    """'14:30'(今天没到则明天) 或 ISO datetime。naive 本地时间。"""
    s = s.strip()
    if re.match(r"^\d{1,2}:\d{2}$", s):
        now = datetime.now()
        t = datetime.strptime(s, "%H:%M").time()
        dt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"无法解析时间: {s!r},示例 '14:30' 或 ISO datetime")


def build_trigger(trigger: str, when: str):
    """trigger + when → APScheduler Trigger。"""
    if trigger == "interval":
        return IntervalTrigger(seconds=parse_duration(when))
    if trigger == "cron":
        return CronTrigger.from_crontab(when)
    if trigger == "date":
        return DateTrigger(run_date=_parse_when_at(when))
    raise ValueError(f"未知 trigger: {trigger!r},须为 interval/cron/date")


class SchedulerEngine:
    """APScheduler 封装:add/remove/list 任务 + 持久化恢复。"""

    def __init__(self, store: TaskStore, bus=None) -> None:
        self._store = store
        # bus 不再需要:notify 动作经送达层(lib/proactive/delivery.py)自取 get_bus() 单例。
        # 保留参数仅为向后兼容(startup 仍传 bus),实际忽略。
        self._scheduler = AsyncIOScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        )

    async def start(self) -> None:
        # 从持久化恢复所有任务
        tasks = self._store.list_all()
        for task in tasks:
            if task.enabled:
                self._add_to_scheduler(task)
        self._scheduler.start()
        logger.info("SchedulerEngine started,恢复 %d 个任务", len(tasks))

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("SchedulerEngine stopped")

    def add_task(
        self,
        *,
        trigger: str,
        when: str,
        tool: str,
        args: dict[str, Any] | None = None,
        name: str | None = None,
        notify: bool = True,
    ) -> TaskSpec:
        task = TaskSpec(
            id=f"sched_{uuid.uuid4().hex[:10]}",
            trigger=trigger,
            when=when,
            tool=tool,
            args=args or {},
            name=name,
            created_at=datetime.now(UTC).isoformat(),
            notify=notify,
        )
        self._add_to_scheduler(task)
        self._store.upsert(task)
        logger.info(
            "任务已添加 [%s] tool=%s trigger=%s notify=%s",
            task.id,
            tool,
            trigger,
            notify,
        )
        return task

    def remove_task(self, task_id: str) -> bool:
        # 调度器里没有(可能还没 start)则忽略,只清持久化
        with contextlib.suppress(Exception):
            self._scheduler.remove_job(task_id)
        removed = self._store.remove(task_id)
        if removed:
            logger.info("任务已删除 [%s]", task_id)
        return removed

    def list_tasks(self) -> list[TaskSpec]:
        return self._store.list_all()

    def _add_to_scheduler(self, task: TaskSpec) -> None:
        self._scheduler.add_job(
            run_task,
            build_trigger(task.trigger, task.when),
            id=task.id,
            args=[task.id, task.tool, task.args],
            kwargs={"task": task},
            replace_existing=True,
        )
