"""对话服务 — SSE 流式对话 + 历史存 DB + 滚动摘要"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.agent.deps import LumenDeps
from app.backend.logging_config import get_logger
from app.backend.models.conversation import Conversation, Message

logger = get_logger(__name__)

# ── 摘要 ──

# 每次触发生成摘要时，上下文窗口保留最近 20 条，更早的压缩为摘要
_SUMMARY_WINDOW = 20

# 按 conversation_id 隔离的轻量锁，防止并发触发摘要时数据竞争
# 上限 128 个对话锁——插入时校验，超限则清理已完成（unlocked）的锁
_MAX_SUMMARY_LOCKS = 128
_summary_locks: dict[str, asyncio.Lock] = {}


def _prune_summary_locks() -> None:
    """清理已完成（未持有）的摘要锁，释放内存。"""
    stale = [cid for cid, lock in _summary_locks.items() if not lock.locked()]
    for cid in stale:
        del _summary_locks[cid]


_SUMMARIZE_PROMPT = """根据以下对话记录更新摘要。只保留：用户背景变化、重要结论和决策、未完成的待办。丢弃闲聊和中间推理。100 字以内，中文。无关紧要则输出"（无重要内容）"

【上次摘要】
{PREV}

【对话记录】
{MSGS}"""

_MAX_MSG_CHARS = 200  # 单条消息送入摘要前的截断长度

# ── 后台记忆审查 ──

_REVIEW_PROMPT = """审查上一轮对话，判断是否包含值得保存的用户信息。

重点关注：
1. 用户是否透露了关于自己的新信息（目标、技能、经历、偏好、状态）？
2. 用户是否纠正了你、表达了偏好、或做出了决策？

如果有值得保存的信息，调用 memory_save 或 update_profile 保存。
如果没有任何新信息，回复「无需保存」。

【对话】
用户：{user_message}

助手：{assistant_response}"""


async def _background_memory_review(
    user_id: str,
    user_message: str,
    assistant_response: str,
    conversation_id: str,
) -> None:
    """后台审查本轮对话，判断是否有值得保存的记忆。

    仅在 Agent 本轮未主动调用 memory_save/update_profile 时触发。
    使用独立 db session，不阻塞用户看到回复。
    """
    try:
        from app.backend.db.base import get_async_session_maker

        async with get_async_session_maker()() as db:
            from app.backend.agent.deps import LumenDeps
            from app.backend.agent.pydantic_agent import get_agent

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

            # 如果审查 Agent 调了工具 → 触发投影
            if deps.pending_event_ids:
                from app.backend.memory import get_memory

                await get_memory().sync_projections(user_id, deps.pending_event_ids)
                logger.info(
                    "后台审查已保存 %d 条记忆",
                    len(deps.pending_event_ids),
                    conversation_id=conversation_id,
                )
            await db.commit()
    except Exception:
        # 后台审查失败不影响用户
        logger.exception("后台记忆审查失败", conversation_id=conversation_id)


def _log_task_error(task: asyncio.Task) -> None:
    """asyncio.Task 的 done_callback：未取消且抛异常时记录日志。"""
    if not task.cancelled() and (exc := task.exception()):
        logger.error("后台任务异常", exc_info=exc)


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
        logger.exception("保存用户消息失败", conversation_id=conv.conversation_id)
        await db.rollback()
        yield _sse_error("消息保存失败，请稍后重试")
        return

    # PydanticAI Agent 流式处理
    # 记忆上下文 + 对话历史 → @agent.system_prompt 注入（system prompt 语义正确）
    # 用户消息原样传入，不拼接上下文（之前拼接导致指令被淹没）
    try:
        from pydantic_ai.settings import ModelSettings

        from app.backend.agent.pydantic_agent import get_agent

        agent = get_agent()
        deps = LumenDeps(
            user_id=user_id,
            db=db,
            conversation_id=conv.conversation_id,
            current_user_input=user_input,
        )

        full_content = ""
        usage_data: dict | None = None
        try:
            async with agent.run_stream(
                user_input,
                deps=deps,
                model_settings=ModelSettings(max_tokens=4096),
            ) as response:
                async for text in response.stream_text(delta=True):
                    full_content += text
                    yield _sse_token(text, conv.conversation_id)

                try:
                    u = response.usage()
                    usage_data = {
                        "input": u.request_tokens or 0,
                        "output": u.response_tokens or 0,
                    }
                except Exception:
                    pass

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
                    logger.warning("保存 AI 回复失败 (可能为部分)", conversation_id=conv.conversation_id)

                # Agent 工具创建了事件 → commit 后触发投影
                if deps.pending_event_ids:
                    try:
                        from app.backend.memory import get_memory

                        await get_memory().sync_projections(user_id, deps.pending_event_ids)
                    except Exception as e:
                        logger.warning("记忆投影失败", error=str(e))

                # ── 后台记忆审查 ──
                # Agent 本轮没调 memory_save / update_profile → 后台审查兜底
                if not deps.pending_event_ids:
                    task = asyncio.create_task(
                        _background_memory_review(
                            user_id=user_id,
                            user_message=user_input,
                            assistant_response=full_content,
                            conversation_id=conv.conversation_id,
                        )
                    )
                    task.add_done_callback(_log_task_error)
    except Exception:
        logger.exception("生成 AI 回复失败", conversation_id=conv.conversation_id)
        await db.rollback()
        yield _sse_error("生成回复失败，请稍后重试")
        return

    # 滚动摘要：后台异步执行，不阻塞 SSE
    if conv.message_count >= 30 and conv.message_count % 10 == 0:
        task = asyncio.create_task(_summarize_bg(conv.conversation_id))
        task.add_done_callback(_log_task_error)

    yield _sse_done(conv.conversation_id, usage_data)


async def stream_chat_ws(
    db: AsyncSession,
    user_id: str,
    user_input: str,
    conversation_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
):
    """
    WebSocket 流式对话：
    - yield dict 而非 SSE 字符串
    - 支持 cancel_event 中途取消
    - 取消时保存已生成内容（截断）
    """
    if cancel_event is None:
        cancel_event = asyncio.Event()

    # 获取或创建会话
    if conversation_id:
        conv = await db.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            yield {"type": "error", "message": "会话不存在"}
            return
    else:
        conv = Conversation(
            user_id=user_id,
            title=user_input[:30] + "..." if len(user_input) > 30 else user_input,
        )
        db.add(conv)
        await db.flush()

    yield {"type": "token", "content": "", "conversation_id": conv.conversation_id}

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
        logger.exception("保存用户消息失败", conversation_id=conv.conversation_id)
        await db.rollback()
        yield {"type": "error", "message": "消息保存失败，请稍后重试"}
        return

    # PydanticAI Agent 流式处理
    try:
        from pydantic_ai.settings import ModelSettings

        from app.backend.agent.pydantic_agent import get_agent

        agent = get_agent()
        deps = LumenDeps(
            user_id=user_id,
            db=db,
            conversation_id=conv.conversation_id,
            current_user_input=user_input,
        )

        full_content = ""
        usage_data: dict | None = None
        cancelled = False

        try:
            async with agent.run_stream(
                user_input,
                deps=deps,
                model_settings=ModelSettings(max_tokens=4096),
            ) as response:
                async for text in response.stream_text(delta=True):
                    if cancel_event.is_set():
                        cancelled = True
                        break
                    full_content += text
                    yield {"type": "token", "content": text, "conversation_id": conv.conversation_id}

                if not cancelled:
                    try:
                        u = response.usage()
                        usage_data = {
                            "input": u.request_tokens or 0,
                            "output": u.response_tokens or 0,
                        }
                    except Exception:
                        pass

        finally:
            # 无论正常完成还是取消，都保存已生成的内容
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
                    logger.warning("保存 AI 回复失败 (可能为部分)", conversation_id=conv.conversation_id)

                # Agent 工具创建了事件 → commit 后触发投影
                if deps.pending_event_ids:
                    try:
                        from app.backend.memory import get_memory

                        await get_memory().sync_projections(user_id, deps.pending_event_ids)
                    except Exception as e:
                        logger.warning("记忆投影失败", error=str(e))

                # ── 后台记忆审查 ──
                if not cancelled and not deps.pending_event_ids:
                    task = asyncio.create_task(
                        _background_memory_review(
                            user_id=user_id,
                            user_message=user_input,
                            assistant_response=full_content,
                            conversation_id=conv.conversation_id,
                        )
                    )
                    task.add_done_callback(_log_task_error)

        if cancelled:
            yield {"type": "cancelled", "conversation_id": conv.conversation_id}
            return

    except asyncio.CancelledError:
        # 任务被取消，保存已生成内容
        if full_content:
            db.add(
                Message(
                    conversation_id=conv.conversation_id,
                    role="assistant",
                    content=full_content,
                    intent="consultation",
                )
            )
            try:
                await db.commit()
            except Exception:
                await db.rollback()
        yield {"type": "cancelled", "conversation_id": conv.conversation_id}
        return
    except Exception:
        logger.exception("生成 AI 回复失败", conversation_id=conv.conversation_id)
        await db.rollback()
        yield {"type": "error", "message": "生成回复失败，请稍后重试"}
        return

    # 滚动摘要
    if conv.message_count >= 30 and conv.message_count % 10 == 0:
        task = asyncio.create_task(_summarize_bg(conv.conversation_id))
        task.add_done_callback(_log_task_error)

    yield {"type": "done", "conversation_id": conv.conversation_id, "usage": usage_data}


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
                logger.exception("后台摘要失败", conversation_id=conversation_id)
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

            from app.backend.config import get_settings

            settings = get_settings()
            provider = settings.llm_provider or "dashscope"
            model = settings.llm_model or "qwen-plus"
            model_id = model if provider == "openai" else f"{provider}/{model}"
            api_key = settings.llm_api_key or settings.dashscope_api_key or ""
            base_url = settings.llm_base_url

            kwargs: dict = {
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 256,
                "api_key": api_key,
                "stream": False,
                "timeout": 30,
            }
            if base_url:
                kwargs["base_url"] = base_url

            response = await litellm.acompletion(**kwargs)
            summary = response.choices[0].message.content or ""
            conv.summary = summary.strip() if summary else None
            await db.commit()
            logger.info("摘要已更新", conversation_id=conv.conversation_id, length=len(summary) if summary else 0)
        except asyncio.CancelledError:
            raise
        except Exception:
            await db.rollback()
            logger.warning("摘要生成失败，保留旧摘要", conversation_id=conv.conversation_id)
        finally:
            # 释放锁引用，防止无限增长
            _summary_locks.pop(conv.conversation_id, None)


def _sse_token(content: str, conversation_id: str) -> str:
    return f"data: {json.dumps({'type': 'token', 'content': content, 'conversation_id': conversation_id}, ensure_ascii=False)}\n\n"


def _sse_error(message: str) -> str:
    return f"data: {json.dumps({'type': 'error', 'message': message}, ensure_ascii=False)}\n\n"


def _sse_done(conversation_id: str, usage: dict | None = None) -> str:
    payload: dict = {"type": "done", "conversation_id": conversation_id}
    if usage:
        payload["usage"] = usage
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
