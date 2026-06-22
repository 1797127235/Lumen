"""订阅信息流应用 — RSS 拉取 (feedparser + httpx)。

支持 ETag / Last-Modified 增量拉取，guid 去重。
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

_REQUEST_TIMEOUT = 30.0
_APP_NAME = "lumen-feed"


def feed_id_of(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def item_id_of(feed_id: str, guid: str) -> str:
    return hashlib.sha256(f"{feed_id}:{guid}".encode()).hexdigest()[:16]


def _strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", text).strip()[:1000]


def _parse_published(entry: Any) -> str:
    for key in ("published_parsed", "updated_parsed"):
        tp = entry.get(key)
        if tp:
            try:
                return datetime(*tp[:6], tzinfo=UTC).isoformat()
            except Exception:
                continue
    return entry.get("published") or entry.get("updated") or ""


def _entry_guid(entry: Any) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title") or ""


async def fetch_feed(
    url: str,
    etag: str = "",
    last_modified: str = "",
) -> dict[str, Any] | None:
    """拉取并解析 RSS feed。

    返回 {feed, entries, etag, last_modified, not_modified} 或 {error}。
    not_modified=True 表示源无更新（304）。
    """
    headers = {"User-Agent": f"{_APP_NAME}/0.1.0"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 304:
                return {"not_modified": True, "feed": {}, "entries": [], "etag": etag, "last_modified": last_modified}
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
    entries = []
    for entry in parsed.entries:
        entries.append(
            {
                "guid": _entry_guid(entry),
                "title": entry.get("title", "无标题"),
                "link": entry.get("link", ""),
                "summary": _strip_html(entry.get("summary") or entry.get("description", "")),
                "published_at": _parse_published(entry),
            }
        )

    return {
        "feed": feed_info,
        "entries": entries,
        "etag": resp.headers.get("etag", ""),
        "last_modified": resp.headers.get("last-modified", ""),
        "not_modified": False,
    }
