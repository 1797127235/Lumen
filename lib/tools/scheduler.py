"""定时任务 — agent 工具三件套。

agent 通过 schedule / list_schedules / cancel_schedule 管理"到点调用工具"的任务。
依赖 engine(调度)。**不依赖具体工具** —— 调什么工具由 agent 传 tool 名决定。
"""

from __future__ import annotations

from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok

_TRIGGER_HELP = (
    "trigger + when 组合:\n"
    "  interval → '30s' / '5m' / '2h' / '1h30m'(每隔一段时间)\n"
    "  cron     → '0 9 * * *'(cron 表达式,如每天9点)\n"
    "  date     → '14:30' 或 ISO 时间(一次性,到点跑一次)"
)


async def _schedule(args: dict[str, Any], ctx: Any = None):
    trigger = args.get("trigger", "").strip()
    when = args.get("when", "").strip()
    tool = args.get("tool", "").strip()
    tool_args = args.get("args") or {}
    name = args.get("name") or None
    # 默认 notify=True(到点跑完主动推送结果给用户);显式传 false 才静默
    want_notify = args.get("notify", True)
    if isinstance(want_notify, str):
        want_notify = want_notify.lower() not in ("false", "0", "no")
    want_notify = bool(want_notify)

    if trigger not in ("interval", "cron", "date"):
        return tool_error(f"trigger 须为 interval/cron/date\n{_TRIGGER_HELP}")
    if not when:
        return tool_error("when 必填")
    if not tool:
        return tool_error("tool 必填:要定时调用的工具名(如 web_search,或某个 MCP 工具名)")

    from lib.scheduler import get_scheduler_engine

    try:
        task = get_scheduler_engine().add_task(
            trigger=trigger,
            when=when,
            tool=tool,
            args=tool_args,
            name=name,
            notify=want_notify,
        )
    except Exception as exc:
        return tool_error(f"注册失败: {exc}")

    mode_hint = "\n跑完会主动推送结果给你(经你已配置的渠道)。" if want_notify else "\n(静默模式:跑完只记日志,不打扰你)"
    return tool_ok(
        f"已注册定时任务「{task.name or task.id}」\ntrigger={trigger} when={when}\n到点调用工具: {tool}{mode_hint}",
        task_id=task.id,
    )


async def _list(args: dict[str, Any], ctx: Any = None):
    from lib.scheduler import get_scheduler_engine

    tasks = get_scheduler_engine().list_tasks()
    if not tasks:
        return tool_ok("当前没有定时任务")
    lines = [f"定时任务(共 {len(tasks)} 个):"]
    for t in tasks:
        label = t.name or t.id
        lines.append(f"• [{label}] trigger={t.trigger} when={t.when} tool={t.tool}")
    return tool_ok("\n".join(lines))


async def _cancel(args: dict[str, Any], ctx: Any = None):
    task_id = args.get("id", "").strip()
    name = args.get("name", "").strip()
    if not task_id and not name:
        return tool_error("id 或 name 至少提供一个")

    from lib.scheduler import get_scheduler_engine

    engine = get_scheduler_engine()
    if name:
        matched = [t for t in engine.list_tasks() if t.name == name]
        if not matched:
            return tool_error(f"未找到名为 {name!r} 的任务")
        for t in matched:
            engine.remove_task(t.id)
        return tool_ok(f"已取消 {len(matched)} 个名为 {name!r} 的任务")

    ok = engine.remove_task(task_id)
    return tool_ok(f"已取消任务 {task_id}") if ok else tool_error(f"未找到任务 {task_id!r}")


def create_scheduler_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="schedule",
            description=(
                "注册定时任务:到点自动调用一个工具(任意已注册工具,含 MCP 工具)。\n"
                "适合:定时搜索、定时调 MCP 工具、周期性数据抓取、定时报告等。\n\n"
                + _TRIGGER_HELP
                + "\n\ntool: 要调用的工具名(如 web_search)\n"
                "args: 调用该工具的参数(对象)\n"
                "notify: 跑完后是否主动推送结果给你(默认 true,推送到你已配置的渠道);"
                "false=静默,只记日志\n\n"
                '用户说"每天X点报告/提醒/推送"这类诉求时,直接用本工具(默认就会主动推送),'
                "不要让用户自己设闹钟或来问你要结果。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "trigger": {"type": "string", "enum": ["interval", "cron", "date"]},
                    "when": {"type": "string", "description": "触发时间,与 trigger 对应"},
                    "tool": {"type": "string", "description": "要定时调用的工具名"},
                    "args": {"type": "object", "description": "调用工具的参数"},
                    "name": {"type": "string", "description": "任务名(可选,方便后续取消)"},
                    "notify": {"type": "boolean", "description": "跑完后是否主动推送结果(默认 true)"},
                },
                "required": ["trigger", "when", "tool"],
            },
            execute=_schedule,
            read_only=False,
            meta=ToolMeta(risk="write", search_hint="定时、定期、每隔、schedule、cron、周期、报告、提醒"),
        ),
        ToolDef(
            name="list_schedules",
            description="列出所有定时任务",
            input_schema={"type": "object", "properties": {}},
            execute=_list,
            read_only=True,
            meta=ToolMeta(always_on=False, risk="read-only"),
        ),
        ToolDef(
            name="cancel_schedule",
            description="取消定时任务(按 id 或 name)",
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "任务 id"},
                    "name": {"type": "string", "description": "任务名"},
                },
            },
            execute=_cancel,
            read_only=False,
            meta=ToolMeta(risk="write"),
        ),
    ]
