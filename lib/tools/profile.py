"""画像工具 — get_profile / update_profile。"""

from __future__ import annotations

import re
from typing import Any

from lib.memory import get_memory
from lib.tools._base import ToolDef, ToolMeta, tool_error
from shared.logging import get_logger

logger = get_logger(__name__)


async def _get_profile(args: dict[str, Any], deps) -> str:
    if deps.build_context_cache and deps.build_context_cache.strip():
        return _strip_tags(deps.build_context_cache)

    memory = get_memory()
    context = await memory.build_context(deps.user_id)
    if context.strip():
        return _strip_tags(context)

    return "用户画像还是空白，多和我聊聊，我会逐渐了解你。"


async def _update_profile(args: dict[str, Any], deps) -> str:
    from lib.profile.schemas import ProfilePayload

    fields = {k: v for k in ("nickname", "bio") if (v := args.get(k)) is not None}
    if not fields:
        return "没有需要更新的字段。"

    allowed = set(ProfilePayload.model_fields)
    known = {k: v for k, v in fields.items() if k in allowed}
    discarded = [k for k in fields if k not in allowed]
    if discarded:
        logger.warning("update_profile discarded unknown keys", discarded=discarded)

    try:
        validated = ProfilePayload.model_validate(known)
    except Exception as e:
        return tool_error(f"字段校验失败：{e}", "VALIDATION_ERROR")

    memory = get_memory()
    event = await memory.remember(
        user_id=deps.user_id,
        event_type="profile_updated",
        entity_type="profile",
        entity_id="profile_fields",
        payload=validated.model_dump(exclude_none=True),
        source="Agent工具",
        db=deps.db,
    )
    if event and event.id is not None:
        deps.pending_event_ids.append(str(event.id))
        deps.build_context_cache = ""
        updated = ", ".join(validated.model_dump(exclude_none=True))
        return f"画像已更新：{updated}"

    return "画像内容没有变化，跳过更新。"


def _strip_tags(text: str) -> str:
    text = re.sub(r"^<memory-context>\n\[System note:[^\]]*\]\n", "", text)
    return text.removesuffix("\n</memory-context>")


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
