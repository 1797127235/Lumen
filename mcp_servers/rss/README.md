# Lumen RSS MCP Server

Lumen 的独立 RSS 订阅源管理插件，通过 MCP 协议与 Lumen 通信。

## 设计原则

- **零核心耦合**：不修改 Lumen 核心代码，不依赖核心数据库/配置
- **手动启用**：用户通过 `~/.lumen/config.json` 或 `/api/mcp/servers` 接口自行添加
- **被动数据源**：没有后台主动推送，仅响应工具调用
- **自包含存储**：数据存在 `~/.lumen/rss/` 下

## 安装

```bash
cd mcp_servers/rss
pip install -e .
```

## 暴露的工具

| 工具名 | 功能 |
|---|---|
| `rss_add_feed` | 添加 RSS 订阅源 |
| `rss_remove_feed` | 删除订阅源 |
| `rss_list_feeds` | 列出所有订阅源 |
| `rss_poll` | 拉取所有订阅源的新内容 |
| `rss_list_items` | 查询已缓存条目 |
| `rss_get_unread` | 获取未读条目 |
| `rss_acknowledge` | 标记条目已处理 |

## 在 Lumen 中启用

编辑 `~/.lumen/config.json`：

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

> 注意：把 `args` 中的路径替换为你机器上的实际绝对路径。

重启 Lumen 后，Agent 即可看到并使用 RSS 工具。

## 使用示例

用户："我订阅的 RSS 有什么新消息？"

Agent 典型调用链：

1. `rss_list_feeds` — 确认有订阅源
2. `rss_poll` — 拉取新内容
3. `rss_get_unread` — 获取未读条目
4. `rss_acknowledge` — 标记已读

## 数据文件

- `~/.lumen/rss/feeds.json` — 订阅源列表
- `~/.lumen/rss/items.json` — 缓存条目
- `~/.lumen/rss/ack_state.json` — 已读标记
