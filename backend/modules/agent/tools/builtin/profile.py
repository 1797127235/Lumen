"""画像工具 Handlers — 获取和更新用户画像。"""

from __future__ import annotations

import re
from typing import Any

from backend.core.logging import get_logger
from backend.modules.agent.tools.builtin.schemas import GetProfileArgs, UpdateProfileArgs
from backend.modules.agent.tools.core.context import ToolRuntimeContext
from backend.modules.memory import get_memory

logger = get_logger(__name__)


async def handle_get_profile(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """获取用户画像。"""
    # 优先使用缓存
    cached = ctx.tool_state.get("build_context_cache", "")
    if cached and cached.strip():
        return _strip_context_tags(cached)

    memory_instance = get_memory()
    context = await memory_instance.build_context(ctx.user_id)
    if context.strip():
        return _strip_context_tags(context)

    return "用户画像为空，请先上传简历或手动填写画像。"


# GetProfileArgs 仅用于类型标注，实际无需使用 typed 变量
_ = GetProfileArgs


async def handle_update_profile(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """更新用户画像。"""
    from backend.modules.profile.schemas import ProfilePayload

    typed: UpdateProfileArgs = args  # type: ignore[assignment]
    fields: dict[str, Any] = {}
    for name in [
        "school_name",
        "major",
        "grade",
        "graduation_year",
        "school_level",
        "target_direction",
        "target_company_level",
        "city",
        "gpa",
        "ranking",
        "awards",
        "bio",
        "english_level",
        "expected_salary",
    ]:
        val = typed.get(name)
        if val is not None:
            fields[name] = val

    if not fields:
        return "没有需要更新的字段。"

    allowed_keys = set(ProfilePayload.model_fields.keys())
    known = {k: v for k, v in fields.items() if k in allowed_keys}
    discarded = [k for k in fields if k not in allowed_keys]
    if discarded:
        logger.warning("update_profile discarded unknown keys", discarded=discarded)

    try:
        validated = ProfilePayload.model_validate(known)
    except Exception as e:
        return f"[工具错误/VALIDATION_ERROR] 画像字段校验失败：{e}"

    memory = get_memory()
    event = await memory.remember(
        user_id=ctx.user_id,
        event_type="profile_updated",
        entity_type="profile",
        entity_id="profile_fields",
        payload=validated.model_dump(exclude_none=True),
        source="Agent工具",
        db=ctx.db,
    )
    if event and event.id is not None:
        pending = ctx.tool_state.setdefault("pending_event_ids", [])
        pending.append(str(event.id))
        ctx.tool_state["build_context_cache"] = ""
        updated_keys = ", ".join(validated.model_dump(exclude_none=True).keys())
        return f"画像已更新：{updated_keys}"

    return "画像内容没有变化，跳过更新。"


def _strip_context_tags(text: str) -> str:
    """去除 <memory-context> 包裹标签。"""
    text = re.sub(r"^<memory-context>\n\[System note:[^\]]*\]\n", "", text)
    return text.removesuffix("\n</memory-context>")
