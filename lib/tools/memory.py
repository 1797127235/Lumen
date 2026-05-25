"""记忆工具 — memory_search / memory。

对齐 Hermes：memory 工具统一入口，支持 add 到 MEMORY.md 或 USER.md。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lib.memory.markdown import AsyncMarkdownStore
from lib.memory.understanding import update_ai_understanding
from lib.tools._base import ToolDef, ToolMeta, tool_error
from shared.logging import get_logger

logger = get_logger(__name__)

_store = AsyncMarkdownStore()


async def _bg_refresh_understanding(user_id: str) -> None:
    """后台刷新 USER.md，失败静默。"""
    try:
        await update_ai_understanding(user_id)
    except Exception as exc:
        logger.debug("USER.md 后台刷新失败", error=str(exc))


async def _search(args: dict[str, Any], deps) -> str:
    query = args.get("query", "").strip()
    if not query:
        return tool_error("请提供搜索关键词")

    user_id = deps.user_id

    # 1. 本地 MEMORY.md 简单文本匹配
    content = await _store.read_memory(user_id)
    results: list[str] = []

    if content:
        keywords = [kw.lower() for kw in query.split() if len(kw) > 1]
        if keywords:
            paragraphs = content.split("\n\n")
            for para in paragraphs:
                para_lower = para.lower()
                if any(kw in para_lower for kw in keywords):
                    results.append(para[:300])

    # 2. 外部 provider prefetch（如果有）
    # 阶段 4 后通过 MemoryManager 注入，阶段 2 仅做本地匹配
    # 预留：未来可通过 deps.memory_manager 获取外部 provider 召回

    if results:
        return "\n".join(f"- {r}" for r in results)
    return "未找到相关内容。"


async def _memory(args: dict[str, Any], deps) -> str:
    action = args.get("action", "").strip().lower()
    target = args.get("target", "memory").strip().lower()
    content = args.get("content", "")

    if action not in {"add", "replace", "remove"}:
        return tool_error("action 必须是 add / replace / remove")

    if target not in {"memory", "user"}:
        return tool_error("target 必须是 memory 或 user")

    user_id = deps.user_id

    if action in {"replace", "remove"}:
        return tool_error("replace / remove 暂未实现，请使用 add")

    # action == "add"
    if not content or not content.strip():
        return "内容为空，跳过保存。"

    # 安全扫描
    from lib.memory.markdown import _scan_memory_content

    safe, reason = _scan_memory_content(content)
    if not safe:
        logger.warning("记忆写入被拒绝", user_id=user_id, reason=reason)
        return f"写入被拒绝: {reason}"

    if target == "memory":
        # 写入 MEMORY.md（带日期和 category 标签）
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        entry = f"- {date_str} — [memory] {content}"
        await _store.append_memory_entry(user_id, "memory", content)
    else:
        # target == "user"：写入 USER.md
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        entry = f"- {date_str} — [user] {content}"
        # USER.md 直接追加，不经过 append_memory_entry 的章节逻辑
        import os
        import tempfile

        from lib.memory.markdown import _acquire_file_lock, _release_file_lock

        user_dir = _store._user_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        user_path = user_dir / "USER.md"
        lock_path = user_dir / ".lock"

        import asyncio

        lock_fd = await asyncio.to_thread(_acquire_file_lock, lock_path)
        try:
            existing = await _store.read_about_you(user_id)
            if not existing.strip():
                existing = "# 用户画像\n\n"
            new_content = existing.rstrip() + f"\n\n{entry}\n"
            # 写入（使用底层原子写入）
            fd, temp_path = tempfile.mkstemp(dir=str(user_dir), suffix=".tmp")
            try:
                os.write(fd, new_content.encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(temp_path, user_path)
        finally:
            await asyncio.to_thread(_release_file_lock, lock_fd, lock_path)

    # 触发 USER.md 刷新（后台，不阻塞）
    import asyncio as _asyncio

    _asyncio.create_task(_bg_refresh_understanding(user_id))  # noqa: RUF006

    return f"已记录到 {target}"


def create_memory_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="memory_search",
            description="搜索记忆。直接对 MEMORY.md 做关键词匹配。",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
            execute=_search,
            read_only=True,
            meta=ToolMeta(always_on=True, risk="read-only", search_hint="搜索记忆、回忆、查找历史"),
        ),
        ToolDef(
            name="memory",
            description=(
                "保存持久化记忆，跨会话生效。主动调用，不要等用户要求。\n\n"
                "WHEN TO SAVE:\n"
                "- 用户纠正你或说'记住这个'\n"
                "- 用户分享偏好、习惯、个人信息\n"
                "- 你发现环境事实、项目约定、工具 quirks\n\n"
                "TWO TARGETS:\n"
                "- 'memory': 环境事实、项目约定、工具 quirks、学到的教训（写入 MEMORY.md）\n"
                "- 'user': 用户偏好、沟通风格、习惯、关系（写入 USER.md）\n\n"
                "ACTIONS: add (新增), replace (替换 — 暂未实现), remove (删除 — 暂未实现)"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "replace", "remove"],
                        "description": "操作类型: add 新增, replace 替换, remove 删除",
                    },
                    "target": {
                        "type": "string",
                        "enum": ["memory", "user"],
                        "description": "目标: 'memory' 写入 MEMORY.md (环境/事实), 'user' 写入 USER.md (用户画像)",
                    },
                    "content": {
                        "type": "string",
                        "description": "内容。add/replace 时必填。",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "用于 replace/remove 定位旧条目。replace/remove 时必填。",
                    },
                },
                "required": ["action", "target"],
            },
            execute=_memory,
            read_only=False,
            meta=ToolMeta(always_on=True, risk="write", search_hint="保存记忆、记录、记住"),
        ),
    ]
