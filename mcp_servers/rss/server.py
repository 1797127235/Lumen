"""Lumen RSS MCP Server

一个完全独立的 RSS 订阅源管理 MCP Server。

设计原则：
- 数据自包含在 ~/.lumen/rss/
- 被动响应工具调用，无后台推送
- 通过 stdio 与 Lumen 通信

暴露工具：
- rss_add_feed
- rss_remove_feed
- rss_list_feeds
- rss_poll
- rss_list_items
- rss_get_unread
- rss_acknowledge
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import feedparser
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

APP_NAME = "lumen-rss"
USER_DATA_DIR = Path.home() / ".lumen"
RSS_DATA_DIR = USER_DATA_DIR / "rss"

FEEDS_FILE = RSS_DATA_DIR / "feeds.json"
ITEMS_FILE = RSS_DATA_DIR / "items.json"
ACK_FILE = RSS_DATA_DIR / "ack_state.json"

_request_timeout = httpx.Timeout(30.0, connect=10.0)


def _ensure_data_dir() -> None:
    RSS_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data: Any) -> None:
    _ensure_data_dir()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ═══════════════════════════════════════════════════════════
# 存储层
# ═══════════════════════════════════════════════════════════


class RSSStore:
    """RSS 数据存储 — JSON 文件 + asyncio.Lock（单进程内并发安全）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def load_feeds(self) -> list[dict[str, Any]]:
        data = await asyncio.to_thread(_load_json, FEEDS_FILE, {"feeds": []})
        return data.get("feeds", [])

    async def save_feeds(self, feeds: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(_save_json, FEEDS_FILE, {"feeds": feeds})

    async def load_items(self) -> list[dict[str, Any]]:
        data = await asyncio.to_thread(_load_json, ITEMS_FILE, {"items": []})
        return data.get("items", [])

    async def save_items(self, items: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(_save_json, ITEMS_FILE, {"items": items})

    async def load_acked_ids(self) -> set[str]:
        data = await asyncio.to_thread(_load_json, ACK_FILE, {"acked_ids": []})
        return set(data.get("acked_ids", []))

    async def save_acked_ids(self, acked_ids: set[str]) -> None:
        await asyncio.to_thread(_save_json, ACK_FILE, {"acked_ids": sorted(acked_ids)})


_store = RSSStore()


# ═══════════════════════════════════════════════════════════
# RSS 解析
# ═══════════════════════════════════════════════════════════


def _item_id(feed_id: str, entry: dict[str, Any]) -> str:
    """为条目生成稳定 ID。"""
    raw = entry.get("id") or entry.get("link") or entry.get("title") or ""
    if not raw:
        raw = json.dumps(entry, sort_keys=True, default=str)
    return hashlib.sha256(f"{feed_id}:{raw}".encode()).hexdigest()[:16]


def _parse_published(entry: dict[str, Any]) -> str:
    """提取发布时间，失败返回空字符串。"""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=UTC).isoformat()
        except Exception:
            pass
    return entry.get("published") or entry.get("updated") or ""


def _strip_html(raw: str) -> str:
    """简单去除 HTML 标签。"""
    if not raw:
        return ""
    import re

    text = raw.replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def _fetch_feed(url: str) -> dict[str, Any] | None:
    """拉取并解析单个 RSS feed。"""
    try:
        async with httpx.AsyncClient(timeout=_request_timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": f"{APP_NAME}/0.1.0"})
            resp.raise_for_status()
    except Exception as exc:
        return {"error": f"请求失败: {exc}"}

    try:
        parsed = feedparser.parse(resp.text)
    except Exception as exc:
        return {"error": f"解析失败: {exc}"}

    feed_info = {
        "title": parsed.feed.get("title", ""),
        "link": parsed.feed.get("link", ""),
        "description": parsed.feed.get("description", ""),
    }
    entries: list[dict[str, Any]] = []
    for entry in parsed.entries:
        entries.append(
            {
                "title": entry.get("title", "无标题"),
                "link": entry.get("link", ""),
                "summary": _strip_html(entry.get("summary") or entry.get("description", "")),
                "published_at": _parse_published(entry),
            }
        )

    return {"feed": feed_info, "entries": entries}


# ═══════════════════════════════════════════════════════════
# 工具实现
# ═══════════════════════════════════════════════════════════


async def _tool_add_feed(url: str) -> dict[str, Any]:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "url 必须以 http:// 或 https:// 开头"}

    async with _store._lock:
        feeds = await _store.load_feeds()
        for feed in feeds:
            if feed["url"] == url:
                return {"error": "该订阅源已存在", "feed": feed}

        result = await _fetch_feed(url)
        if result is None or "error" in result:
            return {"error": result.get("error", "拉取订阅源失败") if result else "拉取订阅源失败"}

        feed_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        feed = {
            "id": feed_id,
            "url": url,
            "title": result["feed"].get("title", url),
            "link": result["feed"].get("link", ""),
            "description": result["feed"].get("description", ""),
            "added_at": datetime.now(UTC).isoformat(),
        }
        feeds.append(feed)
        await _store.save_feeds(feeds)

        # 首次添加时同步缓存条目
        items = await _store.load_items()
        existing_ids = {i["id"] for i in items}
        for entry in result["entries"]:
            item_id = _item_id(feed_id, entry)
            if item_id in existing_ids:
                continue
            items.append(
                {
                    "id": item_id,
                    "feed_id": feed_id,
                    **entry,
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            )
        await _store.save_items(items)

    return {"success": True, "feed": feed, "cached_items": len(result["entries"])}


async def _tool_remove_feed(feed_id: str) -> dict[str, Any]:
    async with _store._lock:
        feeds = await _store.load_feeds()
        new_feeds = [f for f in feeds if f["id"] != feed_id]
        if len(new_feeds) == len(feeds):
            return {"error": f"未找到 feed_id: {feed_id}"}

        items = await _store.load_items()
        new_items = [i for i in items if i.get("feed_id") != feed_id]

        await _store.save_feeds(new_feeds)
        await _store.save_items(new_items)

    return {"success": True, "removed_feed_id": feed_id}


async def _tool_list_feeds() -> dict[str, Any]:
    feeds = await _store.load_feeds()
    return {"feeds": feeds, "count": len(feeds)}


async def _tool_poll(limit_per_feed: int = 20) -> dict[str, Any]:
    async with _store._lock:
        feeds = await _store.load_feeds()
        if not feeds:
            return {"error": "没有订阅源，先用 rss_add_feed 添加"}

        items = await _store.load_items()
        existing_ids = {i["id"] for i in items}
        added_count = 0
        errors: list[str] = []

        for feed in feeds:
            result = await _fetch_feed(feed["url"])
            if result is None or "error" in result:
                errors.append(
                    f"{feed.get('title', feed['url'])}: {result.get('error', 'unknown') if result else 'unknown'}"
                )
                continue

            for entry in result["entries"][:limit_per_feed]:
                item_id = _item_id(feed["id"], entry)
                if item_id in existing_ids:
                    continue
                items.append(
                    {
                        "id": item_id,
                        "feed_id": feed["id"],
                        **entry,
                        "fetched_at": datetime.now(UTC).isoformat(),
                    }
                )
                existing_ids.add(item_id)
                added_count += 1

        await _store.save_items(items)

    return {"success": True, "added_items": added_count, "errors": errors}


async def _tool_list_items(limit: int = 50, feed_id: str = "") -> dict[str, Any]:
    items = await _store.load_items()
    if feed_id:
        items = [i for i in items if i.get("feed_id") == feed_id]
    items = sorted(items, key=lambda x: x.get("published_at") or x.get("fetched_at") or "", reverse=True)
    return {"items": items[:limit], "count": len(items[:limit]), "total": len(items)}


async def _tool_get_unread(limit: int = 20) -> dict[str, Any]:
    async with _store._lock:
        items = await _store.load_items()
        acked_ids = await _store.load_acked_ids()
        unread = [i for i in items if i["id"] not in acked_ids]
        unread = sorted(unread, key=lambda x: x.get("published_at") or x.get("fetched_at") or "", reverse=True)
    return {"items": unread[:limit], "count": len(unread[:limit]), "total_unread": len(unread)}


async def _tool_acknowledge(item_ids: list[str]) -> dict[str, Any]:
    async with _store._lock:
        acked_ids = await _store.load_acked_ids()
        acked_ids.update(item_ids)
        await _store.save_acked_ids(acked_ids)
    return {"success": True, "acked_count": len(item_ids)}


# ═══════════════════════════════════════════════════════════
# MCP Server 定义
# ═══════════════════════════════════════════════════════════

_TOOLS: list[Tool] = [
    Tool(
        name="rss_add_feed",
        description="添加一个 RSS 订阅源。首次添加会拉取并缓存最近条目。",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "RSS feed URL，必须以 http:// 或 https:// 开头"},
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="rss_remove_feed",
        description="删除指定 RSS 订阅源及其缓存条目。",
        inputSchema={
            "type": "object",
            "properties": {
                "feed_id": {"type": "string", "description": "订阅源 ID"},
            },
            "required": ["feed_id"],
        },
    ),
    Tool(
        name="rss_list_feeds",
        description="列出所有已添加的 RSS 订阅源。",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="rss_poll",
        description="拉取所有订阅源的新内容并缓存。",
        inputSchema={
            "type": "object",
            "properties": {
                "limit_per_feed": {
                    "type": "integer",
                    "description": "每个 feed 最多拉取多少条，默认 20",
                    "default": 20,
                },
            },
        },
    ),
    Tool(
        name="rss_list_items",
        description="查询已缓存的 RSS 条目。",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "最多返回条数，默认 50", "default": 50},
                "feed_id": {"type": "string", "description": "按订阅源过滤", "default": ""},
            },
        },
    ),
    Tool(
        name="rss_get_unread",
        description="获取未读 RSS 条目。",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "最多返回条数，默认 20", "default": 20},
            },
        },
    ),
    Tool(
        name="rss_acknowledge",
        description="标记 RSS 条目为已读/已处理。",
        inputSchema={
            "type": "object",
            "properties": {
                "item_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要标记的条目 ID 列表",
                },
            },
            "required": ["item_ids"],
        },
    ),
]

_TOOL_MAP = {
    "rss_add_feed": lambda args: _tool_add_feed(args.get("url", "")),
    "rss_remove_feed": lambda args: _tool_remove_feed(args.get("feed_id", "")),
    "rss_list_feeds": lambda _: _tool_list_feeds(),
    "rss_poll": lambda args: _tool_poll(args.get("limit_per_feed", 20)),
    "rss_list_items": lambda args: _tool_list_items(args.get("limit", 50), args.get("feed_id", "")),
    "rss_get_unread": lambda args: _tool_get_unread(args.get("limit", 20)),
    "rss_acknowledge": lambda args: _tool_acknowledge(args.get("item_ids", [])),
}


def _build_error(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": message}, ensure_ascii=False))]


async def main() -> None:
    _ensure_data_dir()

    server = Server(APP_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return _TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> list[TextContent]:
        arguments = arguments or {}
        if name not in _TOOL_MAP:
            return _build_error(f"未知工具: {name}")

        try:
            result = await _TOOL_MAP[name](arguments)
        except Exception as exc:
            return _build_error(f"工具执行失败: {exc}")

        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
