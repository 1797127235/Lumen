"""记忆工具 — memory_search / memory。

对齐 Hermes：memory 工具统一入口，支持 add 到 MEMORY.md 或 USER.md。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from lib.memory.markdown import AsyncMarkdownStore
from lib.memory.understanding import update_ai_understanding
from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)

_store = AsyncMarkdownStore()


async def _bg_refresh_understanding(user_id: str) -> None:
    """后台刷新 USER.md，失败静默。"""
    try:
        await update_ai_understanding(user_id)
    except Exception as exc:
        logger.debug("USER.md 后台刷新失败", error=str(exc))


async def _notify_memory_write(
    action: str,
    target: str,
    content: str,
    user_id: str,
    category: str = "fact",
) -> None:
    """通知 MemoryManager 将写入事件镜像给外部 provider。"""
    try:
        from lib.memory import get_memory_manager

        manager = get_memory_manager()
        await manager.on_memory_write(
            action,
            target,
            content,
            metadata={"user_id": user_id, "category": category},
        )
    except Exception as exc:
        logger.debug("on_memory_write 镜像失败", error=str(exc))


async def _search(args: dict[str, Any], ctx: Any = None):
    query = args.get("query", "").strip()
    if not query:
        return tool_error("请提供搜索关键词")

    user_id = args.get("user_id")

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
    # 预留：未来可通过 args.get("memory_manager") 获取外部 provider 召回

    if results:
        return tool_ok("\n".join(f"- {r}" for r in results))
    return tool_ok("未找到相关内容。")


async def _memory(args: dict[str, Any], ctx: Any = None):
    action = args.get("action", "").strip().lower()
    target = args.get("target", "memory").strip().lower()
    content = args.get("content", "")

    if action not in {"add", "replace", "remove"}:
        return tool_error("action 必须是 add / replace / remove")

    if target not in {"memory", "user", "partner"}:
        return tool_error("target 必须是 memory / user / partner")

    user_id = args.get("user_id")

    if action == "add":
        return await _memory_add(args)

    # action == "replace" or "remove"
    old_text = args.get("old_text", "")
    if not old_text or not old_text.strip():
        return tool_error(f"{action} 操作必须提供 old_text 参数")

    return await _memory_replace_remove(action, user_id, target, old_text, content)


async def _memory_add(args: dict[str, Any], ctx: Any = None) -> Any:
    """处理 add 操作。"""
    content = args.get("content", "")
    user_id = args.get("user_id")
    target = args.get("target", "memory")

    if not content or not content.strip():
        return tool_ok("内容为空，跳过保存。")

    # 安全扫描
    from lib.memory.markdown import _scan_memory_content

    safe, reason = _scan_memory_content(content)
    if not safe:
        logger.warning("记忆写入被拒绝", user_id=user_id, reason=reason)
        return tool_error(f"写入被拒绝: {reason}", "SAFETY")

    # 记忆分类标签
    category = args.get("category", "fact")
    valid_categories = {"fact", "preference", "intent", "transient", "correction"}
    if category not in valid_categories:
        category = "fact"

    if target == "memory":
        # 写入 MEMORY.md，category 作为条目标签
        await _store.append_memory_entry(user_id, category, content)
    elif target == "partner":
        # target == "partner"：写入 PARTNER.md（AI 协作规则）
        await _store.append_partner_rule(user_id, content)
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
    _asyncio.create_task(_notify_memory_write("add", target, content, user_id, category=category))  # noqa: RUF006

    return tool_ok(f"已记录到 {target}", target=target)


async def _memory_replace_remove(
    action: str,
    user_id: str,
    target: str,
    old_text: str,
    new_content: str | None = None,
) -> Any:
    """处理 replace / remove 操作。

    在 MEMORY.md 或 USER.md 中查找包含 old_text 的行，执行替换或删除。
    """
    import asyncio as _asyncio
    import os
    import tempfile

    from lib.memory.markdown import _acquire_file_lock, _release_file_lock

    # 选择文件路径
    if target == "memory":
        file_path = _store._memory_path(user_id)
    elif target == "partner":
        file_path = _store._partner_path(user_id)
    else:
        file_path = _store._about_you_path(user_id)

    # 读取文件
    existing = await _store._read(file_path)
    if not existing.strip():
        return tool_error(f"{target} 文件为空，无法 {action}")

    # 查找匹配行
    lines = existing.splitlines(keepends=True)
    matches: list[int] = []
    for i, line in enumerate(lines):
        if old_text in line:
            matches.append(i)

    if len(matches) == 0:
        return tool_error(f"未找到包含 '{old_text}' 的条目", hint="可用 memory_search 查看现有内容")
    if len(matches) > 1:
        # 返回所有匹配行的预览
        previews = [f"  行 {i + 1}: {lines[i].strip()[:80]}" for i in matches]
        return tool_error(
            f"找到 {len(matches)} 条匹配，请提供更精确的 old_text",
            hint="\n".join(previews),
        )

    match_idx = matches[0]
    matched_line = lines[match_idx]

    if action == "remove":
        # 删除该行
        new_lines = lines[:match_idx] + lines[match_idx + 1 :]
        result_msg = f"已删除条目: {matched_line.strip()[:80]}"
    else:
        # replace
        if not new_content or not new_content.strip():
            return tool_error("replace 操作必须提供 content 参数")

        # 安全扫描
        from lib.memory.markdown import _scan_memory_content

        safe, reason = _scan_memory_content(new_content)
        if not safe:
            logger.warning("记忆写入被拒绝", user_id=user_id, reason=reason)
            return tool_error(f"写入被拒绝: {reason}", "SAFETY")

        # 保留前缀（日期 + category），替换内容部分
        # 格式: "- 2026-05-26 — [category] content" 或 partner 的 "- content"
        prefix_match = _MATCH_ENTRY_PREFIX.match(matched_line)
        if prefix_match:
            prefix = prefix_match.group(0)
            new_line = prefix + new_content + "\n"
        elif matched_line.strip().startswith("- "):
            # partner 条目：保留 "- " 前缀
            new_line = "- " + new_content + "\n"
        else:
            # 非标准格式，整行替换
            new_line = matched_line.replace(old_text, new_content, 1)
            if new_line == matched_line:
                new_line = new_content + "\n"

        new_lines = [*lines[:match_idx], new_line, *lines[match_idx + 1 :]]
        result_msg = f"已替换条目: {matched_line.strip()[:80]} → {new_content[:80]}"

    # 写回文件
    user_dir = _store._user_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    lock_path = user_dir / ".lock"

    lock_fd = await _asyncio.to_thread(_acquire_file_lock, lock_path)
    try:
        fd, temp_path = tempfile.mkstemp(dir=str(user_dir), suffix=".tmp")
        try:
            os.write(fd, "".join(new_lines).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(temp_path, file_path)
    finally:
        await _asyncio.to_thread(_release_file_lock, lock_fd, lock_path)

    # 触发 USER.md 刷新（后台，不阻塞）
    _asyncio.create_task(_bg_refresh_understanding(user_id))  # noqa: RUF006

    notify_content = new_content if action == "replace" else matched_line.strip()
    _asyncio.create_task(_notify_memory_write(action, target, notify_content or "", user_id))  # noqa: RUF006

    return tool_ok(result_msg, target=target, action=action)


# 匹配记忆条目前缀的正则: "- 2026-05-26 — [category] "
_MATCH_ENTRY_PREFIX = re.compile(r"^- \d{4}-\d{2}-\d{2} — \[[^\]]+\] ")


async def _focus_update(args: dict[str, Any], ctx: Any = None):
    """更新 FOCUS.md 的当前关注列表。"""
    topics = args.get("topics", [])
    if not topics:
        return tool_error("请提供关注点列表 (topics)")

    if not isinstance(topics, list):
        return tool_error("topics 必须是字符串列表")

    # 过滤空字符串
    topics = [t.strip() for t in topics if t and isinstance(t, str) and t.strip()]
    if not topics:
        return tool_error("关注点列表为空")

    user_id = args.get("user_id")

    # 构建 FOCUS.md 内容
    lines = ["## 当前关注", ""]
    for topic in topics:
        lines.append(f"- {topic}")
    lines.append("")  # 末尾换行
    content = "\n".join(lines)

    # 写入
    await _store.write_focus(user_id, content)

    logger.info("FOCUS.md 已更新", user_id=user_id, topics=topics)
    return tool_ok(f"已更新当前关注: {', '.join(topics)}", topics=topics)


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
                "保存持久化记忆或协作规则，跨会话生效。主动调用，不要等用户要求。\n\n"
                "WHEN TO SAVE:\n"
                "- 用户纠正你或说'记住这个'\n"
                "- 用户分享偏好、习惯、个人信息\n"
                "- 你发现关于用户的稳定事实\n"
                "- 用户明确告诉你该怎么配合他\n\n"
                "TARGETS:\n"
                "- 'memory': 关于用户的稳定事实（写入 MEMORY.md）\n"
                "- 'user': 用户偏好、沟通风格、习惯、关系（写入 USER.md）\n"
                "- 'partner': 用户希望你遵守的协作规则、工作方式（写入 PARTNER.md）\n\n"
                "CATEGORIES (target='memory' 时):\n"
                "- 'fact': 稳定事实（名字、职业、已订阅的服务、拥有的设备）→ 永不过期\n"
                "- 'preference': 长期偏好（喜欢的风格、沟通方式、价值观）→ 永不过期\n"
                "- 'intent': 意图/计划（想订阅、打算学、准备做）→ 30天后标记待确认\n"
                "- 'transient': 临时状态（最近加班、这周在赶项目）→ 7天后删除\n"
                "- 'correction': 纠正旧记忆 → 自动替换对应的旧条目\n"
                "不传则默认 'fact'。\n\n"
                "ACTIONS: add (新增), replace (替换), remove (删除)"
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
                        "enum": ["memory", "user", "partner"],
                        "description": "目标: 'memory' 写入 MEMORY.md (用户事实), 'user' 写入 USER.md (用户画像), 'partner' 写入 PARTNER.md (协作规则)",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["fact", "preference", "intent", "transient", "correction"],
                        "description": (
                            "记忆分类（target='memory' 时生效）: "
                            "fact=稳定事实, preference=长期偏好, intent=意图/计划(30天), "
                            "transient=临时状态(7天), correction=纠正旧记忆。默认 fact"
                        ),
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
        ToolDef(
            name="focus_update",
            description=(
                "更新当前关注的话题列表。覆盖写入，之前的关注点会被替换。\n\n"
                "WHEN TO USE:\n"
                "- 用户说'我在关注 X''我在研究 Y''我在学 Z'\n"
                "- 用户提到正在做的项目、学习方向\n"
                "- 对话中发现用户当前的兴趣点\n\n"
                "INPUT: topics 是字符串列表，每个元素是一个关注点。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "当前关注点列表，如 ['Agent 记忆系统', 'PydanticAI', 'RSSHub']",
                    },
                },
                "required": ["topics"],
            },
            execute=_focus_update,
            read_only=False,
            meta=ToolMeta(always_on=True, risk="write", search_hint="关注点、兴趣、研究方向、学习"),
        ),
    ]
