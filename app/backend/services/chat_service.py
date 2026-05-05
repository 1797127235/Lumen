"""对话服务 — SSE 流式对话 + 历史存 DB + 滚动摘要（PydanticAI 版本）"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.agent.deps import CareerOSDeps
from app.backend.agent.pydantic_agent import get_agent
from app.backend.models.conversation import Conversation, Message

logger = logging.getLogger(__name__)

# ── 摘要 ──

# 每次触发生成摘要时，上下文窗口保留最近 20 条，更早的压缩为摘要
_SUMMARY_WINDOW = 20

# 按 conversation_id 隔离的轻量锁，防止并发触发摘要时数据竞争
# MVP: 只增不减，生产环境需加 LRU 或 TTL 清理（<100 对话无影响）
_summary_locks: dict[str, asyncio.Lock] = {}

_SUMMARIZE_PROMPT = """根据以下对话记录更新摘要。只保留：用户背景变化、重要结论和决策、未完成的待办。丢弃闲聊和中间推理。100 字以内，中文。无关紧要则输出"（无重要内容）"

【上次摘要】
{PREV}

【对话记录】
{MSGS}"""

_MAX_MSG_CHARS = 200  # 单条消息送入摘要前的截断长度


def _log_task_error(task: asyncio.Task) -> None:
    """asyncio.Task 的 done_callback：未取消且抛异常时记录日志。"""
    if not task.cancelled() and (exc := task.exception()):
        logger.error("摘要任务异常", exc_info=exc)


async def stream_chat(
    db: AsyncSession,
    user_id: str,
    user_input: str,
    conversation_id: str | None = None,
) -> AsyncIterator[str]:
    """
    SSE 流式对话（PydanticAI 版本）：
    1. 获取/创建会话
    2. 加载历史上下文
    3. PydanticAI Agent 处理（内置工具调用）
    4. 存 DB + 滚动摘要
    """
    # 获取或创建会话
    if conversation_id:
        conv = await db.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            yield _sse_error("会话不存在")
            return
    else:
        conv = Conversation(
            user_id=user_id,
            title=user_input[:30] + "..." if len(user_input) > 30 else user_input,
        )
        db.add(conv)
        await db.flush()

    yield _sse_token("", conv.conversation_id)  # 初始事件（返回 conversation_id）

    # 保存用户消息
    user_message = Message(
        conversation_id=conv.conversation_id,
        role="user",
        content=user_input,
        intent="consultation",
    )
    db.add(user_message)
    conv.message_count = (conv.message_count or 0) + 1
    conv.last_message_at = datetime.now(UTC)
    try:
        await db.commit()
    except Exception:
        logger.exception("保存用户消息失败: conversation_id=%s", conv.conversation_id)
        await db.rollback()
        yield _sse_error("消息保存失败，请稍后重试")
        return

    # PydanticAI Agent 流式处理（不传 message_history，由 dynamic_prompt 处理上下文）
    try:
        agent = get_agent()
        deps = CareerOSDeps(user_id=user_id, db=db)

        full_content = ""
        try:
            async with agent.run_stream(
                user_input,
                deps=deps,
            ) as response:
                async for text in response.stream_text():
                    full_content += text
                    yield _sse_token(text, conv.conversation_id)

        finally:
            # 无论正常完成还是客户端断开，都保存已生成的内容
            if full_content:
                db.add(
                    Message(
                        conversation_id=conv.conversation_id,
                        role="assistant",
                        content=full_content,
                        intent="consultation",
                    )
                )
                conv.message_count = (conv.message_count or 0) + 1
                conv.last_message_at = datetime.now(UTC)
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
                    logger.warning("保存 AI 回复失败 (可能为部分): conversation_id=%s", conv.conversation_id)
    except Exception:
        logger.exception("生成 AI 回复失败: conversation_id=%s", conv.conversation_id)
        await db.rollback()
        yield _sse_error("生成回复失败，请稍后重试")
        return

    # 滚动摘要：fire-and-forget，不阻塞 SSE
    if conv.message_count >= 30 and conv.message_count % 10 == 0:
        task = asyncio.create_task(_summarize_bg(conv.conversation_id))
        task.add_done_callback(_log_task_error)

    yield _sse_done(conv.conversation_id)


async def _load_user_profile(db: AsyncSession, user_id: str) -> dict | None:
    """从 DB 加载用户画像（含 nickname）"""
    from app.backend.models.user import User, UserProfile

    user_result = await db.execute(select(User).where(User.user_id == user_id))
    user = user_result.scalar_one_or_none()

    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        return None
    return {
        "nickname": user.nickname if user else None,
        "grade": profile.grade,
        "school_name": profile.school_name,
        "major": profile.major,
        "target_direction": profile.target_direction,
        "current_skills": profile.current_skills,
    }


async def _summarize_bg(conversation_id: str) -> None:
    """后台摘要：独立 db session，内部重判触发条件防并发重复触发。"""
    from app.backend.db.base import get_async_session_maker

    try:
        async with get_async_session_maker()() as db:
            try:
                conv = await db.get(Conversation, conversation_id)
                if conv is None or conv.message_count < 30 or conv.message_count % 10 != 0:
                    return
                await _summarize_and_persist(db, conv)
            except Exception:
                logger.exception("后台摘要失败: conversation_id=%s", conversation_id)
    finally:
        # 确保即使任务被取消也能清理锁
        _summary_locks.pop(conversation_id, None)


async def _fetch_old_messages(db: AsyncSession, conv: Conversation) -> list[Message]:
    """取窗口外最早 N 条消息（N = 总消息数 - 窗口大小）。"""
    limit = conv.message_count - _SUMMARY_WINDOW
    if limit <= 0:
        return []
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


def _format_messages(messages: list[Message]) -> str:
    """消息列表 → user: xxx\nassistant: xxx，清洗文件名引用防止 LLM 误读。"""
    import re

    def _clean(content: str) -> str:
        content = re.sub(r"[\[【].*?\.(?:pdf|docx?|png|jpg|txt)[\]】]", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\[PDF\s*\d+\]", "", content, flags=re.IGNORECASE)
        return content

    return "\n".join(
        f"{'user' if msg.role == 'user' else 'assistant'}: {_clean((msg.content or '')[:_MAX_MSG_CHARS])}"
        for msg in messages
    )


def _build_summary_prompt(previous: str, old_text: str) -> str:
    """拼接摘要 prompt；用 replace() 避免用户消息含 { } 时与 format() 冲突。"""
    return _SUMMARIZE_PROMPT.replace("{PREV}", previous or "（新对话）").replace("{MSGS}", old_text)


async def _summarize_and_persist(db: AsyncSession, conv: Conversation) -> None:
    """将窗口外的旧消息压缩为摘要，写入 Conversation.summary。"""
    lock = _summary_locks.setdefault(conv.conversation_id, asyncio.Lock())
    async with lock:
        await db.refresh(conv)
        old_messages = await _fetch_old_messages(db, conv)
        if not old_messages:
            return

        prompt = _build_summary_prompt(conv.summary or "", _format_messages(old_messages))

        try:
            from app.backend.agent.llm_router import chat as llm_chat

            result = await llm_chat(
                task_type="memory_summarize",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=256,
            )
            # llm_chat 可能返回 str 或 dict（如果有 tool_calls），确保处理两种情况
            summary = result if isinstance(result, str) else str(result)
            conv.summary = summary.strip() if summary else None
            await db.commit()
            logger.info("摘要已更新: conversation_id=%s, len=%d", conv.conversation_id, len(summary) if summary else 0)
        except asyncio.CancelledError:
            raise
        except Exception:
            await db.rollback()
            logger.warning("摘要生成失败，保留旧摘要: conversation_id=%s", conv.conversation_id)
        finally:
            # 释放锁引用，防止无限增长
            _summary_locks.pop(conv.conversation_id, None)


def _sse_token(content: str, conversation_id: str) -> str:
    return f"data: {json.dumps({'type': 'token', 'content': content, 'conversation_id': conversation_id}, ensure_ascii=False)}\n\n"


def _sse_error(message: str) -> str:
    return f"data: {json.dumps({'type': 'error', 'message': message}, ensure_ascii=False)}\n\n"


def _sse_done(conversation_id: str) -> str:
    return f"data: {json.dumps({'type': 'done', 'conversation_id': conversation_id}, ensure_ascii=False)}\n\n"
