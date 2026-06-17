"""定时任务 — 任务元数据持久化(JSON)。

独立模块:不依赖 APScheduler、不依赖工具体系,只管 TaskSpec 的读写。
路径由上层(engine)注入,本模块不假设数据落在哪。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TaskSpec:
    """一个定时任务的元数据(可 JSON 序列化)。"""

    id: str
    trigger: str  # "interval" | "cron" | "date"
    when: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    name: str | None = None
    created_at: str = ""
    enabled: bool = True
    # ── notify 动作(双模式)──
    # 任务只表达"做什么",不绑渠道。notify=True(默认)时到点跑完工具,结果交给送达层
    # (lib/proactive/delivery.py)决定推给谁——单用户第一版走 Telegram。
    # notify=False 则后台静默执行,只写 jsonl。
    notify: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskSpec:
        return cls(
            id=d["id"],
            trigger=d["trigger"],
            when=d["when"],
            tool=d["tool"],
            args=d.get("args", {}),
            name=d.get("name"),
            created_at=d.get("created_at", ""),
            enabled=d.get("enabled", True),
            notify=d.get("notify", True),
        )


class TaskStore:
    """JSON 文件持久化 TaskSpec 列表。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[TaskSpec]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [TaskSpec.from_dict(d) for d in data]
        except Exception as exc:
            logger.warning("任务持久化读取失败: %s", exc)
            return []

    def save(self, tasks: list[TaskSpec]) -> None:
        self.path.write_text(
            json.dumps([t.to_dict() for t in tasks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def upsert(self, task: TaskSpec) -> None:
        tasks = self.load()
        for i, t in enumerate(tasks):
            if t.id == task.id:
                tasks[i] = task
                self.save(tasks)
                return
        tasks.append(task)
        self.save(tasks)

    def remove(self, task_id: str) -> bool:
        tasks = self.load()
        new = [t for t in tasks if t.id != task_id]
        if len(new) == len(tasks):
            return False
        self.save(new)
        return True

    def list_all(self) -> list[TaskSpec]:
        return self.load()
