# 订阅信息流应用设计

> 状态：草案
> 日期：2026-06-14
> 范围：把订阅信息流做成一个**独立的常驻 MCP server 应用**，与 Lumen 对等协作

---

## 1. 背景与动机

Lumen 在 `3adb251` 重构里把旧的 RSS 主动推送闭环（`ProactiveScheduler`，375 行）从核心删除，RSS 重建为纯被动的 `mcp_servers/rss/`（7 个数据工具，问才动）。核心确实变干净了，但产品上丢了"系统主动拉取 → AI 分析 → 推送展示"这整条能力。

用户要的是完整闭环：**我订阅 → 系统拉取 → AI 分析 → 给我展示**。把这套做回核心会重蹈覆辙（耦合）；做成 Lumen 的"插件模块"又把它想小了 —— 它是完整 RSS reader + AI 增强的体量。

**结论**：把它做成一个**独立应用**，以常驻 MCP server 形态运行，与 Lumen 对等协作。这同时回答了"模块插件化"的诉求：大功能自己成为独立应用，而不是塞进核心或造一套通用插件框架。

---

## 2. 定位

**是什么**
- 一个独立常驻进程，自闭环：订阅管理 / 定时拉取 / AI 分析 / 存储
- 以 MCP server 形态暴露能力给 Lumen（Lumen 是唯一 client）
- 自己配 LLM、自己管存储、自己跑调度

**不是什么**
- 不是 Lumen 核心的模块（不进 `core/` `lib/`，不被 `startup.py` 注册）
- 不是塞回核心的 scheduler（那是已删除的错误耦合）
- 不是必须有 UI 的产品（见 §3）

**与 Lumen 的关系：单向 MCP，Lumen 主动**
- 关注点：Lumen **push 进去**（调用订阅应用的 `feed_update_focus` 工具传入），订阅应用缓存供分析用。权威源是 Lumen 的 `USER.md`，订阅应用不留副本、不主动连 Lumen
- 数据 / 订阅 / 分析：订阅应用以 MCP 工具暴露，Lumen 调用
- 全程单向：Lumen 是唯一 client，订阅应用从不主动发起连接

---

## 3. 核心决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 接口协议 | **单向 MCP** | Lumen 是唯一 client（`lib/tools/mcp/` 现成），订阅应用是唯一 server。关注点由 Lumen 调用时传入，不造反向连接 |
| 展示形态 | **Lumen 对话内**（非专门 UI） | 走 MCP 后展示天然在对话里发生：用户问 → Lumen 调工具 → 对话回复。UI 不是必须 |
| 关注点来源 | **Lumen 单一源**（`USER.md`） | 关注点权威在 Lumen 长期记忆，Lumen 主动 push 给订阅应用，订阅应用只缓存不留副本 |
| 独立程度 | 后端/进程/调度/分析独立，关注点为唯一外部输入 | "差不多彻底独立"，只接收 Lumen push 的关注点 |
| 存储 | **SQLite**（非 JSON） | 条目增长 + 已读/分析查询，现有 JSON 会吃力 |
| 传输 | **SSE** | 订阅应用要常驻跑 scheduler，stdio 生命周期绑 client 不适合 |

**为什么是单向 MCP**：Lumen 已经是 MCP client，订阅应用做 server，天然契合。关注点方向不需要反向连接 —— Lumen 调用订阅应用的 `feed_update_focus` 工具把关注点 push 进去即可，订阅应用缓存后供 scheduler 分析。全程 Lumen 主动、订阅应用被动，协议单一、拓扑简单。

**为什么 UI 不必须**：展示需求由"Lumen 对话展示 + 推送通知内容"满足。专门的信息流页面降级为可选增强（v2 再考虑）。

---

## 4. 架构总览

**单向 MCP：**

```
  ┌──────────────────┐                        ┌──────────────────────┐
  │      Lumen       │  MCP (client → server)  │   订阅信息流应用       │
  │                  │ ─────────────────────▶ │   (MCP server, SSE)   │
  │  MCP client(已有)│  feed_* 工具            │   feed_* / 调度 / 分析 │
  │                  │  + feed_update_focus    │                       │
  │  (USER.md 关注点)│  (关注点 push 进去)     │                       │
  └──────────────────┘                        └───────────┬──────────┘
                                                          │
                                                   ~/.lumen/feed/ (SQLite)
```

- Lumen 是唯一 client，订阅应用是唯一 server
- 数据/订阅/分析：Lumen 调 `feed_*` 工具拿
- 关注点：Lumen 调 `feed_update_focus` 工具 push 进去

**应用内部：**

```
  订阅信息流应用
  ┌──────────────────────────────────┐
  │ MCP server (SSE)                 │
  │   feed_list/add/get_unread/      │
  │       get_analysis/ack/poll_now  │
  │   feed_update_focus (接收关注点)  │
  ├──────────────────────────────────┤
  │ 调度器（asyncio 后台任务）        │
  │   定时 feedparser 拉取 + 去重     │
  ├──────────────────────────────────┤
  │ 分析引擎                          │
  │   缓存关注点 + 新条目 → LLM       │
  │   → 相关性 / 摘要 / 值不值得看    │
  ├──────────────────────────────────┤
  │ SQLite: feeds/items/analysis/ack │
  └──────────────────────────────────┘
```

---

## 5. 核心模块

### 5.1 订阅与存储
- 订阅源 CRUD：`feed_add_feed(url)` / `feed_remove_feed(id)` / `feed_list_feeds()`
- 存储：SQLite，自包含在 `~/.lumen/feed/feed.db`
- 增量拉取：ETag / Last-Modified / guid 去重

### 5.2 调度器
- 在 MCP server 进程内跑 asyncio 后台任务（`asyncio.create_task`）
- 定时遍历订阅源拉取新条目，写入 items
- 间隔 / 退避：默认每 N 分钟，失败 backoff
- 这就是被删的 `ProactiveScheduler` 干的活，但活在**自己进程**里，不碰 Lumen 核心

### 5.3 分析引擎
- 触发：调度器拉到新条目后，批量送分析
- 输入：① Lumen push 进来并缓存的关注点 ② 一批新条目
- 处理：用自己的 LLM（独立配置），对每条产出 `relevance`（相关性）/ `summary`（一句话）/ `verdict`（值不值得看）
- 输出：写入 analysis 表，带 `focus_snapshot`（分析时用的关注点版本，便于追溯）
- **这是应用的核心价值** —— 给 Lumen 的不是原始 RSS，是带 AI 增强的结果

---

## 6. 接口设计

订阅应用 MCP 工具（Lumen 调用，单向）：

| 工具 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `feed_list_feeds` | — | 订阅源列表 | |
| `feed_add_feed` | url | feed 信息 | 添加并首次拉取 |
| `feed_remove_feed` | feed_id | 确认 | |
| `feed_get_unread` | limit? | 未读条目 **+ 分析结果** | 核心，对话展示用 |
| `feed_get_analysis` | item_id | 单条详细分析 | 深入看某条时 |
| `feed_acknowledge` | item_ids | 确认 | 标记已读 |
| `feed_poll_now` | — | 新增条目数 | 手动触发（可选） |
| **`feed_update_focus`** | focus: str[] | 确认 | **Lumen push 关注点进来**，订阅应用缓存供分析 |

关注点流向：Lumen 在 `USER.md` 刷新后 / 定时 / 用户问订阅前，调用 `feed_update_focus` 把当前关注点 push 进去。订阅应用只缓存最近一份，不维护、不主动拉。

---

## 7. 数据模型

```sql
feeds(id, url, title, created_at, last_fetched_at, etag, last_modified)
items(id, feed_id, guid, title, link, summary, published_at, fetched_at, content_hash)
analysis(item_id PK, relevance, summary, verdict, focus_snapshot, analyzed_at)
ack_state(item_id PK, acked_at)
-- 关注点缓存在内存或单独 kv 表，不持久化为"真相"，真相在 Lumen USER.md
```

---

## 8. 关键流程（闭环时序）

```
订阅  : 用户在对话 "订阅 example.com/feed"
        → Lumen 调 feed_add_feed → 应用添加 + 首次拉取 + 分析

关注点: Lumen 在 USER.md 刷新后 / 定时，调 feed_update_focus 把关注点 push 进去

日常  : 应用 scheduler 定时拉 → 新条目 → 分析引擎(缓存关注点 + 内容→LLM) → 写 analysis

查看  : 用户 "我订阅有啥新消息"
        → Lumen 调 feed_get_unread → 应用返回(未读 + 分析)
        → Lumen 对话回复展示
        → 用户看完 → feed_acknowledge

推送  : (可选 v2) 高相关新条目 → 应用发桌面通知 / Telegram
```

---

## 9. Lumen 侧改动

1. MCP client（`lib/tools/mcp/`）现成可用，**零改动**
2. 在适当时机调用订阅应用的 `feed_update_focus`，把 `USER.md` 关注点 push 过去（触发时机见 §11）
3. 在 `~/.lumen/config.json["mcp_servers"]` 加订阅应用配置

**不进 `core/` 业务逻辑，不碰 `startup.py`，不加数据库表，不加 MCP server 能力。**

---

## 10. 与旧 `mcp_servers/rss/` 的关系

旧版 `mcp_servers/rss/`（7 个被动工具，JSON 存储）曾是 feed 能力的 **v0**，已在 2026-06 移除。

新版 `apps/feed/` 相对旧版的升级：

- 工程位置：`mcp_servers/rss/` → `apps/feed/`（标识"应用"而非"server"）
- 传输：stdio → SSE（常驻需求）
- 形态：被动工具 → 常驻带 scheduler 的 MCP server
- 新增：调度器 + 分析引擎
- 数据工具：保留并扩展（加 `feed_get_analysis` / `feed_update_focus` 等）
- 存储：JSON → SQLite

---

## 11. 待决策点

| 点 | 选项 | 倾向 | 状态 |
|----|------|------|------|
| **工程位置** | 升级 `mcp_servers/rss/` / 新建 `apps/feed/` | 新建 `apps/feed/`，标识"应用"而非"server" | 已决策，旧版已删除 |
| **`feed_update_focus` 触发时机** | USER.md 刷新后自动 / 定时 / 用户问订阅前 | USER.md 刷新后自动（`understanding.py` 已有刷新钩子） |
| **推送** | 不做 / 桌面通知 / Telegram | v1 不做，展示走对话；推送留 v2 |
| **信息流 UI 页** | 不做 / 可选增强 | 不做（展示走对话），UI 留作可选增强 |
| **分析触发** | 拉取即分析 / 批量定时分析 | 拉取即分析（实时性好） |

---

## 12. 不做的事（边界）

- 不做 Lumen 核心业务改动（不加 MCP server，只多调一个 `feed_update_focus` 工具）
- 不订阅应用主动连 Lumen（全程 Lumen 单向调用）
- 不在订阅应用里维护第二份用户画像（关注点单一源 = Lumen，订阅应用只缓存）
- 不做专门 UI 页面（v1 展示走对话）
- 不做主动推送（v1，留 v2）
- 不耦合 Lumen 的 `core.config` / 数据库 / `startup.py`
