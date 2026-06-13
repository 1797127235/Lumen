"""画像工具 — get_profile / update_profile。

阶段 2 改造：
- USER.md 增加 YAML frontmatter（结构化键值对）+ body（AI 综合画像）
- update_profile 读写 frontmatter（覆盖式更新）
- get_profile 返回 frontmatter 结构化摘要
- memory(action="add", target="user") 保留叙事追加能力
"""

from __future__ import annotations

from typing import Any

from lib.agent.system_prompt_builder import invalidate_system_prompt_cache
from lib.memory.markdown import AsyncMarkdownStore, _dump_frontmatter, _parse_frontmatter
from lib.memory.understanding import update_ai_understanding
from lib.tools._base import ToolDef, ToolMeta, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)

_store = AsyncMarkdownStore()

# 允许的画像字段
_PROFILE_FIELDS = {
    "nickname": "昵称",
    "bio": "一句话介绍",
    "birthday": "生日",
    "city": "所在城市",
    "occupation": "职业",
    "company": "公司",
    "interests": "兴趣爱好",
    "communication_style": "沟通偏好",
    "goals": "近期目标",
}


async def _bg_refresh_understanding(
    user_id: str,
    *,
    exclude_conversation_id: str | None = None,
) -> None:
    """后台刷新 USER.md，失败静默。"""
    try:
        await update_ai_understanding(user_id, exclude_conversation_id=exclude_conversation_id)
    except Exception as exc:
        logger.debug("USER.md 后台刷新失败", error=str(exc))


async def _get_profile(args: dict[str, Any], ctx: Any = None):
    """返回结构化画像摘要（frontmatter 内容）。"""
    user_id = args.get("user_id")

    # 读取 USER.md
    raw = await _store.read_about_you(user_id)
    if not raw.strip():
        return tool_ok("用户画像还是空白，多和我聊聊，我会逐渐了解你。")

    frontmatter, body = _parse_frontmatter(raw)

    if not frontmatter:
        # 旧格式：没有 frontmatter，返回简短提示
        return tool_ok(f"已读取用户画像，但目前缺少结构化信息。可用 update_profile 补充。现有画像约 {len(body)} 字。")

    # 构建结构化摘要
    parts: list[str] = []
    for key, label in _PROFILE_FIELDS.items():
        val = frontmatter.get(key)
        if val is not None and val != "":
            if isinstance(val, list):
                val = "、".join(val)
            parts.append(f"- {label}: {val}")

    if not parts:
        return tool_ok("结构化画像尚未填写，可用 update_profile 补充。")

    return tool_ok("用户画像:\n" + "\n".join(parts), frontmatter=frontmatter)


async def _update_profile(args: dict[str, Any], ctx: Any = None):
    """更新结构化画像（覆盖 frontmatter 字段）。"""
    user_id = args.get("user_id")
    conversation_id = getattr(ctx, "conversation_id", None) or ""

    # 提取传入的字段（支持 null 清空）
    updates: dict[str, Any] = {}
    for key in _PROFILE_FIELDS:
        if key in args:
            val = args[key]
            if val is not None and val != "":
                updates[key] = val
            else:
                # null / 空字符串 → 标记为删除
                updates[key] = None

    if not updates:
        return tool_ok("没有需要更新的字段。可用字段: " + ", ".join(_PROFILE_FIELDS.keys()))

    # 读取现有 frontmatter
    raw = await _store.read_about_you(user_id)
    if raw.strip():
        frontmatter, body = _parse_frontmatter(raw)
    else:
        frontmatter, body = {}, ""

    # 应用更新
    changed: list[str] = []
    for key, val in updates.items():
        old_val = frontmatter.get(key)
        if val is None:
            if key in frontmatter:
                del frontmatter[key]
                changed.append(f"{key}={old_val} → (已删除)")
        elif old_val != val:
            frontmatter[key] = val
            changed.append(f"{key}={old_val} → {val}")

    if not changed:
        return tool_ok("字段值未变化。")

    # 写回 USER.md（frontmatter + body）
    meta = ""
    if raw.strip() and "<!-- lumen-meta:" in raw:
        import re

        meta_match = re.search(r"^<!-- lumen-meta:.*?-->\n?", raw)
        if meta_match:
            meta = meta_match.group(0)

    new_content = meta
    if frontmatter:
        new_content += "---\n" + _dump_frontmatter(frontmatter) + "\n---\n\n"
    new_content += body.strip()

    await _store.write_about_you(user_id, new_content)

    # 使 system prompt 缓存失效，但保留当前 conversation 的缓存（会话冻结）
    invalidate_system_prompt_cache(user_id, exclude_conversation_id=conversation_id)

    # 触发 USER.md 刷新（后台，不阻塞）
    import asyncio
    import json

    asyncio.create_task(  # noqa: RUF006
        _bg_refresh_understanding(user_id, exclude_conversation_id=conversation_id or None)
    )

    # 镜像写入事件给外部 memory provider
    try:
        from lib.memory import get_memory_manager

        manager = get_memory_manager()
        asyncio.create_task(  # noqa: RUF006
            manager.on_memory_write(
                "update_profile",
                "user",
                json.dumps(changed, ensure_ascii=False),
                metadata={"user_id": user_id, "updated_fields": list(updates.keys())},
            )
        )
    except Exception as exc:
        logger.debug("update_profile on_memory_write 镜像失败", error=str(exc))

    return tool_ok(
        "画像已更新: " + "; ".join(changed),
        updated_fields=list(updates.keys()),
    )


def create_profile_tools() -> list[ToolDef]:
    properties: dict[str, Any] = {
        key: {"type": "string", "description": label} for key, label in _PROFILE_FIELDS.items()
    }

    return [
        ToolDef(
            name="get_profile",
            description="获取用户结构化画像（frontmatter）。简洁摘要，非全文。",
            input_schema={"type": "object", "properties": {}},
            execute=_get_profile,
            read_only=True,
            meta=ToolMeta(always_on=True, risk="read-only", search_hint="获取画像、用户资料"),
        ),
        ToolDef(
            name="update_profile",
            description=(
                "更新用户结构化画像字段。只传有值的字段，不传的不变。"
                "可用字段: " + ", ".join(f"{k}({v})" for k, v in _PROFILE_FIELDS.items())
            ),
            input_schema={
                "type": "object",
                "properties": properties,
            },
            execute=_update_profile,
            read_only=False,
            meta=ToolMeta(always_on=True, risk="write", search_hint="更新画像、修改资料"),
        ),
    ]
