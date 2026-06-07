"""RSS 调度器 — 调用 async feed_service，定时拉取 + 过滤 + Telegram 推送。

生命周期：
    1. startup.py 中创建并 start()
    2. 后台 asyncio task 循环运行
    3. shutdown 时 stop()
"""

from __future__ import annotations

import asyncio
import contextlib

from core.config import get_settings
from lib.memory.markdown import AsyncMarkdownStore
from lib.rss.feed_service import acknowledge_events, get_unread_events, poll_feeds
from lib.rss.filter import filter_by_relevance, format_push_message, keyword_fallback
from shared.logging import get_logger

logger = get_logger(__name__)

_store = AsyncMarkdownStore()


class RSSScheduler:
    """RSS 调度器 — 定时拉取 + LLM 过滤 + Telegram 推送。

    职责：
        1. 周期调 poll_feeds() 拉新内容
        2. 调 get_unread_events() 获取未处理事件
        3. 读 FOCUS.md 过滤
        4. 推送到 Telegram
        5. 调 acknowledge_events() ACK 已处理事件
    """

    def __init__(self, telegram_channel) -> None:
        self._telegram_channel = telegram_channel
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("RSSScheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("RSSScheduler stopped")

    async def _run_loop(self) -> None:
        settings = get_settings()
        interval = settings.rss_fetch_interval

        while self._running:
            try:
                await self._run_once()
            except Exception as e:
                logger.error("RSS scheduler round failed: %s", e)
            await asyncio.sleep(interval)

    async def _run_once(self) -> None:
        if not self._telegram_channel:
            return

        settings = get_settings()
        chat_id = settings.telegram_chat_id
        if not chat_id:
            logger.warning("No telegram_chat_id configured, skipping RSS round")
            return

        # 1. 触发拉取（async）
        await poll_feeds()

        # 2. 获取未处理事件（async）
        events = await get_unread_events()
        if not events:
            return

        # 3. 读 FOCUS.md（用实际 Telegram user_id）
        focus = await _store.read_focus(chat_id)
        if not focus.strip():
            return

        # 4. 过滤（LLM，失败降级关键词）
        try:
            filtered = await filter_by_relevance(events, focus)
        except Exception as e:
            logger.warning("LLM filter failed, fallback to keywords: %s", e)
            filtered = keyword_fallback(events, focus)

        if not filtered:
            # 过滤后全部不相关，ACK 为 not_interesting（30天）
            ids = [e["event_id"] for e in events if "event_id" in e]
            if ids:
                await acknowledge_events(ids, ttl_hours=720)
            return

        # 5. 推送（最多 5 条）
        ack_ids: list[str] = []
        for item in filtered[:5]:
            content = format_push_message(item)
            try:
                await self._telegram_channel.send_message(chat_id, content)
                ack_ids.append(item["event_id"])
            except Exception as e:
                logger.error("RSS push failed: %s", e)

        # 6. ACK 已推送的（7天）（async）
        if ack_ids:
            await acknowledge_events(ack_ids, ttl_hours=168)
