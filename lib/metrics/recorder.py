"""MetricsRecorder：进程单例，所有埋点的唯一写入入口。

关键设计：
1. 独立短事务 —— 埋点遍布中间件/后台任务/LLM 调用，很多地方没有 db session 句柄，
   单例自己开短事务最简单可靠。
2. 绝不阻塞业务 —— 全程 try/except，失败只 warning，绝不抛异常给调用方。
3. 非阻塞 fire-and-forget —— 高频路径（如 LLM 每次调用）用 asyncio.create_task 异步插入，
   调用方不等待 DB。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)

_recorder: MetricsRecorder | None = None


def set_recorder(recorder: MetricsRecorder | None) -> None:
    """启动时注入单例（core/startup.py 调用）。"""
    global _recorder
    _recorder = recorder


def get_recorder() -> MetricsRecorder | None:
    """获取单例；未初始化时返回 None（业务代码应优雅跳过）。"""
    return _recorder


async def record(
    name: str,
    value: float = 1.0,
    *,
    labels: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    user_id: str | None = None,
    sync: bool = False,
) -> None:
    """便捷函数：经单例记录一条指标。

    Args:
        name: 指标名，如 "llm.tokens.input"
        value: 计数=1.0 / 耗时 ms / token 数
        labels: 维度字典
        conversation_id / user_id: 可选关联
        sync: True=同步等待插入完成（默认 False=fire-and-forget）

    安全性：单例未初始化或插入失败都静默处理，绝不影响业务。
    """
    r = _recorder
    if r is None:
        return
    try:
        if sync:
            await r.record(
                name,
                value,
                labels=labels,
                conversation_id=conversation_id,
                user_id=user_id,
            )
        else:
            await r.record_fire_and_forget(
                name,
                value,
                labels=labels,
                conversation_id=conversation_id,
                user_id=user_id,
            )
    except Exception:
        logger.warning("metrics record 失败（已忽略）", name=name, exc_info=True)


class MetricsRecorder:
    """指标采集单例。所有埋点最终都汇入这里。

    用法：
        # 启动时
        set_recorder(MetricsRecorder())
        # 任意位置
        await record("llm.tokens.input", 1234, labels={"model": "deepseek-chat"})
    """

    def __init__(self) -> None:
        # 保留 fire-and-forget task 的强引用，避免被 GC 提前回收（RUF006）
        self._pending: set[asyncio.Task] = set()

    async def record(
        self,
        name: str,
        value: float = 1.0,
        *,
        labels: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """同步等待插入完成。低频路径（turn 完成、delivery）可用。"""
        try:
            from core.db import get_async_session_maker
            from lib.metrics.models import MetricEvent

            session_maker = get_async_session_maker()
            labels_str = json.dumps(labels, ensure_ascii=False, default=str) if labels else "{}"
            async with session_maker() as session:
                session.add(
                    MetricEvent(
                        name=name,
                        value=float(value),
                        labels_json=labels_str,
                        conversation_id=conversation_id,
                        user_id=user_id,
                    )
                )
                await session.commit()
        except Exception:
            # 绝不让观测拖垮业务：失败只 warning
            logger.warning("metrics 插入失败（已忽略）", name=name, exc_info=True)

    async def record_fire_and_forget(
        self,
        name: str,
        value: float = 1.0,
        *,
        labels: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """高频路径用：丢到事件循环异步执行，调用方不等待。

        LLM 每次调用、每个 token 类型都会触发，必须非阻塞。
        """
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                self.record(
                    name,
                    value,
                    labels=labels,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
            )
            # 保留强引用直到完成，防止 GC 在 task 执行前回收它
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        except RuntimeError:
            # 没有运行中的事件循环（极端情况），降级为同步
            # 这种情况几乎不会发生，但兜底
            logger.debug("metrics fire-and-forget 无事件循环，降级同步", name=name)

    async def record_many(
        self,
        events: list[dict[str, Any]],
    ) -> None:
        """批量插入（如 LLM 一次调用要写 5 条 token/cost/latency 指标）。"""
        if not events:
            return
        try:
            from core.db import get_async_session_maker
            from lib.metrics.models import MetricEvent

            session_maker = get_async_session_maker()
            async with session_maker() as session:
                for ev in events:
                    session.add(
                        MetricEvent(
                            name=ev["name"],
                            value=float(ev.get("value", 1.0)),
                            labels_json=json.dumps(ev.get("labels") or {}, ensure_ascii=False, default=str),
                            conversation_id=ev.get("conversation_id"),
                            user_id=ev.get("user_id"),
                        )
                    )
                await session.commit()
        except Exception:
            logger.warning("metrics 批量插入失败（已忽略）", exc_info=True)


async def record_many(events: list[dict[str, Any]]) -> None:
    """便捷批量记录函数。"""
    r = _recorder
    if r is None:
        return
    try:
        await r.record_many(events)
    except Exception:
        logger.warning("metrics record_many 失败（已忽略）", exc_info=True)
