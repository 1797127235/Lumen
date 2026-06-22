# 订阅信息流应用 (lumen-feed)

一个独立的常驻 MCP server，做"订阅 → 定时拉取 → AI 分析 → 对话展示"闭环。
与 Lumen **单向**协作：Lumen 作为 MCP client 调用它，并把用户关注点 push 进来。

完整设计见 `docs/architecture/feed-app-design.md`。

---

## 依赖

已在 Lumen 根 `requirements.txt` 中：`feedparser`、`mcp`、`httpx`、`aiosqlite`。
无需额外安装。

## 配置（环境变量）

订阅应用**自带 LLM 配置**，不读 Lumen 的 `.env` / `config.json`。

| 变量 | 默认 | 说明 |
|------|------|------|
| `FEED_LLM_API_KEY` | (空) | 分析引擎用的 LLM key，**必填**，否则分析跳过 |
| `FEED_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容 base url |
| `FEED_LLM_MODEL` | `gpt-4o-mini` | 模型名 |
| `FEED_HOST` | `127.0.0.1` | SSE 监听地址 |
| `FEED_PORT` | `8765` | SSE 监听端口 |
| `FEED_POLL_INTERVAL_MIN` | `30` | scheduler 拉取间隔（分钟） |
| `FEED_POLL_LIMIT_PER_FEED` | `20` | 每个 feed 每次最多拉取条数 |
| `FEED_ANALYZE_BATCH` | `8` | 每批送 LLM 分析的条目数 |
| `FEED_DB_PATH` | `~/.lumen/feed/feed.db` | SQLite 路径 |
| `LUMEN_HOME` | `~/.lumen` | 数据根目录 |

## 启动

```bash
# 在 Lumen 项目根目录
$env:FEED_LLM_API_KEY="sk-..."
$env:FEED_LLM_BASE_URL="https://..."
$env:FEED_LLM_MODEL="qwen-plus"
python apps/feed/server.py
```

启动后看到：
```
feed db ready: .../feed.db
listening on http://127.0.0.1:8765/sse
scheduler started, interval=30 min
```

## 接入 Lumen

在 `~/.lumen/config.json` 的 `mcp_servers` 加一条：

```json
{
  "mcp_servers": [
    {
      "name": "lumen-feed",
      "transport": "sse",
      "url": "http://127.0.0.1:8765/sse",
      "enabled": true,
      "auto_approve": true,
      "read_only": false
    }
  ]
}
```

Lumen 的 MCP client（`lib/tools/mcp/`）已支持 SSE，**零代码改动**。

## 工具列表

| 工具 | 说明 |
|------|------|
| `feed_list_feeds` | 列出订阅源 |
| `feed_add_feed` | 添加订阅源（首次拉取） |
| `feed_remove_feed` | 删除订阅源 |
| `feed_poll_now` | 手动触发拉取 + 分析 |
| `feed_get_unread` | 未读条目（带分析：相关性/摘要/值不值得看） |
| `feed_get_analysis` | 单条详细分析 |
| `feed_acknowledge` | 标记已读 |
| `feed_update_focus` | Lumen push 关注点进来（供分析用） |

> 注：Lumen 侧通过 `lib/triggers` 短轮询 `feed_get_unread` 检测新内容，
> 不依赖 server 主动推送。

## 关注点怎么进来

Lumen 侧在 `USER.md` 刷新后 / 定时，调用 `feed_update_focus(focus=[...])` 把用户关注点 push 进来。
订阅应用只缓存最近一份（真相在 Lumen `USER.md`），分析时使用。

## 数据

自包含在 `~/.lumen/feed/feed.db`（SQLite），Lumen 核心不访问。
