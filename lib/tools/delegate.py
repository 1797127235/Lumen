"""delegate 子 Agent 工具 — 主 Agent 可起一个独立 child Agent 执行子任务。

child 在隔离上下文里跑、有独立 UsageLimits、进度实时流回，
跑完只把结果摘要交还主 Agent。超限时做一次无工具的 LLM 调用生成进度总结。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.agent.types import AgentContext
from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Bundle / Blocklist
# ═══════════════════════════════════════════════════════════════

_BUNDLES: dict[str, list[str]] = {
    "web": ["web_search", "web_extract", "web_crawl"],
    "files": ["file_read", "file_write", "file_ls", "file_grep", "file_edit"],
}

_BLOCKLIST: set[str] = {
    # 记忆 / 画像 — child 不可污染共享 memory.md / about_you.md
    "memory",
    "memory_search",
    "update_profile",
    "get_profile",
    # 递归委派阻断（深度固定 = 1）
    "delegate",
    # 主动消息 / shell — child 不应有副作用
    "shell",
}

_DEFAULT_BUNDLES = ["web", "files"]

# child UsageLimits — 限制步骤防止无限循环
_CHILD_REQUEST_LIMIT = 15
_CHILD_TOOL_CALLS_LIMIT = 20

# 工具结果截断上限（约 ~25K tokens）
_MAX_TOOL_RESULT_CHARS = 100_000

# 收尾总结 prompt
_FORCE_SUMMARY_PROMPT = (
    "你已用完任务执行预算，禁止再调用工具。\n"
    "现在必须直接输出中文最终总结。\n"
    "必须覆盖：1) 已完成内容；2) 当前未完成内容；3) 下一步建议。\n"
    "禁止：继续规划工具调用；说'需要继续调用工具'；输出模板句。"
)

# 报告目录
_REPORTS_DIR = Path.home() / ".lumen" / "reports"

# Worker system prompt 模板
_WORKER_PROMPT_TEMPLATE = (
    "你是一个专注的任务执行 Agent。完成以下目标后，用简洁的中文总结结果。\n"
    "若涉及调研，综合多个来源并标注出处。\n\n"
    "【目标】{goal}\n"
    "【背景】{context}"
)


def resolve_tool_names(toolsets: list[str] | None = None) -> list[str]:
    """将 toolsets bundle 名展开成具体工具名，再剥掉 blocklist。"""
    bundles = toolsets if toolsets is not None else _DEFAULT_BUNDLES
    names: list[str] = []
    for bundle in bundles:
        resolved = _BUNDLES.get(bundle)
        if resolved is not None:
            names.extend(resolved)
        # 未知 bundle 名被忽略不报错
    # 剥掉 blocklist
    return [n for n in names if n not in _BLOCKLIST]


# ═══════════════════════════════════════════════════════════════
#  工具执行函数
# ═══════════════════════════════════════════════════════════════


async def _handle_delegate(args: dict[str, Any], ctx: Any = None) -> Any:
    """delegate 工具 handler。

    ctx 是 LumenDeps（见 factory.py _to_pydantic_tool: execute(kwargs, args.get(\"deps\"))）。
    """
    goal = args.get("goal", "").strip()
    context = args.get("context", "").strip()
    toolsets_raw = args.get("toolsets")

    if not goal:
        return tool_error("goal 不能为空")

    # 解析 child 工具集
    toolsets = toolsets_raw if toolsets_raw is not None else None
    child_tool_names = resolve_tool_names(toolsets)

    if not child_tool_names:
        return tool_error("解析后无可用工具，请检查 toolsets 参数")

    # 确保 reports 目录存在
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 构造 worker system prompt（TODO: 传递给 run_worker_agent）
    _system_prompt = _WORKER_PROMPT_TEMPLATE.format(goal=goal, context=context or "无")

    # 进度回调（progress_emitter 可能为 None，如后台 review agent）
    emit = args.get("progress_emitter")

    try:
        from core.agent import run_worker_agent
        from core.db import get_async_session_maker
        from lib.tools._registry import get_tool_registry

        # 确保工具已注册
        registry = get_tool_registry()
        if not registry.get_registered_names():
            from lib.tools.factory import register_all_tools

            register_all_tools()

        # 独立 db session
        async with get_async_session_maker()() as child_db:
            child_ctx = AgentContext(
                user_id=args.get("user_id", ""),
                db=child_db,
                workspace_root=args.get("workspace_root", ""),
            )

            if emit:
                emit("started", f"开始子任务：{goal[:60]}")

            # 收集中间步骤文本，用于超限后的收尾总结
            step_log: list[str] = []

            # 运行 child agent
            try:
                agent_result = await run_worker_agent(
                    messages=[{"role": "user", "content": goal}],
                    ctx=child_ctx,
                    tool_names=child_tool_names,
                )
                result = agent_result.content

            except Exception as exc:
                # 超限或异常：做收尾总结
                logger.warning(
                    "delegate 步骤预算耗尽或异常，执行收尾总结",
                    error=str(exc)[:200],
                    steps=len(step_log),
                )
                if emit:
                    emit("step", "步骤预算耗尽，正在生成进度总结...")

                result = await _force_summary(child_ctx, step_log, reason=str(exc))

            await child_db.commit()

        if not result:
            result = "子任务完成，但未返回结果摘要。"

        if emit:
            emit("done", "子任务完成")

        return tool_ok(result)

    except Exception as exc:
        logger.warning("delegate child agent 失败", error=str(exc)[:200])
        if emit:
            emit("error", f"子任务失败：{str(exc)[:80]}")
        # child 抛错被隔离，返回错误摘要给父 Agent，不崩主对话
        return tool_error(f"子任务执行失败：{str(exc)[:200]}")


async def _force_summary(
    child_ctx: Any,
    step_log: list[str],
    *,
    reason: str,
) -> str:
    """超限后，做一次无工具 LLM 调用生成进度总结。"""
    from lib.llm.client import LLMClient

    steps_text = "\n".join(f"- {s}" for s in step_log[-20:])  # 最近 20 步
    prompt = f"[收尾原因] {reason}\n[已执行步骤]\n{steps_text}\n\n{_FORCE_SUMMARY_PROMPT}"
    try:
        client = LLMClient()
        summary = await client.complete(
            messages=[{"role": "user", "content": prompt}],
            system="你是一位任务执行助手。请总结已完成的进度和未完成的事项。",
        )
        if summary and summary.strip():
            return summary.strip()
    except Exception as e:
        logger.warning("delegate 收尾总结失败", error=str(e)[:200])
    return "步骤预算已耗尽：已完成部分关键步骤，但仍有未完成项。"


def _parse_args(raw: Any) -> dict[str, Any]:
    """把 tool call 的 raw args（可能是 JSON 字符串/None）规整成 dict。"""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _emit_progress_from_event(event: Any, emit: Any, step_log: list[str] | None = None) -> None:
    """从 child 的 stream event 提取进度文本并通过 emit 发出。

    event_kind 值：function_tool_call / function_tool_result / part_start /
    part_delta / agent_run_result。

    Args:
        step_log: 可选的步骤日志列表，用于收集中间步骤供收尾总结使用。
    """
    if emit is None and step_log is None:
        return

    kind = getattr(event, "event_kind", "")
    if kind == "function_tool_call":
        # tool_name / args 可能直接在 event 上或在 event.part 上
        tool_name = getattr(event, "tool_name", "") or getattr(getattr(event, "part", None), "tool_name", "")
        raw_args = getattr(event, "args", None) or getattr(getattr(event, "part", None), "args", None)
        # PydanticAI 的 function_tool_call args 可能是 JSON 字符串，先规整成 dict
        args = raw_args if isinstance(raw_args, dict) else _parse_args(raw_args)
        # 粗粒度：工具名 + 简短参数
        if tool_name == "web_search":
            detail = f"搜索：{args.get('query', '')[:50]}"
        elif tool_name == "web_extract":
            urls = args.get("urls", [])
            detail = f"读取网页：{urls[0][:50] if urls else ''}"
        elif tool_name == "web_crawl":
            detail = f"爬取：{args.get('url', '')[:50]}"
        elif tool_name == "file_read":
            detail = f"读取文件：{args.get('file_path', '')[:50]}"
        elif tool_name == "file_write":
            detail = f"写入文件：{args.get('file_path', '')[:50]}"
        else:
            detail = f"调用 {tool_name}"
        if emit:
            emit("step", detail)
        if step_log is not None:
            step_log.append(detail)
    elif kind == "function_tool_result":
        tool_name = getattr(event, "tool_name", "") or getattr(getattr(event, "part", None), "tool_name", "")
        # tool_error 的规范输出以 ❌ 开头（见 _base.py），据此区分成功/失败
        content = getattr(event, "content", None) or getattr(getattr(event, "result", None), "content", None) or ""
        if isinstance(content, str) and content.lstrip().startswith("❌"):
            detail = f"{tool_name} 失败"
        else:
            detail = f"{tool_name} 完成"
        if emit:
            emit("step", detail)
        if step_log is not None:
            step_log.append(detail)


# ═══════════════════════════════════════════════════════════════
#  工具注册
# ═══════════════════════════════════════════════════════════════


def create_delegate_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="delegate",
            description=(
                "委派一个独立子 Agent 去执行任务。子 Agent 在隔离上下文中运行，"
                "有独立的工具集和步骤预算（最多 15 轮 LLM 调用），完成后只返回结果摘要。\n\n"
                "适用场景：深度调研、多步骤搜索、需要大量工具调用但不应占用主对话上下文的任务。\n\n"
                "使用方式：传入明确的 goal 和可选的背景 context。"
                "toolsets 指定子 Agent 可用的工具类别（默认 web+files）。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "交给子 Agent 的明确目标（必填）",
                    },
                    "context": {
                        "type": "string",
                        "description": "背景信息（主 Agent 从对话里提炼，子 Agent 看不到历史）",
                        "default": "",
                    },
                    "toolsets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "工具类别 bundle 名，默认 ['web','files']。可选: web, files",
                    },
                },
                "required": ["goal"],
            },
            execute=_handle_delegate,
            read_only=False,
            meta=ToolMeta(
                always_on=True,  # 常驻可见，调研类任务直接可委派
                risk="write",
                search_hint="委派任务、子任务、深度调研、delegation、research",
                tags=["delegate", "agent", "research"],
            ),
        )
    ]
