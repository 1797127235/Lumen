"""Lumen 主动行动 — 事件触发模块。

与 lib/scheduler(时间触发)对称,两者构成「主动行动能力」家族,统一概念模型:

        触发源              动作
  ┌──────────────┐    ┌─────────────────────┐
  │ 时间(scheduler)│    │ silent  → 跑工具,写日志 |
  │ 事件(本模块)  │    │ notify  → 注入对话,agent 经 Telegram 主动找你│
  └──────────────┘    └─────────────────────┘

本模块触发源=事件(MCP server notification / 短轮询);动作恒为 notify。
与 scheduler 的 notify 动作共享同一条
「注入 InboundMessage → AgentRunner → OutboundMessage → Telegram 推送」路径。

分层:
  store     — 订阅元数据持久化(JSON),含 target_channel/chat_id/user_id
  listener  — 监听 MCP notification + 短轮询 + 匹配订阅 + 注入对话

用法(startup):
    from lib.triggers import get_manager
    manager = get_manager(bus)   # 注入 MessageBus
    await manager.start()        # 启动(恢复持久化订阅 + 起短轮询)

client_manager 收到 MCP notification 后(已在 client_manager._make_message_handler 接好):
    await get_manager().handle_notification(server_name, notification)

agent 侧用 subscribe_events / list_subscriptions / cancel_subscription 工具。
"""

from __future__ import annotations

import os
from pathlib import Path

from lib.bus.queue import MessageBus
from lib.triggers.listener import TriggerManager
from lib.triggers.store import SubscriptionStore

__all__ = ["TriggerManager", "SubscriptionSpec", "SubscriptionStore", "get_manager"]

_manager: TriggerManager | None = None


def get_manager(bus: MessageBus | None = None) -> TriggerManager:
    """全局单例。首次调用需传 bus;后续调用可省略。按 LUMEN_HOME 装配 store。"""
    global _manager
    if _manager is None:
        if bus is None:
            raise RuntimeError("首次调用 get_manager 必须传 MessageBus")
        home = Path(os.environ.get("LUMEN_HOME", str(Path.home() / ".lumen")))
        store = SubscriptionStore(home / "triggers" / "subscriptions.json")
        _manager = TriggerManager(store, bus)
    return _manager
