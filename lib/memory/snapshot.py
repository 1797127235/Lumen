"""Agent 近期对话上下文快照 — L1 only。

Phase 1 改造后，L0 固定块（用户画像 + PARTNER.md）已移入 system prompt 的
context suffix，由 lib/agent/system_prompt_builder.py 按用户缓存。

本模块只负责：
- L1 近期上下文：最近对话的摘要（Conversation + Message，非原始事件）

通过 `set_conversation_fetcher()` 由 chat 模块注入自定义查询逻辑，
解耦 memory 与 chat 模块的 ORM 模型。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)

_CONTEXT_MAX_CONVERSATIONS = 5
_CONTEXT_MAX_MESSAGES_PER_CONV = 3
_CONTEXT_MAX_AGE_DAYS = 7
_CONTEXT_MAX_CHARS = 600

# L1 摘要化配置：当原始内容超过阈值时，调用 LLM 压缩成一段摘要
_SUMMARIZE_THRESHOLD_TOKENS = 350
_SUMMARY_MAX_TOKENS = 180
_SUMMARY_MAX_CHARS = 450

_CACHE_TTL_MINUTES = 30
_MAX_CACHE_SIZE = 100


# ── token 估算（轻量，避免引入 tiktoken）────────────────────────────


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文按 2 字符/token，其他按 4 字符/token。"""
    if not text:
        return 0
    # 简单按字符加权：中文字符计 1/2，其他计 1/4
    cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - cn_chars
    return max(1, cn_chars // 2 + other_chars // 4)


@dataclass
class ConversationContext:
    """近期对话上下文 — 解耦 snapshot.py 与 chat 模块的 ORM 模型。"""

    conversation_id: str
    title: str | None
    summary: str | None
    messages: list[dict[str, str]]


ConversationFetcher = Callable[[str, Any], Awaitable[list[ConversationContext]]]
"""获取近期对话上下文的 fetcher 协议。

通过 `set_conversation_fetcher()` 注入自定义实现，默认使用内部延迟导入的查询逻辑。
"""

_conversation_fetcher: ConversationFetcher | None = None


def set_conversation_fetcher(fetcher: ConversationFetcher) -> None:
    """注入自定义的对话上下文获取器。

    由 chat 模块在初始化时调用，将查询逻辑从 snapshot.py 中彻底解耦。
    未注入时，snapshot.py 使用内部默认实现（延迟导入 chat 模型）。
    """
    global _conversation_fetcher
    _conversation_fetcher = fetcher


@dataclass
class _CacheEntry:
    user_id: str
    content: str
    created_at: datetime
    last_accessed: datetime = field(default_factory=lambda: datetime.now(UTC))
    context_conv_ids: set[str] = field(default_factory=set)


_static_cache: dict[str, _CacheEntry] = {}
_cache_lock = asyncio.Lock()


async def _cache_insert(entry: _CacheEntry) -> None:
    """插入缓存条目，超出上限时驱逐最久未访问的条目（LRU）。"""
    async with _cache_lock:
        if len(_static_cache) >= _MAX_CACHE_SIZE:
            lru_user = min(_static_cache, key=lambda k: _static_cache[k].last_accessed)
            del _static_cache[lru_user]
        _static_cache[entry.user_id] = entry


async def invalidate_cache(user_id: str) -> None:
    async with _cache_lock:
        _static_cache.pop(user_id, None)


# ═══════════════════════════════════════════════════════════════════
#  L1: 近期对话上下文
# ═══════════════════════════════════════════════════════════════════


async def build_recent_context(user_id: str) -> str:
    """构建 L1 近期对话上下文摘要。"""
    await _evict_expired_cache()
    async with _cache_lock:
        cached = _static_cache.get(user_id)
        if cached and (datetime.now(UTC) - cached.created_at) < timedelta(minutes=_CACHE_TTL_MINUTES):
            cached.last_accessed = datetime.now(UTC)
            return cached.content

    # 查询近期对话
    contexts: list[ConversationContext] = []
    try:
        if _conversation_fetcher is not None:
            from core.db import get_async_session_maker

            async with get_async_session_maker()() as db:
                contexts = await _conversation_fetcher(user_id, db)
        else:
            contexts = await _fetch_recent_conversations_default(user_id)
    except Exception:
        logger.debug("build_recent_context failed", user_id=user_id)
        contexts = []

    context_block, conv_ids = _build_context_block(contexts)

    if not context_block:
        content = ""
        await _cache_insert(
            _CacheEntry(
                user_id=user_id,
                content=content,
                created_at=datetime.now(UTC),
                context_conv_ids=conv_ids,
            )
        )
        return content

    # Phase 3：当 L1 过长时，用 LLM 压缩成摘要，减少每轮 context_frame 新增 token
    summarized = await _maybe_summarize_context(context_block)

    await _cache_insert(
        _CacheEntry(
            user_id=user_id,
            content=summarized,
            created_at=datetime.now(UTC),
            context_conv_ids=conv_ids,
        )
    )
    return summarized


# 保留旧别名，避免外部调用方改动
build_snapshot = build_recent_context


# ── 默认查询逻辑（延迟导入，避免启动时循环依赖）──────────────────────


async def _fetch_recent_conversations_default(user_id: str) -> list[ConversationContext]:
    from core.db import get_async_session_maker

    async with get_async_session_maker()() as db:
        return await _fetch_recent_conversations(user_id, db)


async def _fetch_recent_conversations(user_id: str, db) -> list[ConversationContext]:
    """查询用户最近的对话及其消息。"""
    from sqlalchemy import select

    from lib.chat.models import Conversation, Message

    cutoff = datetime.now(UTC) - timedelta(days=_CONTEXT_MAX_AGE_DAYS)

    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.updated_at >= cutoff,
            Conversation.is_deleted.is_(False),
        )
        .order_by(Conversation.updated_at.desc())
        .limit(_CONTEXT_MAX_CONVERSATIONS)
    )
    conversations = result.scalars().all()

    contexts: list[ConversationContext] = []
    for conv in conversations:
        msg_result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conv.conversation_id,
                Message.role.in_(["user", "assistant"]),
                Message.is_deleted.is_(False),
            )
            .order_by(Message.created_at.desc())
            .limit(_CONTEXT_MAX_MESSAGES_PER_CONV)
        )
        messages = list(reversed(msg_result.scalars().all()))

        contexts.append(
            ConversationContext(
                conversation_id=conv.conversation_id,
                title=conv.title,
                summary=conv.summary,
                messages=[
                    {
                        "role": msg.role,
                        "content": (msg.content or "")[:_CONTEXT_MAX_CHARS],
                    }
                    for msg in messages
                ],
            )
        )

    return contexts


def _build_context_block(contexts: list[ConversationContext]) -> tuple[str, set[str]]:
    """把近期对话上下文格式化为 Markdown 字符串。"""
    if not contexts:
        return "", set()

    lines: list[str] = ["## 近期相关对话"]
    conv_ids: set[str] = set()

    for ctx in contexts:
        conv_ids.add(ctx.conversation_id)
        title = ctx.title or "无标题对话"
        summary = ctx.summary
        lines.append(f"\n### {title}")
        if summary:
            lines.append(f"摘要：{summary[:_CONTEXT_MAX_CHARS]}")
        for msg in ctx.messages:
            role_label = "用户" if msg["role"] == "user" else "AI"
            lines.append(f"- {role_label}: {msg['content'][:_CONTEXT_MAX_CHARS]}")

    return "\n".join(lines), conv_ids


async def _maybe_summarize_context(text: str) -> str:
    """当 L1 上下文过长时，调用 LLM 压缩成一段摘要。"""
    if _estimate_tokens(text) <= _SUMMARIZE_THRESHOLD_TOKENS:
        return text

    try:
        from core.config import get_settings
        from lib.llm.client import LLMClient

        settings = get_settings()
        client = LLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
        )
        prompt = (
            "把下面这段近期对话总结成一段简洁的中文摘要，"
            f"不超过 {_SUMMARY_MAX_CHARS} 个汉字。"
            "保留用户提到的关键事实、偏好、计划和情绪。"
            "只输出摘要，不要解释、不要分段。\n\n---\n\n"
            f"{text}\n\n---\n\n摘要："
        )
        resp = await client.chat(
            messages=[
                {"role": "system", "content": "你是一位高效的对话摘要助手。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=_SUMMARY_MAX_TOKENS,
        )
        summary = (resp.content or "").strip()
        if summary:
            logger.debug(
                "L1 context summarized",
                original_tokens=_estimate_tokens(text),
                summary_tokens=_estimate_tokens(summary),
            )
            return f"## 近期相关对话（摘要）\n\n{summary[:_SUMMARY_MAX_CHARS]}"
    except Exception:
        logger.debug("L1 summarization failed, falling back to truncation", exc_info=True)

    # 失败或 LLM 返回空：硬截断到阈值附近
    fallback = text
    while _estimate_tokens(fallback) > _SUMMARIZE_THRESHOLD_TOKENS and len(fallback) > 200:
        fallback = fallback[: int(len(fallback) * 0.9)]
    return fallback


async def _evict_expired_cache() -> None:
    """驱逐过期缓存条目。"""
    now = datetime.now(UTC)
    async with _cache_lock:
        expired = [
            user_id
            for user_id, entry in _static_cache.items()
            if (now - entry.created_at) >= timedelta(minutes=_CACHE_TTL_MINUTES)
        ]
        for user_id in expired:
            del _static_cache[user_id]
