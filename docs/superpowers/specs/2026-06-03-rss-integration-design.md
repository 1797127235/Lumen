# RSS 智能过滤集成设计（MCP 架构）

> **状态：已过时 / 已移除**
>
> RSS 模块与 FOCUS.md 功能已从代码中删除。本文档仅作为历史决策记录保留。

**日期**: 2026-06-03
**状态**: 待审核
**范围**: RSS 订阅 + FOCUS.md 智能过滤 + Telegram 推送

---

## 目标

让 Lumen 能订阅 RSS 信息源，根据用户当前关注点（FOCUS.md）自动过滤，推送相关内容。

## 设计前提

> **Lumen 是单用户系统**（`demo_user` 硬编码，见 `AGENTS.md` 和 `src/lib/userId.ts`）。
>
> **架构借鉴 Akashic**（`E:\OpenHub\akashic-agent`）：数据源作为独立 MCP server 进程，核心引擎通过 MCP 协议标准接口交互。

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│  rss-mcp（独立 MCP server 进程，stdio 通信）                  │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  poll_feeds   │  │  get_events  │  │  ack_events      │  │
│  │  拉取+解析    │  │  返回未ACK事件│  │  ACK+TTL 持久化  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────────┘  │
│         │                 │                  │              │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌──────▼───────────┐  │
│  │  httpx +     │  │  feeds.json  │  │  ack_state.json  │  │
│  │  feedparser  │  │  (订阅源)    │  │  (ACK 持久化)    │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────┬───────────────────────────────────────┘
                      │ MCP stdio
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Lumen 核心                                                  │
│                                                             │
│  ┌──────────────────┐    ┌───────────────────────────────┐ │
│  │ McpClientManager  │    │ RSS Scheduler (asyncio task)  │ │
│  │ (已有，常驻连接)   │───▶│                               │ │
│  └──────────────────┘    │ 1. call poll_feeds             │ │
│                          │ 2. call get_proactive_events   │ │
│                          │ 3. FOCUS.md 过滤               │ │
│                          │ 4. TelegramChannel.send_message│ │
│                          │ 5. call acknowledge_events     │ │
│                          └───────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
   ┌───────────┐
   │  RSSHub   │
   │ (外部服务) │
   └───────────┘
```

**为什么用 MCP 而不是内置模块**：

1. **复用已有基础设施** — Lumen 已有 `McpClientManager`、`McpServerConfig`、MCP 管理 API、tool_bridge 自动发现
2. **数据源与引擎解耦** — RSS 拉取/解析/去重/ACK 状态管理封装在独立进程，Lumen 核心只调三个标准接口
3. **扩展性** — 未来加新数据源（天气 API、GitHub events 等）只需新建 MCP server，不改 Lumen 代码
4. **可独立开发调试** — rss-mcp 可以脱离 Lumen 独立测试
5. **与 Akashic 生态兼容** — 相同的 MCP 接口契约（`get_proactive_events` / `poll_feeds` / `acknowledge_events`）

---

## 第一部分：rss-mcp（独立 MCP Server）

### 文件结构

```
~/.lumen/mcp/rss-mcp/
├── run_mcp.py          # FastMCP 入口
├── feed_backend.py     # RSS 拉取+解析+去重核心逻辑
├── requirements.txt    # feedparser, httpx, mcp
└── feeds.json          # 订阅源配置（用户可编辑）
```

运行时产生的文件：
```
~/.lumen/mcp/rss-mcp/
├── ack_state.json      # ACK 持久化（MCP stdio 按需启停，内存状态会丢）
└── .venv/              # 独立 Python 虚拟环境
```

### 依赖

```txt
# rss-mcp/requirements.txt
feedparser>=6.0.0
httpx>=0.27.0
mcp>=1.0.0
```

> **Lumen 核心不需要安装 feedparser**。RSS 解析完全在 rss-mcp 进程内完成。

### 入口 (`run_mcp.py`)

```python
from mcp.server.fastmcp import FastMCP
from feed_backend import poll_feeds, get_proactive_events, acknowledge_events, manage_feeds

mcp = FastMCP("rss-mcp")

@mcp.tool()
def rss_poll_feeds() -> str:
    """拉取所有订阅源的新内容。由 Lumen 调度器周期性调用。"""
    return poll_feeds()

@mcp.tool()
def rss_get_proactive_events() -> str:
    """返回所有未被 ACK 的事件（list[dict] JSON）。

    事件格式：
    {
        "kind": "content",
        "event_id": "sha256_of_normalized_url",
        "source_type": "rss",
        "source_name": "Hacker News",
        "title": "...",
        "content": "摘要...",
        "url": "https://...",
        "published_at": "2026-06-03T12:00:00Z"
    }
    """
    return get_proactive_events()

@mcp.tool()
def rss_acknowledge_events(event_ids: list[str], ttl_hours: int = 168) -> str:
    """ACK 事件，ttl_hours 内不再返回。

    TTL 分层（借鉴 Akashic）：
    - 已推送且用户已知悉：168h（7天）
    - 过滤后标记 interesting 但未推送：24h
    - 过滤后标记 not_interesting：720h（30天）
    """
    return acknowledge_events(event_ids, ttl_hours)

@mcp.tool()
def rss_add_feed(name: str, url: str, category: str = "") -> str:
    """添加 RSS 订阅源。"""
    return manage_feeds("add", name=name, url=url, category=category)

@mcp.tool()
def rss_remove_feed(name: str) -> str:
    """删除 RSS 订阅源。"""
    return manage_feeds("remove", name=name)

@mcp.tool()
def rss_list_feeds() -> str:
    """列出所有订阅源。"""
    return manage_feeds("list")

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### 核心逻辑 (`feed_backend.py`)

```python
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import feedparser
import httpx

_DIR = Path(__file__).parent
_FEEDS_FILE = _DIR / "feeds.json"
_ACK_FILE = _DIR / "ack_state.json"
_ITEMS_FILE = _DIR / "items.json"  # 缓存已拉取的条目

# ── URL 去重 ──────────────────────────────────────────

_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_content",
                     "utm_term", "from", "spm", "nsukey"}

def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    return urlunparse(parsed._replace(query=urlencode(cleaned, doseq=True)))

def _url_to_id(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:40]

# ── feeds.json 管理 ──────────────────────────────────

def _load_feeds() -> list[dict]:
    if not _FEEDS_FILE.exists():
        return []
    return json.loads(_FEEDS_FILE.read_text(encoding="utf-8"))

def _save_feeds(feeds: list[dict]) -> None:
    _FEEDS_FILE.write_text(json.dumps(feeds, ensure_ascii=False, indent=2), encoding="utf-8")

# ── ACK 持久化 ───────────────────────────────────────

def _load_acks() -> dict[str, float]:
    if not _ACK_FILE.exists():
        return {}
    try:
        return json.loads(_ACK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_acks(acks: dict[str, float]) -> None:
    _ACK_FILE.write_text(json.dumps(acks, ensure_ascii=False), encoding="utf-8")

# ── items 缓存 ───────────────────────────────────────

def _load_items() -> dict[str, dict]:
    if not _ITEMS_FILE.exists():
        return {}
    try:
        return json.loads(_ITEMS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_items(items: dict[str, dict]) -> None:
    _ITEMS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

# ── 公开接口 ─────────────────────────────────────────

def poll_feeds() -> str:
    """拉取所有订阅源的新条目。"""
    feeds = _load_feeds()
    items = _load_items()
    new_count = 0

    for feed in feeds:
        if not feed.get("enabled", True):
            continue
        try:
            resp = httpx.get(feed["url"], timeout=30)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.text)

            for entry in parsed.entries:
                url = entry.get("link", "")
                if not url:
                    continue
                eid = _url_to_id(url)
                if eid in items:
                    continue  # 已有

                published_at = ""
                pp = entry.get("published_parsed")
                if pp:
                    import time as _t
                    ts = _t.mktime(pp)
                    from datetime import datetime, timezone
                    published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

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
            # 单个 feed 失败不阻断其他
            pass

    _save_items(items)
    return json.dumps({"ok": True, "new_items": new_count, "total_items": len(items)})


def get_proactive_events() -> str:
    """返回所有未被 ACK 的事件。"""
    acks = _load_acks()
    now = time.time()
    items = _load_items()

    result = []
    for eid, item in items.items():
        if eid in acks and now < acks[eid]:
            continue  # 已 ACK 且未过期
        result.append(item)

    return json.dumps(result, ensure_ascii=False)


def acknowledge_events(event_ids: list[str], ttl_hours: int = 168) -> str:
    """ACK 事件。"""
    acks = _load_acks()
    until = time.time() + ttl_hours * 3600 if ttl_hours > 0 else float("inf")
    for eid in event_ids:
        acks[eid] = until
    _save_acks(acks)
    return json.dumps({"ok": True, "acked": len(event_ids)})


def manage_feeds(action: str, **kwargs) -> str:
    """管理订阅源：add / remove / list。"""
    feeds = _load_feeds()

    if action == "list":
        return json.dumps(feeds, ensure_ascii=False, indent=2)

    if action == "add":
        name = kwargs.get("name", "")
        url = kwargs.get("url", "")
        category = kwargs.get("category", "")
        if not name or not url:
            return json.dumps({"error": "name and url required"})
        # 去重检查
        if any(f["url"] == url for f in feeds):
            return json.dumps({"error": f"feed {url} already exists"})
        feeds.append({"name": name, "url": url, "category": category, "enabled": True})
        _save_feeds(feeds)
        return json.dumps({"ok": True, "action": "add", "name": name})

    if action == "remove":
        name = kwargs.get("name", "")
        feeds = [f for f in feeds if f["name"] != name]
        _save_feeds(feeds)
        return json.dumps({"ok": True, "action": "remove", "name": name})

    return json.dumps({"error": f"unknown action: {action}"})
```

### 订阅源配置 (`feeds.json`)

```json
[
  {
    "name": "Hacker News",
    "url": "https://rsshub.app/hackernews/best",
    "category": "tech",
    "enabled": true
  },
  {
    "name": "GitHub Trending",
    "url": "https://rsshub.app/github/trending/daily/any",
    "category": "tech",
    "enabled": true
  }
]
```

---

## 第二部分：Lumen 核心侧

### 文件结构

```
lib/rss/
├── __init__.py
├── scheduler.py       # RSS 调度器（asyncio 后台 task）
└── filter.py          # FOCUS.md 过滤引擎
```

> **大幅精简**：没有 models.py、fetcher.py、store.py、pusher.py、feeds.py — 这些职责全部在 rss-mcp 里。Lumen 核心只剩调度和过滤。

### 调度器 (`lib/rss/scheduler.py`)

```python
class RSSScheduler:
    """RSS 调度器 — 通过 McpClientManager 与 rss-mcp 交互。

    职责：
        1. 周期调 poll_feeds 拉新内容
        2. 调 get_proactive_events 获取未处理事件
        3. 读 FOCUS.md 过滤
        4. 推送到 Telegram
        5. 调 acknowledge_events ACK 已处理事件
    """

    def __init__(self, telegram_channel: TelegramChannel | None) -> None:
        self._telegram_channel = telegram_channel
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

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

        manager = get_mcp_manager()
        chat_id = get_settings().telegram_chat_id
        if not chat_id:
            logger.warning("No telegram_chat_id configured")
            return

        # 1. 触发拉取
        await manager.call_tool("rss-mcp", "rss_poll_feeds", {})

        # 2. 获取未处理事件
        raw = await manager.call_tool("rss-mcp", "rss_get_proactive_events", {})
        events = self._parse_json_list(raw)
        if not events:
            return

        # 3. 读 FOCUS.md
        focus = await markdown_store.read_focus("demo_user")
        if not focus.strip():
            return

        # 4. 过滤（LLM，失败降级关键词）
        try:
            filtered = await filter_by_relevance(events, focus)
        except Exception as e:
            logger.warning("LLM filter failed, fallback: %s", e)
            filtered = keyword_fallback(events, focus)

        if not filtered:
            # 过滤后全部不相关，ACK 为 not_interesting（30天）
            await self._ack_all(manager, events, ttl_hours=720)
            return

        # 5. 推送
        ack_ids = []
        for item in filtered[:5]:  # 最多推 5 条
            content = format_push_message(item)
            try:
                await self._telegram_channel.send_message(chat_id, content)
                ack_ids.append(item["event_id"])
            except Exception as e:
                logger.error("Push failed: %s", e)

        # 6. ACK 已推送的（7天），未推送的 interesting（24小时）
        if ack_ids:
            await manager.call_tool("rss-mcp", "rss_acknowledge_events", {
                "event_ids": ack_ids, "ttl_hours": 168,
            })

    async def _ack_all(self, manager, events, ttl_hours: int) -> None:
        ids = [e["event_id"] for e in events if "event_id" in e]
        if ids:
            await manager.call_tool("rss-mcp", "rss_acknowledge_events", {
                "event_ids": ids, "ttl_hours": ttl_hours,
            })

    @staticmethod
    def _parse_json_list(raw: str) -> list[dict]:
        """McpClientManager.call_tool 返回 tool_ok/text 字符串，需 JSON 解析。"""
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []
```

### 过滤引擎 (`lib/rss/filter.py`)

```python
async def filter_by_relevance(items: list[dict], focus_content: str) -> list[dict]:
    """用 LLM 判断条目与 FOCUS.md 的相关性。"""
    prompt = build_filter_prompt(items, focus_content)
    from core.config import build_llm_call_params
    params = build_llm_call_params(model=_get_filter_model())
    result = await litellm.acompletion(
        messages=[{"role": "user", "content": prompt}], **params,
    )
    return parse_filter_result(result.choices[0].message.content, items)


def keyword_fallback(items: list[dict], focus_content: str) -> list[dict]:
    """关键词匹配兜底（LLM 过滤失败时）。"""
    keywords = [w.strip().lower() for w in focus_content.split() if len(w.strip()) > 2]
    return [
        item for item in items
        if any(kw in item.get("title", "").lower() or kw in item.get("content", "").lower()
               for kw in keywords)
    ]
```

**过滤 Prompt 模板**:
```
你是一个信息过滤器。根据用户的当前关注点，判断以下 RSS 条目是否相关。

【用户关注点】
{focus_content}

【RSS 条目】
{items_json}

对每个条目，返回 JSON：
- id: event_id
- relevant: true/false
- reason: 一句话说明为什么相关/不相关

只返回 JSON 数组，不要其他内容。
```

### 生命周期管理

在 `core/startup.py` 的 lifespan 中启动和停止：

```python
# startup.py lifespan 内，Channels 启动后、AgentRunner 之前：

settings = get_settings()
rss_scheduler = None

if getattr(settings, "rss_enabled", False):
    tg_ch = next((ch for ch in channels if isinstance(ch, TelegramChannel)), None)
    if tg_ch:
        from lib.rss.scheduler import RSSScheduler
        rss_scheduler = RSSScheduler(telegram_channel=tg_ch)
        rss_scheduler.start()
        logger.info("RSSScheduler enabled (interval=%ds)", settings.rss_fetch_interval)
    else:
        logger.warning("RSS enabled but no TelegramChannel — RSS not started")

# cleanup：
if rss_scheduler:
    await rss_scheduler.stop()
```

> rss-mcp 进程由 `McpClientManager` 管理（stdio transport，常驻连接），不需要额外启停。

### Telegram Chat ID

在 `core/config.py` 的 `Settings` 新增 `telegram_chat_id`，首次 Telegram 对话时自动填充：

```python
# channels/telegram/telegram.py _on_message 中：
from core.config import save_user_config, get_settings

chat_id = str(update.effective_chat.id)
if not get_settings().telegram_chat_id:
    save_user_config({"telegram_chat_id": chat_id})
```

---

## MCP Server 注册

### 方式一：通过 Lumen API

```bash
curl -X POST http://localhost:8000/api/mcp/servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "rss-mcp",
    "transport": "stdio",
    "command": "~/.lumen/mcp/rss-mcp/.venv/bin/python",
    "args": ["~/.lumen/mcp/rss-mcp/run_mcp.py"],
    "enabled": true
  }'
```

### 方式二：直接编辑配置

`McpClientManager` 在 `connect_all()` 时读取 `~/.lumen/mcp_servers.json`（通过 `config_store.py`）。

### Agent 对话管理订阅

rss-mcp 的 `rss_add_feed` / `rss_remove_feed` / `rss_list_feeds` 被 `tool_bridge.py` 自动发现为 Lumen Agent 工具。用户可以直接说：

> "帮我订阅 Hacker News 的 RSS"
> "列出我的 RSS 订阅"
> "取消 GitHub Trending 的订阅"

---

## 数据流

```
用户说"帮我订阅 HN"
  → Agent 调 rss-mcp.rss_add_feed(name="HN", url="...")
  → feeds.json 更新

RSS Scheduler（asyncio 后台 task，循环 sleep fetch_interval）
  │
  ├── 1. McpClientManager.call_tool("rss-mcp", "rss_poll_feeds", {})
  │       → rss-mcp 内部：httpx 请求 RSSHub → feedparser 解析 → 写 items.json
  │
  ├── 2. McpClientManager.call_tool("rss-mcp", "rss_get_proactive_events", {})
  │       → rss-mcp 内部：读 items.json，过滤已 ACK → 返回 JSON
  │
  ├── 3. read_focus("demo_user") → FOCUS.md
  │       → 空则跳过
  │
  ├── 4. filter_by_relevance(events, focus) → litellm（失败降级关键词）
  │
  ├── 5. TelegramChannel.send_message(chat_id, content)
  │       → chat_id 从 config 读取
  │
  └── 6. McpClientManager.call_tool("rss-mcp", "rss_acknowledge_events", {...})
          → rss-mcp 内部：ACK 写入 ack_state.json
```

---

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| rss-mcp 进程未启动 | `McpClientManager` 连接失败，scheduler 跳过本轮 |
| RSSHub 请求超时 / 5xx | rss-mcp 内部跳过该 feed，返回 `{"ok": true, "new_items": N}` |
| feedparser 解析失败 | rss-mcp 内部跳过该 feed |
| LLM 过滤失败 | 降级：关键词匹配兜底 |
| Telegram send_message 失败 | 不 ACK，下次重试 |
| 未配置 telegram_chat_id | scheduler 跳过，日志 warning |
| `call_tool` 返回异常 | `_parse_json_list` 返回空列表，跳过本轮 |

---

## 与现有系统的集成

| 现有模块 | 集成方式 |
|---------|---------|
| `McpClientManager` | 直接复用，常驻连接 rss-mcp |
| `tool_bridge.py` | 自动发现 rss-mcp 工具，注册为 Agent 可用 |
| `server/routes/mcp.py` | API 管理 rss-mcp 的启停和配置 |
| FOCUS.md | `markdown.read_focus("demo_user")` 读取关注点 |
| `TelegramChannel` | `send_message(chat_id, content)` 推送 |
| `core/config.py` | 新增 `rss_enabled`、`telegram_chat_id` 等字段 |
| `core/startup.py` | lifespan 中启动/停止 RSSScheduler |

---

## 配置

`core/config.py` Settings 新增：

```python
# ── Telegram push target ──
telegram_chat_id: str = ""                  # Telegram 推送目标（首次对话自动填充）

# ── RSS ──
rss_enabled: bool = False
rss_fetch_interval: int = 300              # 拉取间隔（秒）
rss_filter_model: str = ""                 # 空 = 使用 llm_model
rss_filter_threshold: float = 0.7          # 相关性阈值
```

**配置 API 接线清单**（实现时一并修改）：

1. `core/config.py` — `Settings` 新增字段
2. `core/config.py` — `apply_user_config()` 的 `_CONFIG_KEYS` 追加 `"rss_enabled"`, `"rss_fetch_interval"`, `"telegram_chat_id"` 等
3. `server/routes/config.py` — `ConfigUpdate` / `ConfigResponse` 同步更新

---

## 新增依赖

**Lumen 核心**：无新增依赖（feedparser 在 rss-mcp 内）。

**rss-mcp**（独立 venv）：
```txt
feedparser>=6.0.0
httpx>=0.27.0
mcp>=1.0.0
```

---

## 后续扩展

### 未来数据源（零改动接入）

只需新建 MCP server，注册到 Lumen 的 MCP 配置，scheduler 自动发现：

- `weather-mcp` — 天气预警（alert 通道）
- `github-mcp` — GitHub 事件（content 通道）
- `fitbit-mcp` — 健康数据（alert + context 通道）

### 自适应调度（v2）

参考 Akashic 的电量模型，替代固定间隔：

```
用户上次互动时间 → compute_energy() → 电量 [0,1]
    ↓
d_energy = 1 - energy → "饥渴度"
    ↓
base_score = w_e × d_energy + w_c × 新内容量
    ↓
next_interval(base_score)
    高分（饿了）→ 1 分钟
    低分（刚聊完）→ 8 分钟
```

### ACK TTL 策略（已内置）

| 消费结果 | TTL | 说明 |
|---------|-----|------|
| 已推送给用户 | 168h（7天） | 用户已知悉 |
| 过滤后 interesting 但未推送 | 24h | 一天内不重复评估 |
| 过滤后 not_interesting | 720h（30天） | 一个月后再评估（FOCUS.md 可能变了） |

### 摘要生成

对长文章用 LLM 生成摘要再推送，而非直接用 RSS summary。
