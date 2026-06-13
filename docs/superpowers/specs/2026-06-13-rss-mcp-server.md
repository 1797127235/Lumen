# RSS MCP Server 设计

**日期**: 2026-06-13  
**状态**: 已实现  
**范围**: 以独立 MCP Server 形式重建 RSS 功能

---

## 背景

之前的 RSS 模块深度耦合进 Lumen 核心：

- `core/startup.py` 自动注册 `lumen-rss` MCP server
- `core/startup.py` 启动 `ProactiveScheduler` 做主动推送
- `lib/chat/persistence.py` 每轮消息更新 `lumen_presence`
- `core/config.py` 管理 `rss_*` / `proactive_*` 配置
- `lib/partner/models.py` 维护 `LumenPresence` 表

这些耦合点已全部移除。本设计将 RSS 重建为**完全独立的 MCP Server**，核心系统只通过标准 MCP 协议与它通信。

## 目标

1. RSS 功能不依赖 Lumen 核心任何配置、数据库、状态
2. 用户按需手动启用
3. RSS server 只做被动数据工具，不主动推送
4. 数据自包含在 `~/.lumen/rss/`

## 架构

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

核心不感知 RSS server 的存在。如果 `~/.lumen/config.json["mcp_servers"]` 中没有 `lumen-rss`，
系统完全正常运行。

## 工具列表

| 工具名 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `rss_add_feed` | `url: str` | feed 信息 | 添加订阅源，首次缓存条目 |
| `rss_remove_feed` | `feed_id: str` | 确认 | 删除订阅源及缓存 |
| `rss_list_feeds` | — | feeds 列表 | 列出所有订阅源 |
| `rss_poll` | `limit_per_feed?: int` | 新增条目数 | 拉取所有 feed 新内容 |
| `rss_list_items` | `limit?, feed_id?` | items 列表 | 查询缓存条目 |
| `rss_get_unread` | `limit?: int` | items 列表 | 获取未读条目 |
| `rss_acknowledge` | `item_ids: list[str]` | 确认 | 标记已处理 |

## 存储

- `feeds.json`：订阅源元数据
- `items.json`：缓存条目
- `ack_state.json`：已处理条目 ID 集合

所有文件由 RSS server 自行读写，Lumen 核心不访问。

## 不实现的功能

- 主动推送（无 scheduler）
- FOCUS.md 过滤（由 Agent 层决定）
- Telegram 直接推送
- 情绪/伙伴系统集成

这些若未来需要，应在 Lumen Agent 层通过组合工具实现。

## 启用方式

```json
{
  "mcp_servers": [
    {
      "name": "lumen-rss",
      "transport": "stdio",
      "command": "python",
      "args": ["E:/MyHub/Lumen/mcp_servers/rss/server.py"],
      "enabled": true,
      "auto_approve": true,
      "read_only": false
    }
  ]
}
```

## 使用流程示例

用户："我订阅的 RSS 有什么新消息？"

1. Agent 调用 `rss_list_feeds` 确认有订阅源
2. Agent 调用 `rss_poll` 拉取新内容
3. Agent 调用 `rss_get_unread` 获取未读条目
4. Agent 可选调用 `web_extract` 读取原文
5. Agent 生成回复
6. Agent 调用 `rss_acknowledge` 标记已读

## 与旧架构对比

| 维度 | 旧架构 | 新架构 |
|---|---|---|
| 与核心关系 | 深度耦合 | 零耦合 |
| 启动方式 | `startup.py` 自动注册 | 用户手动配置 |
| 推送方式 | `ProactiveScheduler` 主动推送 | 被动工具调用 |
| 过滤方式 | 读取 FOCUS.md | Agent 层自行决定 |
| 数据库 | `lumen_presence` 表 | 无，自包含 JSON 文件 |
| 配置 | `rss_*` / `proactive_*` 在 `core.config.Settings` | 仅在 `mcp_servers` 配置中 |
