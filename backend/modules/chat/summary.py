"""对话摘要服务 — 滚动摘要生成。
从 services/chat_service.py 提取。当 conv 消息数达到阈值时，
将窗口外的旧消息压缩为摘要并写入 Conversation.summary。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.logging import get_logger
from backend.modules.chat.models import Conversation, Message

logger = get_logger(__name__)
_SUMMARY_WINDOW = 10
_MAX_SUMMARY_LOCKS = 128
_MAX_MSG_CHARS = 200

_SUMMARIZE_PROMPT = (
    "根据以下对话记录更新摘要。只保留：用户背景变化、重要结论和决策、未完成的待办。"
    "丢弃闲聊和中间推理。100 字以内，中文。无关紧要则输出「无重要内容」\n\n"
    "【上次摘要】\n{PREV}\n\n"
    "【对话记录】\n{MSGS}"
)

_summary_locks: dict[str, asyncio.Lock] = {}


def _prune_summary_locks() -> None:
    """清理已释放的摘要锁。"""
    stale = [cid for cid, lock in _summary_locks.items() if not lock.locked()]
    for cid in stale:
        del _summary_locks[cid]


def _format_messages(messages: list[Message]) -> str:
    """消息列表 → user: xxx\nassistant: xxx。"""
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
    """拼接摘要 prompt。用 replace() 避免用户消息含 { } 时与 format() 冲突。"""
    return _SUMMARIZE_PROMPT.replace("{PREV}", previous or "（新对话）").replace("{MSGS}", old_text)


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


async def _summarize_and_persist(db: AsyncSession, conv: Conversation) -> None:
    """将窗口外的旧消息压缩为摘要，写入 Conversation.summary。"""
    if len(_summary_locks) >= _MAX_SUMMARY_LOCKS:
        _prune_summary_locks()
    lock = _summary_locks.setdefault(conv.conversation_id, asyncio.Lock())
    async with lock:
        await db.refresh(conv)
        old_messages = await _fetch_old_messages(db, conv)
        if not old_messages:
            return

        prompt = _build_summary_prompt(conv.summary or "", _format_messages(old_messages))

        try:
            import litellm

            from backend.core.config import build_llm_call_params

            llm_params = build_llm_call_params()
            kwargs: dict = {
                "model": llm_params["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 256,
                "api_key": llm_params["api_key"],
                "stream": False,
                "timeout": 30,
            }
            if llm_params["base_url"]:
                kwargs["base_url"] = llm_params["base_url"]

            response = await litellm.acompletion(**kwargs)
            response = cast(Any, response)
            summary = response.choices[0].message.content or ""
            conv.summary = summary.strip() if summary else None
            await db.commit()
            logger.info("摘要已更新", conversation_id=conv.conversation_id, length=len(summary) if summary else 0)
        except asyncio.CancelledError:
            raise
        except Exception:
            await db.rollback()
            logger.warning("摘要生成失败，保留旧摘要", conversation_id=conv.conversation_id)


def _should_summarize(message_count: int) -> bool:
    """分级触发策略：第 10 条首次触发，之后每 10 条触发一次。

    早期（≤30）快速积累摘要避免冷启动丢失上下文，
    后期稳定间隔降低开销。
    """
    if message_count < _SUMMARY_WINDOW:
        return False
    return message_count % _SUMMARY_WINDOW == 0


async def summarize_background(conversation_id: str) -> None:
    """后台摘要任务：自开 session，内部重判触发条件防并发重复触发。"""
    from backend.core.db import get_async_session_maker

    try:
        async with get_async_session_maker()() as db:
            try:
                conv = await db.get(Conversation, conversation_id)
                if conv is None or not _should_summarize(conv.message_count):
                    return
                await _summarize_and_persist(db, conv)
            except Exception:
                logger.exception("后台摘要失败", conversation_id=conversation_id)
    finally:
        _summary_locks.pop(conversation_id, None)
