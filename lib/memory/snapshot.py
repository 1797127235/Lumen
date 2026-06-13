"""Agent 系统提示快照 — 分层注入（固定块 + 近期上下文）。

L0 固定块：用户画像聚合（USER.md / MEMORY.md）
L1 近期上下文：最近对话的摘要（Conversation + Message，非原始事件）

Hermes-Pure 架构下，snapshot.py 作为薄封装层：
- L0 委托给 BuiltinMemoryProvider（通过 MemoryManager）
- L1 保留原有对话查询逻辑
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from core.db import get_async_session_maker
from shared.logging import get_logger

logger = get_logger(__name__)

_CONTEXT_MAX_CONVERSATIONS = 5
_CONTEXT_MAX_MESSAGES_PER_CONV = 3
_CONTEXT_MAX_AGE_DAYS = 7
_CONTEXT_MAX_CHARS = 600

_CACHE_TTL_MINUTES = 30
_MAX_CACHE_SIZE = 100


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


# ── L0: 固定块（数据源 = about_you.md / memory.md）────────────────

_MIN_ABOUT_YOU_CHARS = 30


async def _build_fixed_block(user_id: str, nickname: str | None = None) -> str:
    """L0 固定块：同时注入 USER.md + MEMORY.md。

    由 BuiltinMemoryProvider 读取文件内容。
    """
    from lib.memory import get_memory_manager

    parts: list[str] = []
    if nickname:
        parts.append(f"【用户称呼】你可以称呼用户为「{nickname}」，但不必每次都叫。")

    manager = get_memory_manager()
    l0_block = await manager.build_system_prompt(user_id=user_id)
    l0_block = _strip_meta(l0_block)

    if l0_block and _has_substantive_content(l0_block):
        parts.append(f"## AI 对你的理解\n{l0_block.strip()}")

    return "\n\n".join(parts) if parts else ""


def _strip_meta(text: str) -> str:
    """移除元数据注释行。"""

    lines = text.splitlines()
    filtered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        if stripped.startswith("<!--"):
            continue
        filtered.append(line)
    return "\n".join(filtered)


def _has_substantive_content(text: str) -> bool:
    """判断文本是否有实质内容（非纯模板占位符）。"""
    import re as _re

    text = text.strip()
    if len(text) <= _MIN_ABOUT_YOU_CHARS:
        return False
    stripped = _re.sub(r"（待填写）", "", text)
    stripped = _re.sub(r"_暂无记录_", "", stripped)
    stripped = stripped.strip()
    return len(stripped) > _MIN_ABOUT_YOU_CHARS


# ── L1: 近期上下文（数据源 = Conversation + Message，通过依赖注入解耦）──


def _build_context_block(contexts: list[ConversationContext]) -> tuple[str, set[str]]:
    """从最近对话中提取上下文摘要。

    与 L0 不同：L0 来自 USER.md + MEMORY.md（「用户是谁」），
    L1 从对话记录提取「最近在聊什么」— 数据源分离，避免重复注入。
    """
    if not contexts:
        return "", set()

    lines: list[str] = ["## 近期对话"]
    conv_ids: set[str] = set()
    total_chars = 0

    for ctx in contexts:
        if total_chars >= _CONTEXT_MAX_CHARS:
            break

        msgs = ctx.messages
        if not msgs:
            continue

        title = ctx.title
        if not title:
            user_msgs = [m for m in msgs if m.get("role") == "user"]
            if user_msgs:
                title = (user_msgs[0].get("content") or "")[:40]
            else:
                title = "未命名对话"

        if ctx.summary:
            line = f"- **{title}**：{ctx.summary[:80]}"
        else:
            msg_parts: list[str] = []
            for msg in msgs[:_CONTEXT_MAX_MESSAGES_PER_CONV]:
                content = msg.get("content")
                if content:
                    role_label = "用户" if msg.get("role") == "user" else "AI"
                    msg_parts.append(f"{role_label}：{content[:50]}")
            content_hint = "；".join(msg_parts)
            line = f"- **{title}**：{content_hint[:80]}" if content_hint else f"- **{title}**"

        if len(line) > 120:
            line = line[:117] + "…"

        lines.append(line)
        conv_ids.add(ctx.conversation_id)
        total_chars += len(line)

    if len(lines) <= 1:
        return "", set()

    return "\n".join(lines), conv_ids


async def _default_fetch_recent_conversations(user_id: str, db) -> list[ConversationContext]:
    """默认 fetcher：延迟导入 chat 模型，转换为 ConversationContext。"""
    cutoff = datetime.now(UTC) - timedelta(days=_CONTEXT_MAX_AGE_DAYS)

    from lib.chat.models import Conversation, Message

    conv_stmt = (
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.status == "active",
            Conversation.last_message_at >= cutoff,
        )
        .order_by(Conversation.last_message_at.desc())
        .limit(_CONTEXT_MAX_CONVERSATIONS)
    )
    conv_result = await db.execute(conv_stmt)
    conversations = list(conv_result.scalars().all())

    if not conversations:
        return []

    conv_ids = [c.conversation_id for c in conversations]
    msg_stmt = select(Message).where(Message.conversation_id.in_(conv_ids)).order_by(Message.created_at.desc())
    msg_result = await db.execute(msg_stmt)
    all_messages = list(msg_result.scalars().all())

    messages_by_conv: dict[str, list[Message]] = {}
    for msg in all_messages:
        msgs = messages_by_conv.setdefault(msg.conversation_id, [])
        if len(msgs) < _CONTEXT_MAX_MESSAGES_PER_CONV:
            msgs.append(msg)

    contexts: list[ConversationContext] = []
    for conv in conversations:
        msgs = messages_by_conv.get(conv.conversation_id, [])
        contexts.append(
            ConversationContext(
                conversation_id=conv.conversation_id,
                title=conv.title,
                summary=conv.summary,
                messages=[{"role": m.role, "content": m.content or ""} for m in msgs],
            )
        )

    return contexts


async def _fetch_recent_conversations(user_id: str, db) -> list[ConversationContext]:
    """获取近期对话上下文。

    优先使用注入的 fetcher，未注入时使用默认实现（延迟导入 chat 模型）。
    """
    if _conversation_fetcher is not None:
        return await _conversation_fetcher(user_id, db)
    return await _default_fetch_recent_conversations(user_id, db)


# ── 构建快照 ──────────────────────────────────────────────────────


async def _evict_expired_cache() -> None:
    """驱逐所有过期的缓存条目（定期清理，防止内存泄漏）。"""
    now = datetime.now(UTC)
    async with _cache_lock:
        expired = [k for k, v in _static_cache.items() if (now - v.created_at) >= timedelta(minutes=_CACHE_TTL_MINUTES)]
        for k in expired:
            del _static_cache[k]


async def build_snapshot(user_id: str) -> str:
    """构建用户画像快照（L0 + L1）。

    Hermes-Pure 架构下保留为兼容层，内部使用 MemoryManager 获取 L0。
    """
    await _evict_expired_cache()
    async with _cache_lock:
        cached = _static_cache.get(user_id)
        if cached and (datetime.now(UTC) - cached.created_at) < timedelta(minutes=_CACHE_TTL_MINUTES):
            cached.last_accessed = datetime.now(UTC)
            return cached.content

    # 查询用户 nickname + 近期对话
    nickname: str | None = None
    try:
        from lib.profile.models import User

        async with get_async_session_maker()() as db:
            user_result = await db.execute(select(User).where(User.user_id == user_id))
            user_obj = user_result.scalar_one_or_none()
            if user_obj:
                nickname = user_obj.nickname
            contexts = await _fetch_recent_conversations(user_id, db)
    except Exception:
        contexts = []

    fixed_block = await _build_fixed_block(user_id, nickname)

    if not fixed_block and not contexts:
        content = "【用户画像为空】"
        await _cache_insert(_CacheEntry(user_id=user_id, content=content, created_at=datetime.now(UTC)))
        return content

    context_block, conv_ids = _build_context_block(contexts)

    parts = [p for p in [fixed_block, context_block] if p]
    content = "\n\n".join(parts) if parts else "【用户画像为空】"

    await _cache_insert(
        _CacheEntry(
            user_id=user_id,
            content=content,
            created_at=datetime.now(UTC),
            context_conv_ids=conv_ids,
        )
    )
    return content
