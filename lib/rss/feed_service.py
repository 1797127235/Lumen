"""RSS Feed 服务 — 异步拉取、解析、去重、ACK。

所有状态持久化到 ~/.lumen/rss/ 目录下的 JSON 文件。
内存缓存避免高频读盘，写入时同步更新缓存。

RSSHub 多实例自动降级：
    当 feed URL 匹配已知 RSSHub 域名时，请求失败后自动替换为
    其他健康的 RSSHub 公共实例重试，直到成功或全部耗尽。
    每个实例维护失败计数和冷却时间，避免反复请求已挂的实例。
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
_INSTANCE_COOLDOWN = 3600  # 实例失败后冷却时间（秒）
_INSTANCE_MAX_FAILS = 3  # 连续失败 N 次后进入冷却
_FEED_BACKOFF_BASE = 300  # Feed 退避基础时间（秒），与调度间隔一致
_FEED_BACKOFF_MAX = 86400  # Feed 最大退避时间（24 小时）

_write_count = 0  # 简易计数器，用于触发 ACK 清理


# ── RSSHub 多实例降级 ─────────────────────────────────

# 已知 RSSHub 公共实例（按优先级排序，当前可用的在前）
RSSHUB_INSTANCES: list[str] = [
    "rsshub.liumingye.cn",  # 2026-06  verified working (bilibili ok)
    "rsshub.app",  # 官方，但 Cloudflare 403
    "rsshub.woodland.cafe",
    "rsshub.rssforever.com",
    "rsshub.friesport.ac.cn",
    "hub.slarker.me",
    "rsshub.atgw.io",
    "rsshub.rss.tips",
    "rsshub.mubibai.com",
    "rsshub.ktachibana.party",
    "rsshub.pseudoyu.com",
    "rss.fatpandac.com",
    "rsshub.aierliz.xyz",
    "rsshub-instance.zeabur.app",
]

# 已知 RSSHub 域名集合，用于检测 feed URL 是否走 RSSHub
_RSSHUB_DOMAINS: frozenset[str] = frozenset(RSSHUB_INSTANCES)


def _is_rsshub_url(url: str) -> bool:
    """判断 URL 是否指向 RSSHub 实例。"""
    host = urlparse(url).hostname or ""
    return host in _RSSHUB_DOMAINS


def _replace_rsshub_host(url: str, new_host: str) -> str:
    """将 URL 中的 RSSHub 主机替换为 new_host。"""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(netloc=new_host))


class _InstanceHealth:
    """RSSHub 实例健康状态追踪（进程级单例，纯内存）。"""

    def __init__(self) -> None:
        # {host: {"fails": int, "cooldown_until": float}}
        self._state: dict[str, dict[str, Any]] = {h: {"fails": 0, "cooldown_until": 0.0} for h in RSSHUB_INSTANCES}

    def report_success(self, host: str) -> None:
        if host in self._state:
            self._state[host]["fails"] = 0
            self._state[host]["cooldown_until"] = 0.0

    def report_failure(self, host: str) -> None:
        if host not in self._state:
            return
        self._state[host]["fails"] += 1
        if self._state[host]["fails"] >= _INSTANCE_MAX_FAILS:
            self._state[host]["cooldown_until"] = time.monotonic() + _INSTANCE_COOLDOWN
            logger.info("RSSHub instance entered cooldown", host=host, cooldown_s=_INSTANCE_COOLDOWN)

    def is_healthy(self, host: str) -> bool:
        if host not in self._state:
            return True  # 未知实例，视为健康（允许尝试）
        return time.monotonic() >= self._state[host]["cooldown_until"]

    def get_healthy_hosts(self) -> list[str]:
        """返回健康的实例列表（排除冷却中的）。"""
        return [h for h in RSSHUB_INSTANCES if self.is_healthy(h)]

    def get_status(self) -> dict[str, dict[str, Any]]:
        """返回所有实例状态快照（用于 API 展示）。"""
        now = time.monotonic()
        result = {}
        for host in RSSHUB_INSTANCES:
            s = self._state.get(host, {"fails": 0, "cooldown_until": 0.0})
            result[host] = {
                "fails": s["fails"],
                "cooling_down": now < s.get("cooldown_until", 0),
            }
        return result


_instance_health = _InstanceHealth()


# ── Feed 级退避 ─────────────────────────────────────────


def _compute_backoff(consecutive_fails: int) -> float:
    """指数退避：base * 2^(fails-1)，上限 max。"""
    delay = _FEED_BACKOFF_BASE * (2 ** (consecutive_fails - 1))
    return min(delay, _FEED_BACKOFF_MAX)


# feed 退避状态（进程级内存）
_feed_fail_state: dict[str, dict[str, Any]] = {}  # {name: {"fails": int, "retry_after": float}}


def _should_skip_feed(feed_name: str) -> bool:
    """判断是否应跳过该 feed（退避中）。"""
    state = _feed_fail_state.get(feed_name)
    if not state:
        return False
    return time.monotonic() < state.get("retry_after", 0)


def _record_feed_failure(feed_name: str) -> None:
    """记录 feed 失败，增加退避。"""
    state = _feed_fail_state.get(feed_name, {"fails": 0, "retry_after": 0.0})
    state["fails"] += 1
    state["retry_after"] = time.monotonic() + _compute_backoff(state["fails"])
    _feed_fail_state[feed_name] = state
    logger.debug(
        "Feed backoff updated",
        feed=feed_name,
        consecutive_fails=state["fails"],
        backoff_s=_compute_backoff(state["fails"]),
    )


def _record_feed_success(feed_name: str) -> None:
    """记录 feed 成功，重置退避。"""
    _feed_fail_state.pop(feed_name, None)


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


async def _fetch_with_rsshub_fallback(
    client: httpx.AsyncClient,
    url: str,
    feed_name: str,
) -> httpx.Response | None:
    """尝试请求 feed URL，RSSHub URL 自动多实例降级。

    Returns:
        成功时返回 Response；所有尝试均失败返回 None。
    """
    # 非首个实例尝试时的简短间隔，避免密集请求
    _RETRY_GAP = 0.3

    # 构建候选 URL 列表
    candidates: list[str] = [url]

    if _is_rsshub_url(url):
        original_host = urlparse(url).hostname or ""
        healthy = _instance_health.get_healthy_hosts()
        for host in healthy:
            if host != original_host:
                candidates.append(_replace_rsshub_host(url, host))

    last_error = ""
    for candidate_url in candidates:
        host = urlparse(candidate_url).hostname or "?"
        try:
            resp = await client.get(candidate_url)
            resp.raise_for_status()
            if _is_rsshub_url(candidate_url):
                _instance_health.report_success(host)
            return resp
        except Exception as e:
            last_error = str(e)
            if _is_rsshub_url(candidate_url):
                _instance_health.report_failure(host)
            # 仅 RSSHub 降级时短暂间隔
            if len(candidates) > 1:
                await asyncio.sleep(_RETRY_GAP)

    # 全部失败
    logger.warning(
        "RSS feed fetch failed (all instances exhausted)",
        feed=feed_name,
        attempts=len(candidates),
        last_error=last_error,
    )
    return None


async def poll_feeds() -> dict:
    """拉取所有订阅源的新条目。

    RSSHub URL 自动多实例降级；连续失败的 feed 指数退避。

    Returns:
        {"ok": True, "new_items": N, "total_items": M}
    """
    _ensure_dir()
    feeds: list[dict] = await _load_json(_FEEDS_FILE, [])
    items: dict = await _load_json(_ITEMS_FILE, {})
    new_count = 0

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for feed in feeds:
            feed_name = feed.get("name", "")

            if not feed.get("enabled", True):
                continue

            # Feed 级退避：连续失败时跳过
            if _should_skip_feed(feed_name):
                continue

            resp = await _fetch_with_rsshub_fallback(client, feed["url"], feed_name)
            if resp is None:
                _record_feed_failure(feed_name)
                continue

            try:
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
                        "source_name": feed_name,
                        "title": entry.get("title", ""),
                        "content": entry.get("summary", ""),
                        "url": url,
                        "published_at": published_at,
                        "category": feed.get("category", ""),
                    }
                    new_count += 1

                # 成功 → 重置 feed 退避
                _record_feed_success(feed_name)
            except Exception as e:
                logger.warning("RSS feed parse failed", feed=feed_name, error=str(e))
                _record_feed_failure(feed_name)

    # 条目淘汰
    items = _evict_oldest(items)
    await _save_json(_ITEMS_FILE, items)
    return {"ok": True, "new_items": new_count, "total_items": len(items)}


async def list_items(
    source_name: str = "",
    limit: int = 20,
    unread_only: bool = False,
) -> list[dict]:
    """返回已缓存的 RSS 条目，支持按来源过滤和数量限制。

    Args:
        source_name: 按订阅源名称过滤（空字符串 = 全部）
        limit: 最多返回条数
        unread_only: 是否只返回未读条目
    """
    items: dict = await _load_json(_ITEMS_FILE, {})
    acks: dict[str, float] = await _load_json(_ACK_FILE, {}) if unread_only else {}
    now = time.time()

    results: list[dict] = []
    for eid, item in items.items():
        # 来源过滤
        if source_name and item.get("source_name", "") != source_name:
            continue
        # 未读过滤
        if unread_only and eid in acks and now < acks[eid]:
            continue
        results.append(item)

    # 按 published_at 降序（最新在前）
    results.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return results[:limit]


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


def get_instance_status() -> dict[str, dict[str, Any]]:
    """返回 RSSHub 实例健康状态快照（供 API 展示）。"""
    return _instance_health.get_status()


def get_feed_backoff_status() -> dict[str, dict[str, Any]]:
    """返回各 feed 的退避状态快照。"""
    now = time.monotonic()
    return {
        name: {
            "consecutive_fails": state["fails"],
            "cooling_down": now < state.get("retry_after", 0),
        }
        for name, state in _feed_fail_state.items()
    }
