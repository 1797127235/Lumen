"""订阅信息流应用 — 常驻 MCP server (SSE)。

启动：
    python apps/feed/server.py

默认监听 127.0.0.1:8765。Lumen 通过 transport=sse 连接。
内部 scheduler 定时拉取订阅源并调用分析引擎。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from analyzer import analyze_pending
from config import Settings, load_settings
from fetcher import feed_id_of, fetch_feed, item_id_of
from mcp.server.fastmcp import FastMCP
from store import FeedStore

# Windows 控制台默认 cp936，中文日志会乱码，强制 UTF-8
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("feed")

settings: Settings = load_settings()
store = FeedStore(settings.db_path)

mcp = FastMCP("lumen-feed")
mcp.settings.host = settings.host
mcp.settings.port = settings.port


# ═══════════════════════════════════════════════════════════
# MCP 工具
# ═══════════════════════════════════════════════════════════


@mcp.tool()
async def feed_list_feeds() -> dict[str, Any]:
    """列出所有已订阅的 RSS 源。"""
    feeds = await store.list_feeds()
    return {"feeds": feeds, "count": len(feeds)}


@mcp.tool()
async def feed_add_feed(url: str) -> dict[str, Any]:
    """添加一个 RSS 订阅源（url 必须以 http:// 或 https:// 开头）。首次添加会拉取最近条目。"""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "url 必须以 http:// 或 https:// 开头"}
    if await store.get_feed_by_url(url):
        return {"error": "该订阅源已存在"}

    result = await fetch_feed(url)
    if result is None or "error" in result:
        return {"error": (result or {}).get("error", "拉取失败")}

    feed_id = feed_id_of(url)
    now = datetime.now(UTC).isoformat()
    await store.upsert_feed(
        {
            "id": feed_id,
            "url": url,
            "title": result["feed"].get("title", url),
            "link": result["feed"].get("link", ""),
            "description": result["feed"].get("description", ""),
            "added_at": now,
            "last_fetched_at": now,
            "etag": result.get("etag", ""),
            "last_modified": result.get("last_modified", ""),
        }
    )

    added = await _persist_entries(feed_id, result["entries"][: settings.poll_limit_per_feed], now)
    return {"feed_id": feed_id, "title": result["feed"].get("title", url), "added_items": added}


@mcp.tool()
async def feed_remove_feed(feed_id: str) -> dict[str, Any]:
    """删除指定 RSS 订阅源及其缓存条目。"""
    await store.delete_feed(feed_id)
    return {"removed": feed_id}


@mcp.tool()
async def feed_poll_now() -> dict[str, Any]:
    """手动触发一次拉取：遍历所有订阅源拉新内容，然后立即分析。"""
    return await _poll_and_analyze()


@mcp.tool()
async def feed_get_unread(limit: int = 20) -> dict[str, Any]:
    """获取未读条目（带 AI 分析：相关性 / 摘要 / 是否值得看），按时间倒序。"""
    items = await store.get_unread_items(limit)
    return {"items": items, "count": len(items)}


@mcp.tool()
async def feed_get_analysis(item_id: str) -> dict[str, Any]:
    """获取单条条目的详细分析。"""
    item = await store.get_item(item_id)
    if not item:
        return {"error": "条目不存在"}
    return {"item": item, "analysis": await store.get_analysis(item_id)}


@mcp.tool()
async def feed_acknowledge(item_ids: list[str]) -> dict[str, Any]:
    """标记条目为已读。"""
    return {"acked": await store.ack_items(item_ids)}


@mcp.tool()
async def feed_update_focus(focus: list[str]) -> dict[str, Any]:
    """由 Lumen 调用：把当前用户关注点 push 进来，供分析引擎使用。"""
    await store.set_focus(focus)
    return {"received": len(focus), "focus": focus}


# ═══════════════════════════════════════════════════════════
# 拉取 + 分析
# ═══════════════════════════════════════════════════════════


async def _persist_entries(feed_id: str, entries: list[dict[str, Any]], now: str) -> int:
    added = 0
    for entry in entries:
        guid = entry.get("guid", "")
        if not guid:
            continue
        ok = await store.insert_item(
            {
                "id": item_id_of(feed_id, guid),
                "feed_id": feed_id,
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published_at": entry.get("published_at", ""),
                "fetched_at": now,
            }
        )
        if ok:
            added += 1
    return added


async def _poll_and_analyze() -> dict[str, Any]:
    feeds = await store.list_feeds()
    new_total = 0
    errors: list[str] = []
    now = datetime.now(UTC).isoformat()

    for feed in feeds:
        result = await fetch_feed(
            feed["url"],
            etag=feed.get("etag", ""),
            last_modified=feed.get("last_modified", ""),
        )
        if result is None or "error" in result:
            errors.append(f"{feed.get('title') or feed['url']}: {(result or {}).get('error')}")
            continue
        if result.get("not_modified"):
            continue
        new_total += await _persist_entries(feed["id"], result["entries"][: settings.poll_limit_per_feed], now)
        await store.update_feed_fetched(
            feed["id"],
            result.get("etag", ""),
            result.get("last_modified", ""),
        )

    analysis = await analyze_pending(store, settings)
    logger.info("poll done: new=%d analyzed=%d errors=%d", new_total, analysis.get("analyzed", 0), len(errors))
    return {"new_items": new_total, "analysis": analysis, "errors": errors}


async def _scheduler_loop() -> None:
    logger.info("scheduler started, interval=%d min", settings.poll_interval_min)
    while True:
        try:
            await _poll_and_analyze()
        except Exception as exc:
            logger.error("scheduler tick failed: %s", exc)
        await asyncio.sleep(settings.poll_interval_min * 60)


async def main() -> None:
    await store.init()
    logger.info("feed db ready: %s", settings.db_path)
    logger.info("listening on http://%s:%d/sse", settings.host, settings.port)
    if not settings.llm_api_key:
        logger.warning("FEED_LLM_API_KEY 未设置，分析引擎将跳过")
    sched = asyncio.create_task(_scheduler_loop(), name="feed-scheduler")
    try:
        await mcp.run_sse_async()
    finally:
        sched.cancel()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
