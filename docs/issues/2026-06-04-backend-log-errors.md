# 后端日志错误分析 — 2026-06-04

## 1. 概述

检查 `logs/lumen.log`（当前 2.3MB）及轮转日志，发现 3 类问题。本文档记录错误现象、根因分析和建议修复方案。

## 2. 错误总览

| # | 严重级别 | 错误 | 频率 | 影响范围 |
|---|---------|------|------|---------|
| 1 | **ERROR** | `UsageLimitExceeded: tool_calls_limit of 20` | 2 次（08:34, 12:47） | 单次对话完全失败，用户无回复 |
| 2 | **WARNING** | RSS feed fetch 403 Forbidden | 持续高频（每 5 分钟） | 日志噪音，RSS 功能不可用 |
| 3 | ERROR | Telegram `get_updates` / typing action 失败 | 2 次（关停期间） | 仅影响关停流程，无实质影响 |

---

## 3. 问题 1：Agent 工具调用超限（P1）

### 3.1 错误信息

```
pydantic_ai.exceptions.UsageLimitExceeded: The next tool call(s) would exceed 
the tool_calls_limit of 20 (tool_calls=21).
```

调用栈关键路径：

```
agent_runner.py:188  _process_message → async for event in stream
  → pydantic_ai _agent_graph.py:1664  process_tool_calls
    → pydantic_ai usage.py:417  check_before_tool_call
      → raise UsageLimitExceeded
```

### 3.2 触发场景

用户通过 Telegram 询问「新华社 B 站最新视频」，Agent（deepseek-v4-flash）陷入死循环：

```
web_search("新华社B站最新视频")     → 结果不精确
web_extract(bilibili space page)   → 页面 JS 渲染，抓不到内容
web_crawl(bilibili space page)     → 同上
tool_search("select:shell")        → 加载 shell 工具
shell(curl bilibili API)           → -799 请求过于频繁
shell(curl rsshub.app)             → Cloudflare JS Challenge 拦截
shell(python3 ...)                 → Windows 找不到 python3.exe
web_search(换关键词再搜)            → 结果仍不精确
web_crawl(rsshub 备用实例)         → 超时无输出
web_search(再换关键词)              → ...
（循环继续，直至触达 20 次上限）
```

Agent 用了 **21 次工具调用**，全部用于尝试获取 B 站视频列表，未产出有效回复。

### 3.3 根因分析

1. **Agent 不知道技术限制**：B 站 API 需要 WBI 签名、RSSHub 被 Cloudflare 保护、Windows 上 `python3` 命令不存在——Agent 对这些一无所知，反复尝试注定失败的路径
2. **缺乏「放弃」策略**：deepseek-v4-flash 在面对无法完成的任务时，不会主动告知用户「我做不到」并收手，而是不断尝试新的工具组合
3. **`tool_calls_limit=20` 是正确的保护机制**，但上限内没有产出有效结果说明任务本身超出了 Agent 当前的工具能力

### 3.4 建议修复

| 方案 | 改动量 | 效果 |
|------|--------|------|
| **A. System prompt 注入知识** | 小 | 在 system prompt 中告知 Agent「B 站 API 需要 WBI 签名，无法直接调用；RSSHub 公共实例有 Cloudflare 防护」 |
| **B. 添加专用 bilibili_search 工具** | 中 | 封装正确的 B 站搜索逻辑（WBI 签名或走 B 站搜索页解析），Agent 直接调用即可 |
| **C. 工具调用失败退避** | 中 | 在工具中间件层检测：同一 session 内连续 N 次工具调用无有效产出时，注入「考虑放弃并回复用户」的提示 |
| **D. 提高 tool_calls_limit** | 极小 | 治标不治本，仅延后报错，不推荐 |

**推荐**：A + C 组合，成本最低且覆盖面广（不限于 B 站场景）。

### 3.5 修复记录（2026-06-04）

**已实现方案 C**：工具连续失败降级中间件，改动文件 `lib/tools/_middleware.py` + `lib/tools/factory.py`。

**机制**：

1. **错误检测**：每次工具返回时，正则匹配返回内容中的错误关键词（`❌`、`失败`、`403`、`超时`等）
2. **连续计数**：在 `deps.usage_budget["consecutive_fails"]` 中追踪连续无有效产出的工具调用次数
3. **辅助工具豁免**：`tool_search`、`skill_load`、`get_profile`、`memory_search` 不参与计数（它们本身是辅助行为）
4. **降级提示**：连续 8 次无有效产出后，在工具返回中追加提示：「你已经连续 N 次工具调用没有获得有效结果，请立即停止尝试工具，直接基于已有信息回答用户」
5. **自动重置**：任何一次工具成功产出时立即重置计数；降级触发后也重置，避免重复提示

**覆盖范围**：所有内置工具 + MCP 工具均在 `factory.py` 中装配了该中间件。

---

## 4. 问题 2：RSS Feed 持续拉取失败（P2）

### 4.1 错误信息

```
[warning] RSS feed fetch failed
  error="Client error '403 Forbidden' for url 'https://rsshub.app/bilibili/user/video/473837611'"
  feed='新华社 B站' filename=feed_service.py func_name=poll_feeds lineno=208
```

### 4.2 影响范围

每 **5 分钟**（`RSSScheduler interval=300s`）拉取一次，**每次都失败**。涉及的 feed：

| Feed | 状态 |
|------|------|
| 新华社 B站 (`rsshub.app/bilibili/user/video/473837611`) | 持续 403（Cloudflare Challenge） |
| Simon Willison's Weblog | 间歇性失败（空 error，疑似超时） |
| LangChain Blog | 间歇性失败（空 error，疑似超时） |

仅在 `lumen.log` 当天日志中，该 warning 出现 **30+ 次**。

### 4.3 根因分析

1. **RSSHub 公共实例**（`rsshub.app`）启用了 Cloudflare JS Challenge，Python httpx 客户端无法通过
2. **无退避机制**：`feed_service.py` 的 `poll_feeds` 不论连续失败多少次，始终按固定间隔重试
3. **日志噪音**：每 5 分钟一条 warning，淹没真正有价值的日志信息

### 4.4 建议修复

| 方案 | 改动量 | 效果 |
|------|--------|------|
| **A. 指数退避 + 失败计数** | 中 | 连续失败 N 次后暂停该 feed（如 1h → 2h → 4h），成功后重置 |
| **B. 自部署 RSSHub** | 中（运维） | 彻底解决 Cloudflare 问题，但需要额外服务器 |
| **C. 降级日志级别** | 极小 | 连续失败超过 3 次后降为 debug 级别，减少日志噪音 |
| **D. Feed 健康检查** | 中 | 新增 `/api/rss/status` 端点，展示各 feed 健康状态和失败次数 |

**推荐**：A + C 组合。先实现退避机制降低无意义重试，再降级日志。

### 4.5 修复记录（2026-06-04）

**已实现**：多实例自动降级 + Feed 级指数退避，改动文件 `lib/rss/feed_service.py`。

**机制**：

1. **RSSHub 实例池**（14 个公共实例）：检测 feed URL 是否匹配已知 RSSHub 域名
2. **自动降级**：主 URL 请求失败 → 按优先级遍历健康的备用实例重试（每次间隔 0.3s）
3. **实例健康追踪**：每个实例维护连续失败计数，3 次失败后进入 1 小时冷却
4. **Feed 级指数退避**：所有实例都挂 → feed 进入退避（5min → 10min → 20min → ... → 24h max），成功后重置
5. **日志降噪**：退避中的 feed 直接跳过不请求，不产生日志

**实例健康状态**：重启后自动恢复（纯内存状态），不持久化，给所有实例重新机会。

**当前可用的 RSSHub 实例**（经测试验证）：`rsshub.woodland.cafe`（德国）

---

## 5. 问题 3：Telegram 关停竞争（P3，非问题）

### 5.1 错误信息

```
[error] Error while calling `get_updates` one more time to mark all fetched updates. 
Suppressing error to ensure graceful shutdown.

[debug] [telegram] typing action failed: httpx.ReadError:
```

### 5.2 分析

发生在服务关停时（`12:51:19` AgentRunner 停止 → `12:51:20` Telegram typing action 失败）。

这是**正常行为**：关停时 Telegram 长轮询连接被中断，python-telegram-bot 库在清理时尝试最后一次 `get_updates` 调用，此时连接已关闭。

**无需处理**。如果介意日志中的 error 级别，可以在关停流程中先停止 typing loop 再关闭 bot。

---

## 6. 附录：日志时间线

```
08:34:22  ERROR  UsageLimitExceeded (第 1 次，conversation 8595876131)
11:09:05  WARN   RSS feed 403 (开始持续出现)
  ...     WARN   RSS feed 403 (每 5 分钟重复)
12:40:13  ERROR  Telegram get_updates shutdown error
12:47:12  ERROR  UsageLimitExceeded (第 2 次，同一 conversation)
12:48:30  WARN   RSS feed 403
12:51:19  INFO   服务关停（RSSScheduler → MemoryHousekeeper → AgentRunner）
12:51:20  DEBUG  Telegram typing action failed (关停竞争)
12:51:23  INFO   服务重新启动（config.json 加载 → WebChannel → TelegramChannel → RSSScheduler）
12:51:35  INFO   RSSScheduler started (interval=300s)
12:51:40  WARN   RSS feed 403 (重启后立即失败)
```
