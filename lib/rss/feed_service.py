"""RSS Feed 服务 — 异步拉取、解析、去重、ACK。

所有状态持久化到 ~/.lumen/rss/ 目录下的 JSON 文件。
内存缓存避免高频读盘，写入时同步更新缓存。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import feedparser
import httpx

from shared.logging import get_logger

logger = get_logger(__name__)

_DATA_DIR = Path.home() / ".lumen" / "rss"
_FEEDS_FILE = _DATA_DIR / "feeds.json"
_ACK_FILE = _DATA_DIR / "ack_state.json"
_ITEMS_FILE = _DATA_DIR / "items.json"

# ── 常量 ────────────────────────────────────────────────
_MAX_ITEMS = 2000  # 条目数量上限，超出裁剪最旧的
_ACK_CLEANUP_INTERVAL = 50  # 每 N 次 write 操作触发一次 ACK 过期清理

_write_count = 0  # 简易计数器，用于触发 ACK 清理


def _ensure_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── URL 去重 ──────────────────────────────────────────

_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "from",
        "spm",
        "nsukey",
    }
)


def normalize_url(url: str) -> str:
    """去掉 tracking 参数，标准化 URL 用于去重。"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    return urlunparse(parsed._replace(query=urlencode(cleaned, doseq=True)))


def _url_to_id(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:40]


# ── 内存缓存 ──────────────────────────────────────────

_cache: dict[str, Any] = {}
_CACHE_KEY_FEEDS = "feeds"
_CACHE_KEY_ITEMS = "items"
_CACHE_KEY_ACKS = "acks"


def _invalidate(key: str) -> None:
    _cache.pop(key, None)


def _invalidate_all() -> None:
    _cache.clear()


# ── JSON 文件读写（async） ────────────────────────────


async def _load_json(path: Path, default: Any) -> Any:
    cache_key = path.name
    if cache_key in _cache:
        return _cache[cache_key]

    def _read() -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    data = await asyncio.to_thread(_read)
    _cache[cache_key] = data
    return data


async def _save_json(path: Path, data: Any) -> None:
    global _write_count

    def _write() -> None:
        _ensure_dir()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    await asyncio.to_thread(_write)
    _cache[path.name] = data

    # 定期清理过期 ACK
    _write_count += 1
    if _write_count >= _ACK_CLEANUP_INTERVAL:
        _write_count = 0
        await _cleanup_expired_acks()


# ── 条目淘汰 ──────────────────────────────────────────


def _evict_oldest(items: dict, max_items: int = _MAX_ITEMS) -> dict:
    """保留最新的 max_items 条，淘汰最旧的。"""
    if len(items) <= max_items:
        return items

    # 按 published_at 降序排列，保留最新的
    sorted_items = sorted(
        items.items(),
        key=lambda kv: kv[1].get("published_at", ""),
        reverse=True,
    )
    return dict(sorted_items[:max_items])


# ── ACK 过期清理 ──────────────────────────────────────


async def _cleanup_expired_acks() -> None:
    """清理已过期的 ACK 条目。"""
    acks: dict[str, float] = await _load_json(_ACK_FILE, {})
    if not acks:
        return

    now = time.time()
    cleaned = {eid: until for eid, until in acks.items() if now < until}
    removed = len(acks) - len(cleaned)
    if removed > 0:
        await _save_json(_ACK_FILE, cleaned)
        logger.debug("ACK cleanup: removed %d expired entries", removed)


# ── 公开接口（全部 async） ─────────────────────────────


async def poll_feeds() -> dict:
    """拉取所有订阅源的新条目。

    Returns:
        {"ok": True, "new_items": N, "total_items": M}
    """
    _ensure_dir()
    feeds: list[dict] = await _load_json(_FEEDS_FILE, [])
    items: dict = await _load_json(_ITEMS_FILE, {})
    new_count = 0

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for feed in feeds:
            if not feed.get("enabled", True):
                continue
            try:
                resp = await client.get(feed["url"])
                resp.raise_for_status()
                # feedparser.parse 是 CPU 密集型，放线程池
                parsed = await asyncio.to_thread(feedparser.parse, resp.text)

                for entry in parsed.entries:
                    url = entry.get("link", "")
                    if not url or not isinstance(url, str):
                        continue
                    eid = _url_to_id(url)
                    if eid in items:
                        continue

                    published_at = ""
                    pp = entry.get("published_parsed")
                    if pp and hasattr(pp, "__len__"):
                        ts = time.mktime(pp)  # type: ignore[arg-type]
                        published_at = datetime.fromtimestamp(ts, tz=UTC).isoformat()

                    items[eid] = {
                        "kind": "content",
                        "event_id": eid,
                        "source_type": "rss",
                        "source_name": feed.get("name", ""),
                        "title": entry.get("title", ""),
                        "content": entry.get("summary", ""),
                        "url": url,
                        "published_at": published_at,
                        "category": feed.get("category", ""),
                    }
                    new_count += 1
            except Exception as e:
                logger.warning("RSS feed fetch failed", feed=feed.get("name", ""), error=str(e))

    # 条目淘汰
    items = _evict_oldest(items)
    await _save_json(_ITEMS_FILE, items)
    return {"ok": True, "new_items": new_count, "total_items": len(items)}


async def get_unread_events() -> list[dict]:
    """返回所有未被 ACK 的事件。"""
    acks: dict[str, float] = await _load_json(_ACK_FILE, {})
    now = time.time()
    items: dict = await _load_json(_ITEMS_FILE, {})

    return [item for eid, item in items.items() if not (eid in acks and now < acks[eid])]


async def acknowledge_events(event_ids: list[str], ttl_hours: int = 168) -> dict:
    """ACK 事件。"""
    acks: dict[str, float] = await _load_json(_ACK_FILE, {})
    until = time.time() + ttl_hours * 3600 if ttl_hours > 0 else float("inf")
    for eid in event_ids:
        acks[eid] = until
    await _save_json(_ACK_FILE, acks)
    return {"ok": True, "acked": len(event_ids)}


async def add_feed(name: str, url: str, category: str = "") -> dict:
    """添加订阅源。"""
    _ensure_dir()
    feeds: list[dict] = await _load_json(_FEEDS_FILE, [])
    if any(f["url"] == url for f in feeds):
        return {"error": f"订阅源 {url} 已存在"}
    feeds.append({"name": name, "url": url, "category": category, "enabled": True})
    await _save_json(_FEEDS_FILE, feeds)
    return {"ok": True, "action": "add", "name": name}


async def remove_feed(name: str) -> dict:
    """删除订阅源。"""
    feeds: list[dict] = await _load_json(_FEEDS_FILE, [])
    feeds = [f for f in feeds if f["name"] != name]
    await _save_json(_FEEDS_FILE, feeds)
    return {"ok": True, "action": "remove", "name": name}


async def list_feeds() -> list[dict]:
    """列出所有订阅源。"""
    return await _load_json(_FEEDS_FILE, [])
