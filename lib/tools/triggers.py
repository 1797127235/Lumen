"""事件触发 — agent 工具三件套。

agent 通过 subscribe_events / list_subscriptions / cancel_subscription 管理
「收到 MCP server 事件 → 主动找我反应」的订阅。
依赖 listener(TriggerManager)。**不依赖具体 MCP server** —— 订阅哪个 server 由 agent 传 server_name 决定。

与 lib/tools/scheduler.py 对称:scheduler 管「到点调工具」,这里管「事件触发对话」。
"""

from __future__ import annotations

from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok

_SUB_HELP = (
    "server_name: MCP server 名(如 feed)\n"
    "event_filter: 可选,notification method 过滤(如 resources/updated),空=该 server 所有通知\n"
    "name: 可选,订阅名(方便后续取消)\n"
    "订阅不绑渠道:有通知时,Lumen 会主动推到你已配置的渠道(如 Telegram)。"
)


async def _subscribe(args: dict[str, Any], ctx: Any = None):
    server_name = args.get("server_name", "").strip()
    event_filter = args.get("event_filter", "").strip()
    name = args.get("name") or None

    if not server_name:
        return tool_error("server_name 必填:要订阅的 MCP server 名(如 feed)")

    # 用户身份从当前对话 ctx 取(送达时作 sender);渠道由送达层决定,不在订阅时绑定
    user_id = getattr(ctx, "user_id", "") if ctx else ""

    from lib.triggers import get_manager

    try:
        sub = get_manager().add_subscription(
            server_name=server_name,
            event_filter=event_filter,
            user_id=user_id,
            name=name,
        )
    except Exception as exc:
        return tool_error(f"订阅失败: {exc}")

    return tool_ok(
        f"已订阅事件「{sub.name or sub.id}」\n"
        f"server={server_name} event_filter={event_filter or '(全部通知)'}\n"
        f"当该 server 有通知时,Lumen 会主动找你反应。",
        subscription_id=sub.id,
    )


async def _list(args: dict[str, Any], ctx: Any = None):
    from lib.triggers import get_manager

    subs = get_manager().list_subscriptions()
    if not subs:
        return tool_ok("当前没有事件订阅")
    lines = [f"事件订阅(共 {len(subs)} 个):"]
    for s in subs:
        label = s.name or s.id
        lines.append(
            f"• [{label}] server={s.server_name} " f"event={s.event_filter or '(全部)'} → 主动推送(已配置渠道)"
        )
    return tool_ok("\n".join(lines))


async def _cancel(args: dict[str, Any], ctx: Any = None):
    sub_id = args.get("id", "").strip()
    name = args.get("name", "").strip()
    if not sub_id and not name:
        return tool_error("id 或 name 至少提供一个")

    from lib.triggers import get_manager

    manager = get_manager()
    if name:
        matched = [s for s in manager.list_subscriptions() if s.name == name]
        if not matched:
            return tool_error(f"未找到名为 {name!r} 的订阅")
        for s in matched:
            manager.remove_subscription(s.id)
        return tool_ok(f"已取消 {len(matched)} 个名为 {name!r} 的订阅")

    ok = manager.remove_subscription(sub_id)
    return tool_ok(f"已取消订阅 {sub_id}") if ok else tool_error(f"未找到订阅 {sub_id!r}")


def create_trigger_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="subscribe_events",
            description=(
                "订阅 MCP server 的事件:当该 server 主动发来通知(如 RSS 有新更新)时,"
                "Lumen 会主动找你反应(像伙伴一样告诉你有什么值得关注的)。\n\n" + _SUB_HELP
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "server_name": {"type": "string", "description": "要订阅的 MCP server 名(如 feed)"},
                    "event_filter": {
                        "type": "string",
                        "description": "可选,notification method 过滤(如 resources/updated),空=全部",
                    },
                    "name": {"type": "string", "description": "订阅名(可选,方便后续取消)"},
                },
                "required": ["server_name"],
            },
            execute=_subscribe,
            read_only=False,
            meta=ToolMeta(risk="write", search_hint="订阅、事件、触发、notify、更新提醒、subscribe"),
        ),
        ToolDef(
            name="list_subscriptions",
            description="列出所有事件订阅",
            input_schema={"type": "object", "properties": {}},
            execute=_list,
            read_only=True,
            meta=ToolMeta(always_on=False, risk="read-only"),
        ),
        ToolDef(
            name="cancel_subscription",
            description="取消事件订阅(按 id 或 name)",
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "订阅 id"},
                    "name": {"type": "string", "description": "订阅名"},
                },
            },
            execute=_cancel,
            read_only=False,
            meta=ToolMeta(risk="write"),
        ),
    ]
