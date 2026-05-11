"""后台记忆审查服务。

从 services/chat_service.py 提取。
当 Agent 在对话中未主动保存记忆时，后台 fork Agent 审查本轮对话，
判断是否有值得保存的用户信息。
"""

from __future__ import annotations

from backend.logging_config import get_logger

logger = get_logger(__name__)

_REVIEW_PROMPT = (
    "审查上一轮对话，判断是否包含值得保存的用户信息。\n\n"
    "重点关注：\n"
    "1. 用户是否透露了关于自己的新信息（目标、技能、经历、偏好、状态）？\n"
    "2. 用户是否纠正了你、表达了偏好、或做出了决策？\n\n"
    "如果有值得保存的信息，调用 memory_save 或 update_profile 保存。\n"
    "如果没有任何新信息，回复「无需保存」。\n\n"
    "【对话】\n"
    "用户：{user_message}\n\n"
    "助手：{assistant_response}"
)


async def background_memory_review(
    user_id: str,
    user_message: str,
    assistant_response: str,
    conversation_id: str,
) -> None:
    """后台审查本轮对话，判断是否有值得保存的记忆。"""
    try:
        from backend.agent.deps import LumenDeps
        from backend.agent.pydantic_agent import get_agent
        from backend.db import get_async_session_maker
        from backend.memory import get_memory

        async with get_async_session_maker()() as db:
            agent = get_agent()
            deps = LumenDeps(
                user_id=user_id,
                db=db,
                conversation_id=conversation_id,
                current_user_input=user_message,
            )

            prompt = _REVIEW_PROMPT.format(
                user_message=user_message,
                assistant_response=assistant_response,
            )

            await agent.run(prompt, deps=deps)

            if deps.pending_event_ids:
                await get_memory().sync_projections(user_id, deps.pending_event_ids)
                logger.info(
                    "后台审查已保存 %d 条记忆",
                    len(deps.pending_event_ids),
                    conversation_id=conversation_id,
                )
            await db.commit()
    except Exception:
        logger.exception("后台记忆审查失败", conversation_id=conversation_id)
