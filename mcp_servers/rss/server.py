"""Lumen RSS MCP Server — RSS 订阅数据能力，通过 MCP 协议暴露。

数据存储：~/.lumen/rss/ 目录下的 JSON 文件（与原 feed_service.py 格式兼容）。

暴露的工具：
  - rss_add_feed:       添加订阅源
  - rss_remove_feed:    删除订阅源
  - rss_list_feeds:     列出所有订阅源
  - rss_poll:           拉取最新内容
  - rss_list_items:     查询缓存条目
  - rss_get_unread:     获取未读事件（proactive 数据源接口）
  - rss_acknowledge:    确认已处理事件
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import feedparser
import httpx
from mcp.server.fastmcp import FastMCP

# ── 日志 ────────────────────────────────────────────────

log = logging.getLogger("lumen-rss")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# ── 常量 ────────────────────────────────────────────────

_DATA_DIR = Path.home() / ".lumen" / "rss"
_FEEDS_FILE = _DATA_DIR / "feeds.json"
_ACK_FILE = _DATA_DIR / "ack_state.json"
_ITEMS_FILE = _DATA_DIR / "items.json"

_MAX_ITEMS = 500
_ACK_CLEANUP_INTERVAL = 50
_INSTANCE_COOLDOWN = 3600
_INSTANCE_MAX_FAILS = 3
_FEED_BACKOFF_BASE = 300
_FEED_BACKOFF_MAX = 86400

_write_count = 0

# ── RSSHub 多实例 ─────────────────────────────────────

RSSHUB_INSTANCES: list[str] = [
    "rsshub.liumingye.cn",
    "rsshub.app",
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

_RSSHUB_DOMAINS: frozenset[str] = frozenset(RSSHUB_INSTANCES)


def _is_rsshub_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in _RSSHUB_DOMAINS


def _replace_rsshub_host(url: str, new_host: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(netloc=new_host))


class _InstanceHealth:
    def __init__(self) -> None:
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

    def is_healthy(self, host: str) -> bool:
        if host not in self._state:
            return True
        return time.monotonic() >= self._state[host]["cooldown_until"]

    def get_healthy_hosts(self) -> list[str]:
        return [h for h in RSSHUB_INSTANCES if self.is_healthy(h)]


_instance_health = _InstanceHealth()

# ── Feed 退避 ──────────────────────────────────────────


def _compute_backoff(consecutive_fails: int) -> float:
    delay = _FEED_BACKOFF_BASE * (2 ** (consecutive_fails - 1))
    return min(delay, _FEED_BACKOFF_MAX)


_feed_fail_state: dict[str, dict[str, Any]] = {}


def _should_skip_feed(feed_name: str) -> bool:
    state = _feed_fail_state.get(feed_name)
    if not state:
        return False
    return time.monotonic() < state.get("retry_after", 0)


def _record_feed_failure(feed_name: str) -> None:
    state = _feed_fail_state.get(feed_name, {"fails": 0, "retry_after": 0.0})
    state["fails"] += 1
    state["retry_after"] = time.monotonic() + _compute_backoff(state["fails"])
    _feed_fail_state[feed_name] = state


def _record_feed_success(feed_name: str) -> None:
    _feed_fail_state.pop(feed_name, None)


# ── URL 去重 ──────────────────────────────────────────

_TRACKING_PARAMS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "from", "spm", "nsukey"}
)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    return urlunparse(parsed._replace(query=urlencode(cleaned, doseq=True)))


def _url_to_id(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:40]


# ── JSON 文件读写 ──────────────────────────────────────

_cache: dict[str, Any] = {}


def _load_json(path: Path, default: Any) -> Any:
    cache_key = path.name
    if cache_key in _cache:
        return _cache[cache_key]
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _cache[cache_key] = data
        return data
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data: Any) -> None:
    global _write_count
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _cache[path.name] = data
    _write_count += 1
    if _write_count >= _ACK_CLEANUP_INTERVAL:
        _write_count = 0
        _cleanup_expired_acks()


def _ensure_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── 条目淘汰 ──────────────────────────────────────────


def _evict_oldest(items: dict, max_items: int = _MAX_ITEMS) -> dict:
    if len(items) <= max_items:
        return items
    sorted_items = sorted(items.items(), key=lambda kv: kv[1].get("published_at", ""), reverse=True)
    return dict(sorted_items[:max_items])


# ── ACK 清理 ──────────────────────────────────────────


def _cleanup_expired_acks() -> None:
    acks: dict[str, float] = _load_json(_ACK_FILE, {})
    if not acks:
        return
    now = time.time()
    cleaned = {eid: until for eid, until in acks.items() if now < until}
    removed = len(acks) - len(cleaned)
    if removed > 0:
        log.info("rss_ack_cleanup removed=%d remaining=%d", removed, len(cleaned))
        _save_json(_ACK_FILE, cleaned)


# ── HTTP 拉取 ─────────────────────────────────────────


async def _fetch_with_rsshub_fallback(
    client: httpx.AsyncClient,
    url: str,
    feed_name: str,
) -> httpx.Response | None:
    _RETRY_GAP = 0.3
    candidates: list[str] = [url]

    if _is_rsshub_url(url):
        original_host = urlparse(url).hostname or ""
        healthy = _instance_health.get_healthy_hosts()
        for host in healthy:
            if host != original_host:
                candidates.append(_replace_rsshub_host(url, host))

    for candidate_url in candidates:
        host = urlparse(candidate_url).hostname or "?"
        try:
            resp = await client.get(candidate_url)
            resp.raise_for_status()
            if _is_rsshub_url(candidate_url):
                _instance_health.report_success(host)
            return resp
        except Exception as e:
            log.warning("fetch failed url=%s error=%s", candidate_url, e)
            if _is_rsshub_url(candidate_url):
                _instance_health.report_failure(host)
            if len(candidates) > 1:
                await asyncio.sleep(_RETRY_GAP)

    return None


# ── 核心操作（sync，被 MCP tool 调用）──────────────────


def _poll_feeds_sync() -> dict:
    """拉取所有订阅源的新条目。"""
    _ensure_dir()
    feeds: list[dict] = _load_json(_FEEDS_FILE, [])
    items: dict = _load_json(_ITEMS_FILE, {})
    new_count = 0
    new_event_ids: list[str] = []
    feed_results: list[dict] = []  # 每个 feed 的拉取结果

    # httpx sync client for simplicity in MCP server context
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for feed in feeds:
            feed_name = feed.get("name", "")
            if not feed.get("enabled", True):
                continue
            if _should_skip_feed(feed_name):
                feed_results.append({"feed": feed_name, "status": "skipped", "reason": "backoff"})
                continue

            # 同步版 fetch（在 MCP server 进程中足够）
            candidates = [feed["url"]]
            if _is_rsshub_url(feed["url"]):
                original_host = urlparse(feed["url"]).hostname or ""
                for host in _instance_health.get_healthy_hosts():
                    if host != original_host:
                        candidates.append(_replace_rsshub_host(feed["url"], host))

            resp = None
            attempts = 0
            for candidate_url in candidates:
                host = urlparse(candidate_url).hostname or "?"
                attempts += 1
                try:
                    resp = client.get(candidate_url)
                    resp.raise_for_status()
                    if _is_rsshub_url(candidate_url):
                        _instance_health.report_success(host)
                    break
                except Exception:
                    if _is_rsshub_url(candidate_url):
                        _instance_health.report_failure(host)

            if resp is None:
                _record_feed_failure(feed_name)
                feed_results.append({"feed": feed_name, "status": "failed", "attempts": attempts})
                log.warning("rss_feed_fetch_failed feed=%s attempts=%d", feed_name, attempts)
                continue

            try:
                parsed = feedparser.parse(resp.text)
                feed_new = 0
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
                        ts = time.mktime(pp)
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
                    feed_new += 1
                    new_event_ids.append(eid)

                _record_feed_success(feed_name)
                feed_results.append({"feed": feed_name, "status": "ok", "new": feed_new})
            except Exception as e:
                _record_feed_failure(feed_name)
                feed_results.append({"feed": feed_name, "status": "parse_error", "error": str(e)})
                log.error("rss_feed_parse_error feed=%s error=%s", feed_name, e)

    items = _evict_oldest(items)
    _save_json(_ITEMS_FILE, items)

    # ── 结构化 summary（一条日志看清整轮结果）──
    ok_count = sum(1 for r in feed_results if r["status"] == "ok")
    fail_count = sum(1 for r in feed_results if r["status"] == "failed")
    skip_count = sum(1 for r in feed_results if r["status"] in ("skipped", "parse_error"))
    log.info(
        "rss_poll_complete total=%d new=%d cached=%d ok=%d fail=%d skip=%d",
        len(feeds),
        new_count,
        len(items),
        ok_count,
        fail_count,
        skip_count,
    )

    return {"ok": True, "new_items": new_count, "total_items": len(items), "new_event_ids": new_event_ids}


def _list_items_sync(source_name: str = "", limit: int = 20, unread_only: bool = False) -> list[dict]:
    items: dict = _load_json(_ITEMS_FILE, {})
    acks: dict[str, float] = _load_json(_ACK_FILE, {}) if unread_only else {}
    now = time.time()
    results: list[dict] = []
    for eid, item in items.items():
        if source_name and item.get("source_name", "") != source_name:
            continue
        if unread_only and eid in acks and now < acks[eid]:
            continue
        results.append(item)
    results.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return results[:limit]


def _get_unread_sync(days: int = 7) -> list[dict]:
    acks: dict[str, float] = _load_json(_ACK_FILE, {})
    now = time.time()
    cutoff = now - days * 86400
    items: dict = _load_json(_ITEMS_FILE, {})
    result = []
    for eid, item in items.items():
        if eid in acks and now < acks[eid]:
            continue
        # 只返回最近 N 天的条目
        published_at = item.get("published_at", "")
        if published_at:
            try:
                ts = datetime.fromisoformat(published_at).timestamp()
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass  # 解析失败就保留
        result.append(item)
    return result


def _acknowledge_sync(event_ids: list[str], ttl_hours: int = 168) -> dict:
    acks: dict[str, float] = _load_json(_ACK_FILE, {})
    until = time.time() + ttl_hours * 3600 if ttl_hours > 0 else float("inf")
    for eid in event_ids:
        acks[eid] = until
    _save_json(_ACK_FILE, acks)
    log.info("rss_ack count=%d ttl_hours=%d", len(event_ids), ttl_hours)
    return {"ok": True, "acked": len(event_ids)}


def _add_feed_sync(name: str, url: str, category: str = "") -> dict:
    _ensure_dir()
    feeds: list[dict] = _load_json(_FEEDS_FILE, [])
    if any(f["url"] == url for f in feeds):
        log.warning("rss_add_feed_duplicate name=%s url=%s", name, url)
        return {"error": f"订阅源 {url} 已存在"}
    feeds.append({"name": name, "url": url, "category": category, "enabled": True})
    _save_json(_FEEDS_FILE, feeds)
    log.info("rss_feed_added name=%s url=%s category=%s total=%d", name, url, category, len(feeds))
    return {"ok": True, "action": "add", "name": name}


def _remove_feed_sync(name: str) -> dict:
    feeds: list[dict] = _load_json(_FEEDS_FILE, [])
    feeds = [f for f in feeds if f["name"] != name]
    _save_json(_FEEDS_FILE, feeds)
    log.info("rss_feed_removed name=%s remaining=%d", name, len(feeds))
    return {"ok": True, "action": "remove", "name": name}


def _list_feeds_sync() -> list[dict]:
    return _load_json(_FEEDS_FILE, [])


# ═══════════════════════════════════════════════════════
#  MCP Server 定义
# ═══════════════════════════════════════════════════════

mcp = FastMCP("lumen-rss", json_response=True)


@mcp.tool()
def rss_add_feed(name: str, url: str, category: str = "") -> str:
    """添加 RSS 订阅源。

    Args:
        name: 订阅源名称，如 'Simon Willison'
        url: RSS/Atom feed 地址
        category: 分类标签（可选）
    """
    result = _add_feed_sync(name, url, category)
    if "error" in result:
        return result["error"]
    return f"已添加订阅源「{name}」({url})"


@mcp.tool()
def rss_remove_feed(name: str) -> str:
    """删除 RSS 订阅源。

    Args:
        name: 要删除的订阅源名称
    """
    _remove_feed_sync(name)
    return f"已删除订阅源「{name}」"


@mcp.tool()
def rss_list_feeds() -> str:
    """列出当前所有 RSS 订阅源。"""
    feeds = _list_feeds_sync()
    if not feeds:
        return "当前没有任何订阅源。"
    lines = [f"共 {len(feeds)} 个订阅源：\n"]
    for i, f in enumerate(feeds, 1):
        status = "✅" if f.get("enabled", True) else "⏸️"
        cat = f" [{f['category']}]" if f.get("category") else ""
        lines.append(f"{i}. {status} {f['name']}{cat}")
        lines.append(f"   {f['url']}")
    return "\n".join(lines)


@mcp.tool()
def rss_poll() -> str:
    """拉取所有 RSS 订阅源的最新内容。"""
    result = _poll_feeds_sync()
    if result.get("new_items", 0) == 0:
        return f"本轮无新内容（已缓存 {result.get('total_items', 0)} 条）"

    unreads = _get_unread_sync()
    lines = [f"拉取完成：新增 {result['new_items']} 条，未读 {len(unreads)} 条\n"]
    for item in unreads[:10]:
        lines.append(f"- {item.get('title', '无标题')}")
        lines.append(f"  {item.get('url', '')}")
        source = item.get("source_name", "")
        if source:
            lines.append(f"  来源：{source}")
    return "\n".join(lines)


@mcp.tool()
def rss_list_items(source_name: str = "", limit: int = 20) -> str:
    """查询本地已缓存的 RSS 条目。

    Args:
        source_name: 按订阅源名称过滤（留空返回全部）
        limit: 最多返回条数（默认 20，最大 50）
    """
    limit = min(limit, 50)
    items = _list_items_sync(source_name=source_name, limit=limit)
    if not items:
        msg = "当前没有缓存的 RSS 条目。" if not source_name else f"订阅源「{source_name}」没有缓存条目。"
        return msg

    total_hint = f"（{source_name}）" if source_name else ""
    lines = [f"RSS 缓存条目{total_hint}，显示最新 {len(items)} 条：\n"]
    for i, item in enumerate(items, 1):
        title = item.get("title", "无标题")
        url = item.get("url", "")
        source = item.get("source_name", "")
        published = item.get("published_at", "")
        time_hint = f" ({published[:10]})" if published else ""
        lines.append(f"{i}. {title}{time_hint}")
        if url:
            lines.append(f"   {url}")
        if source:
            lines.append(f"   来源：{source}")
    return "\n".join(lines)


@mcp.tool()
def rss_get_unread(limit: int = 100, days: int = 7) -> str:
    """获取未读 RSS 事件（供 proactive 系统调用）。

    返回 JSON 格式的事件列表，每个事件包含 event_id, title, content, url, source_name, published_at。

    Args:
        limit: 最多返回多少条，默认 100
        days: 只返回最近 N 天的条目，默认 7
    """
    items = _get_unread_sync(days=days)
    if not items:
        return "没有未读事件。"
    # 返回 JSON 供程序化消费
    import json as _json

    return _json.dumps(items[:limit], ensure_ascii=False)


@mcp.tool()
def rss_acknowledge(event_ids: list[str], ttl_hours: int = 168) -> str:
    """确认已处理的事件（ACK）。

    Args:
        event_ids: 要确认的事件 ID 列表
        ttl_hours: ACK 有效期（小时），默认 168（7 天）
    """
    result = _acknowledge_sync(event_ids, ttl_hours)
    return f"已确认 {result['acked']} 个事件"


def main():
    log.info("rss_mcp_server_starting data_dir=%s", _DATA_DIR)
    mcp.run()


if __name__ == "__main__":
    main()
