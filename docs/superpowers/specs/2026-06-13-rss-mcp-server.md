# RSS MCP Server 设计（已废弃）

**日期**: 2026-06-13  
**状态**: 已废弃 / 已替换  
**替代方案**: `apps/feed/`（独立常驻 SSE MCP 应用）  
**设计文档**: `docs/architecture/feed-app-design.md`

---

> ⚠️ 本文档描述的是旧版 `mcp_servers/rss/` stdio MCP server，该实现已于 2026-06 移除。
> 当前请使用 `apps/feed/` 作为 Lumen 的订阅信息流方案。

## 为什么废弃

旧版 RSS MCP server 存在以下局限：

- 采用 stdio 传输，每次调用拉起进程，无法做后台定时拉取
- 无 AI 分析能力，仅返回原始 RSS 条目
- 数据用 JSON 文件存储，难以扩展
- 与 Lumen 的触发器/调度器体系不兼容

新版 `apps/feed/` 改为：

- 独立常驻 SSE MCP server
- 内置 scheduler，定时拉取订阅源
- 内置 analyzer，用 LLM 判断相关性并生成摘要
- SQLite 存储，支持增量拉取与已读状态
- 与 `lib/triggers/` 短轮询体系配合

## 旧版架构（仅留档）

```
┌─────────────────┐      stdio      ┌─────────────────────┐
│   Lumen 核心    │  ◄────────────►  │  mcp_servers/rss/   │
│                 │                  │  server.py          │
│  lib/tools/mcp/ │                  │                     │
│  client_manager │                  │  ~/.lumen/rss/      │
└─────────────────┘                  │  feeds.json         │
                                     │  items.json         │
                                     │  ack_state.json     │
                                     └─────────────────────┘
```

## 旧版工具列表

| 工具名 | 说明 |
|---|---|
| `rss_add_feed` | 添加订阅源 |
| `rss_remove_feed` | 删除订阅源 |
| `rss_list_feeds` | 列出所有订阅源 |
| `rss_poll` | 拉取所有 feed 新内容 |
| `rss_list_items` | 查询缓存条目 |
| `rss_get_unread` | 获取未读条目 |
| `rss_acknowledge` | 标记已处理 |

## 迁移到新方案

1. 删除 `~/.lumen/rss/` 旧数据（可选，新版不兼容）
2. 启动新版 feed server：
   ```bash
   $env:FEED_LLM_API_KEY="sk-..."
   python apps/feed/server.py
   ```
3. 在 `~/.lumen/config.json` 中配置 SSE MCP server：
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

## 与旧架构对比

| 维度 | 旧架构 | 新架构 (`apps/feed/`) |
|---|---|---|
| 与核心关系 | 零耦合 | 零耦合 |
| 传输协议 | stdio | SSE |
| 生命周期 | 按需拉起 | 常驻进程 |
| 推送方式 | 被动工具调用 | scheduler 后台拉取 + triggers 短轮询 |
| AI 分析 | 无 | 内置 relevance/summary/verdict |
| 数据库 | JSON 文件 | SQLite |
| 配置 | `mcp_servers` | `mcp_servers` + 独立环境变量 |
