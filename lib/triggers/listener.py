"""事件触发 — 监听 MCP notification + 匹配订阅 + 注入对话。

对应 lib/scheduler/engine.py 的角色:把外部信号转成「触发动作」。
动作 = 注入一条 InboundMessage 到 MessageBus,让 AgentRunner 处理。

**短轮询方案**:TriggerManager 为每个订阅起一个后台任务,定时(默认 5 分钟)
调用 server 的查询工具(如 feed_get_unread)拉取状态,对比上次已知 id 去重,
有新的「值得看」内容才注入对话。短调用不长期占用 MCP ClientSession,
避免与 agent 正常工具调用竞态导致 ClosedResourceError。

handle_notification 保留:其他 MCP server 若主动发 resources/updated 通知
(经 client_manager message_handler 转发),仍能匹配订阅注入对话。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from lib.bus.queue import MessageBus
from lib.triggers.store import SubscriptionSpec, SubscriptionStore
from shared.logging import get_logger

logger = get_logger(__name__)

_POLL_INTERVAL_SEC = 300  # 短轮询间隔(5 分钟;RSS 30 分钟拉取粒度,5 分钟检查够用)
_POLL_TOOL = "feed_get_unread"  # 约定:事件源 server 暴露的查询工具名(返回未读 + 分析)
_POLL_LIMIT = 10  # 每次拉取条数
_MAX_CONSECUTIVE_FAILURES = 5  # 连续失败 N 次触发 MCP server 重连
_SEEN_IDS_LIMIT = 1000  # 去重集合上限,超过则清空(可接受偶尔重复通知)


def _notification_method(notification: Any) -> str:
    """从 mcp.types.ServerNotification 提取 method(discriminated union 的 .root.method)。"""
    root = getattr(notification, "root", notification)
    return getattr(root, "method", "") or ""


class TriggerManager:
    """事件触发管理器:匹配订阅 → 注入对话 + 长轮询调用 server。

    生命周期:
        startup 调 start()(恢复订阅 + 起长轮询)
        client_manager 收到 MCP notification 转发给 handle_notification()
        agent 工具(subscribe_events 等)调 add/remove/list
        shutdown 调 stop()
    """

    def __init__(self, store: SubscriptionStore, bus: MessageBus) -> None:
        self._store = store
        self._bus = bus
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        subs = self._store.list_all()
        for sub in subs:
            if sub.enabled:
                self._start_poll(sub)
        logger.info("TriggerManager started,恢复 %d 个订阅", len(subs))

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        logger.info("TriggerManager stopped")

    # ── 订阅管理(agent 工具调用)──

    def add_subscription(
        self,
        *,
        server_name: str,
        event_filter: str = "",
        user_id: str = "",
        name: str | None = None,
    ) -> SubscriptionSpec:
        sub = SubscriptionSpec(
            id=f"sub_{uuid.uuid4().hex[:10]}",
            server_name=server_name,
            event_filter=event_filter,
            user_id=user_id,
            name=name,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._store.upsert(sub)
        self._start_poll(sub)
        logger.info("订阅已添加 [%s] server=%s", sub.id, server_name)
        return sub

    def remove_subscription(self, sub_id: str) -> bool:
        task = self._tasks.pop(sub_id, None)
        if task:
            task.cancel()
        removed = self._store.remove(sub_id)
        if removed:
            logger.info("订阅已删除 [%s]", sub_id)
        return removed

    def list_subscriptions(self) -> list[SubscriptionSpec]:
        return self._store.list_all()

    # ── 事件触发(client_manager 转发)──

    async def handle_notification(self, server_name: str, notification: Any) -> None:
        """收到 MCP server 的 notification → 匹配订阅 → 命中则注入对话。

        notification 只作「有更新」信号,不带内容——agent 收到后会自己调
        对应 server 的工具(如 feed_get_unread)拉详情,再决定怎么告诉用户。
        """
        method = _notification_method(notification)
        subs = self._store.list_all()
        matched = [
            s
            for s in subs
            if s.enabled and s.server_name == server_name and (not s.event_filter or s.event_filter == method)
        ]
        for sub in matched:
            content = (
                f"【订阅更新】MCP 服务「{server_name}」报告有新事件"
                f"{'(' + method + ')' if method else ''}。"
                f"帮我看看有什么值得关注的,简要告诉我。"
            )
            from lib.proactive.delivery import deliver

            delivered = await deliver(
                user_id=sub.user_id,
                content=content,
                source="trigger",
                source_id=sub.id,
            )
            logger.info(
                "事件已送达 [%s] server=%s channels=%s",
                sub.id,
                server_name,
                delivered,
            )

    # ── 短轮询(主动调 server 查询工具,去重后注入对话)──

    def _start_poll(self, sub: SubscriptionSpec) -> None:
        """为某订阅起一个短轮询后台任务(先取消同 id 旧任务)。"""
        old = self._tasks.pop(sub.id, None)
        if old:
            old.cancel()
        task = asyncio.create_task(self._poll_loop(sub), name=f"trigger:{sub.id}")
        self._tasks[sub.id] = task

    async def _poll_loop(self, sub: SubscriptionSpec) -> None:
        """短轮询:定时调 server 的查询工具,对比去重,有新内容才注入。

        用短调用避免长轮询(feed_wait_updates)长期占用 MCP ClientSession,
        与 agent 正常工具调用竞态导致 ClosedResourceError。
        """
        from lib.tools.mcp.client_manager import get_mcp_manager

        manager = get_mcp_manager()
        seen_ids: set[str] = set()  # 已通知过的 item_id,避免重复注入
        consecutive_failures = 0

        while not self._stopping:
            try:
                result = await manager.call_tool(
                    sub.server_name,
                    _POLL_TOOL,
                    {"limit": _POLL_LIMIT},
                )
            except Exception as exc:
                logger.warning("短轮询调用异常", sub=sub.id, server=sub.server_name, error=str(exc))
                consecutive_failures += 1
                await self._handle_poll_failure(sub, consecutive_failures)
                continue

            # call_tool 失败时返回 tool_error(❌ 字符串)而非 raise
            if isinstance(result, str) and result.startswith("❌"):
                consecutive_failures += 1
                await self._handle_poll_failure(sub, consecutive_failures)
                continue

            consecutive_failures = 0
            await self._check_and_inject(sub, result, seen_ids)
            await asyncio.sleep(_POLL_INTERVAL_SEC)

    async def _check_and_inject(
        self,
        sub: SubscriptionSpec,
        result_str: str,
        seen_ids: set[str],
    ) -> None:
        """解析查询结果,对新的「值得看」条目注入对话。

        result_str 是 tool_ok 包装的文本,内容可能是 JSON 字符串。
        解析失败则静默跳过(不报错,等下次轮询)。
        """
        try:
            data = json.loads(result_str)
            items = data.get("items", []) if isinstance(data, dict) else []
        except (json.JSONDecodeError, TypeError):
            return  # 结果不是预期格式,静默跳过

        new_worth = [it for it in items if it.get("verdict") == "worth_reading" and it.get("id") not in seen_ids]
        if not new_worth:
            return

        # 去重集合上限保护:超过则清空(可接受偶尔重复通知)
        if len(seen_ids) > _SEEN_IDS_LIMIT:
            seen_ids.clear()
        for it in new_worth:
            seen_ids.add(it["id"])

        content = f"【订阅更新】{sub.server_name} 有 {len(new_worth)} 条值得看的新内容。帮我看看,简要告诉我重点。"
        from lib.proactive.delivery import deliver

        delivered = await deliver(
            user_id=sub.user_id,
            content=content,
            source="trigger",
            source_id=sub.id,
        )
        logger.info(
            "短轮询发现新内容并送达 [%s] server=%s count=%d channels=%s",
            sub.id,
            sub.server_name,
            len(new_worth),
            delivered,
        )

    async def _handle_poll_failure(self, sub: SubscriptionSpec, failure_count: int) -> None:
        """连续失败时退避,达到阈值触发 MCP server 定点重连。

        避免对已断开的 session 空转死循环:重连成功后下一轮自然恢复。
        """
        if failure_count >= _MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                "连续失败触发重连 [%s] server=%s failures=%d",
                sub.id,
                sub.server_name,
                failure_count,
            )
            try:
                from lib.tools.mcp.client_manager import get_mcp_manager

                manager = get_mcp_manager()
                await manager.reconnect_one(sub.server_name)
            except Exception as exc:
                logger.error("重连失败", server=sub.server_name, error=str(exc))
            await asyncio.sleep(60)  # 重连后等待较久再试
        else:
            await asyncio.sleep(15)
