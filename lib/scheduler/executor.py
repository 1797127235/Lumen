"""定时任务 — 到点执行器。

到点调用一个工具(通过 ToolRegistry,任意已注册工具含 MCP),
结果追加写到 jsonl。本模块只认 ToolRegistry 抽象,**不 import 任何具体工具**
(不知道 web_search 是什么,只按名字从 registry 取)。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.logging import get_logger

if TYPE_CHECKING:
    from lib.scheduler.store import TaskSpec

logger = get_logger(__name__)


def _results_dir() -> Path:
    """结果目录:`~/.lumen/scheduler/results/`。"""
    home = Path(os.environ.get("LUMEN_HOME", str(Path.home() / ".lumen")))
    d = home / "scheduler" / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:64] or "task"


def _append_result(task_id: str, record: dict[str, Any]) -> None:
    path = _results_dir() / f"{_safe(task_id)}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def run_task(
    task_id: str,
    tool: str,
    args: dict[str, Any],
    *,
    task: TaskSpec | None = None,
) -> str:
    """到点:调 registry.execute(tool, args) + 结果写 jsonl。

    作为 APScheduler 的 job func。返回值用于日志/APScheduler 记录。

    notify 动作:若 task.notify(默认 True),把结果交给送达层(delivery.py)主动推给用户。
    送达层决定推哪个渠道(单用户第一版 Telegram),任务本身不绑渠道。
    notify=False 则只写 jsonl,后台静默执行。
    """
    from lib.tools._registry import get_tool_registry

    registry = get_tool_registry()
    ran_at = datetime.now(UTC).isoformat()

    try:
        result = await registry.execute(tool, args)
    except Exception as exc:
        logger.error("定时任务执行失败 [%s] tool=%s: %s", task_id, tool, exc)
        _append_result(
            task_id,
            {
                "task_id": task_id,
                "tool": tool,
                "args": args,
                "ran_at": ran_at,
                "ok": False,
                "error": str(exc),
            },
        )
        return f"❌ {tool} 执行失败: {exc}"

    _append_result(
        task_id,
        {
            "task_id": task_id,
            "tool": tool,
            "args": args,
            "ran_at": ran_at,
            "ok": True,
            "result": str(result)[:5000],
        },
    )
    logger.info("定时任务执行完成 [%s] tool=%s", task_id, tool)

    # notify 动作:结果交给送达层,任务不绑渠道
    if task is not None and task.notify:
        await _notify(task, result)

    return str(result)


async def _notify(task: TaskSpec, result: Any) -> None:
    """把工具结果交给送达层主动推给用户。

    任务只负责"做了什么";推给谁、推哪个渠道由送达层(delivery.py)决定。
    """
    from lib.proactive.delivery import deliver

    label = task.name or task.id
    summary = str(result)[:800]
    content = (
        f"【定时任务「{label}」完成】帮你执行了 {task.tool}。\n\n"
        f"结果摘要:\n{summary}\n\n"
        f"请基于以上结果,像伙伴一样主动告诉我重点和你的看法。"
    )
    try:
        delivered = await deliver(
            user_id="",  # 任务不存 user;送达层用 get_user_id() 兜底 sender(身份常量)
            content=content,
            source="scheduler",
            source_id=task.id,
        )
        if delivered:
            logger.info("定时任务结果已送达 [%s] channels=%s", task.id, delivered)
    except Exception as exc:
        logger.warning("定时任务 notify 送达失败 [%s]: %s", task.id, exc)
