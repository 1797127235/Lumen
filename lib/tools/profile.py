"""画像工具 — get_profile / update_profile。

阶段 2 改造：直接读 USER.md / MEMORY.md，不再通过 GrowthEvent。
"""

from __future__ import annotations

from typing import Any

from lib.memory.markdown import AsyncMarkdownStore
from lib.memory.understanding import update_ai_understanding
from lib.tools._base import ToolDef, ToolMeta
from shared.logging import get_logger

logger = get_logger(__name__)

_store = AsyncMarkdownStore()


async def _bg_refresh_understanding(user_id: str) -> None:
    """后台刷新 USER.md，失败静默。"""
    try:
        await update_ai_understanding(user_id)
    except Exception as exc:
        logger.debug("USER.md 后台刷新失败", error=str(exc))


async def _get_profile(args: dict[str, Any], deps) -> str:
    user_id = deps.user_id

    # 优先读 USER.md，缺失回退到 MEMORY.md
    user_md = await _store.read_about_you(user_id)
    if user_md.strip():
        return user_md

    memory = await _store.read_memory(user_id)
    if memory.strip():
        return memory

    return "用户画像还是空白，多和我聊聊，我会逐渐了解你。"


async def _update_profile(args: dict[str, Any], deps) -> str:
    fields = {k: v for k in ("nickname", "bio") if (v := args.get(k)) is not None}
    if not fields:
        return "没有需要更新的字段。"

    user_id = deps.user_id
    parts: list[str] = []
    for k, v in fields.items():
        parts.append(f"{k}: {v}")
    text = "; ".join(parts)

    await _store.append_memory_entry(user_id, "profile", text)

    # 触发 USER.md 刷新（后台，不阻塞）
    import asyncio

    asyncio.create_task(_bg_refresh_understanding(user_id))  # noqa: RUF006

    updated = ", ".join(f"{k}={v}" for k, v in fields.items())
    return f"画像已更新：{updated}"


def create_profile_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="get_profile",
            description="获取用户完整画像。画像已在 system prompt 中，通常无需主动调用。",
            input_schema={"type": "object", "properties": {}},
            execute=_get_profile,
            read_only=True,
            meta=ToolMeta(always_on=True, risk="read-only", search_hint="获取画像、用户资料"),
        ),
        ToolDef(
            name="update_profile",
            description="更新用户基础名片。只传有值的字段。可用字段: nickname（昵称）, bio（一句话介绍）",
            input_schema={
                "type": "object",
                "properties": {
                    "nickname": {"type": "string", "description": "用户昵称"},
                    "bio": {"type": "string", "description": "一句话介绍自己"},
                },
            },
            execute=_update_profile,
            read_only=False,
            meta=ToolMeta(always_on=False, risk="write", search_hint="更新画像、修改资料"),
        ),
    ]
