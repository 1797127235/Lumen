# Token 消耗与工具调用分析 — 2026-06-10

## 1. 概述

分析 Telegram 会话（conversation `8595876131`）最近的运行日志，聚焦 token 消耗、工具调用链路和后台管线效率。发现 3 个需要关注的问题。

---

## 2. Token 消耗趋势

| 时间 (UTC) | 场景 | input tokens | output tokens | cache 命中率 | 模型 |
|:---|:---|---:|---:|:---:|:---|
| 10:03 | RSS 操作（浏览 Karpathy） | 30,560 | 716 | 63.7% | deepseek-v4-flash |
| 10:08 | 搜索 + 抓取 RSS feed | 14,492 | 164 | 33.6% | |
| 10:26 | 添加 Karpathy 订阅 | 14,192 | 348 | 34.3% | |
| 10:42 | 提取文章原文 | 14,350 | 420 | 49.1% | |
| **10:43** | **取消 3 个订阅源** | **77,127** | **567** | **80.3%** | |
| 10:53 | 查看当前源列表 | 40,192 | 832 | 73.6% | |
| 12:44 | 新对话（Karpathy 文章） | 30,180 | 647 | 67.0% | |
| 12:48 | 纯对话（AI 产出质量） | 14,138 | 449 | 35.3% | |
| 12:50 | 纯对话（AI 粗糙无意义） | 14,877 | 496 | 48.2% | |
| 12:51 | 纯对话（没设计灵感） | 14,865 | 362 | 87.0% | |

### 基线

- **正常对话**：input ~14-15K，output ~300-800 tokens
- **工具密集轮次**：input 30-77K，工具返回占比 >60%
- **cache 命中率**：新话题时 ~35%，连续对话时可达 87%

---

## 3. 工具调用链路

最近 10 轮的工具调用链路还原：

```
轮次1 (10:03): tool_search → rss_list_feeds → web_extract → (阅读 Karpathy 博客)
轮次2 (10:08): web_search → web_extract → (搜索 + 抓取 RSS feed)
轮次3 (10:26): tool_search → rss_add_feed + memory (添加 Karpathy 订阅)
轮次4 (10:42): web_extract → (提取 Sequoia Ascent 原文)
轮次5 (10:43): tool_search → rss_list_feeds → rss_remove_feed x3 → (删除 3 个源)
轮次6 (10:53): tool_search → rss_list_feeds → (查看当前源列表)
轮次7 (12:44): (新对话开始，工具搜索 Karpathy 文章)
轮次8-10:      (纯对话，无工具调用)
```

### 历史消息统计（最新快照）

| 指标 | 值 |
|------|-----|
| 总消息数 | 40 (cleaned 后 38) |
| 用户消息 | 10 条 / 401 字符 |
| 助手文本 | 12 条 / 4,269 字符 |
| 工具调用 | 14 次 |
| 工具返回 | 14 次 / 7,745 字符 (**占比 62.4%**) |

---

## 4. 后台管线（每轮自动触发）

```
用户消息到达
    │
    ├─ 1. Honcho prefetch    → 语义召回相关记忆（注入 <memory-context>）
    ├─ 2. sanitize_history   → 清理孤立 tool response、残留 focus 消息
    ├─ 3. Agent 生成回复     → 主 Agent loop（可能含多轮工具调用）
    ├─ 4. persist_turn       → 持久化 + 记录 token usage
    ├─ 5. Honcho sync_turn   → 同步对话轮次到 Honcho（~6s）
    └─ 6. 记忆审查 Agent     → 后台 fork Agent 审查是否需写入 MEMORY.md（~5-8s）
```

---

## 5. 问题分析

### 问题 1：连续工具调用导致 input token 爆炸（P2）

**现象**：10:43 轮次 input 达到 **77,127 tokens**，是正常轮次的 5 倍。

**根因**：
1. 该轮次涉及 `tool_search` → `rss_list_feeds` → `rss_remove_feed` x3 → `memory`，共 6 次工具调用
2. 每次 `tool_search` 返回完整工具 schema（含参数描述），累积到历史中
3. DeepSeek 的工具 schema 作为 `tools` 参数每轮都发送，但历史中也保留了所有 `tool_call` / `tool` 消息对
4. 之前轮次 `web_extract` 返回的网页内容（~31K 压缩后）仍在历史中

**影响**：
- 单轮 API 成本急剧上升
- 长对话中历史不断膨胀，后续每轮的 input 都会更高

**建议修复**：
- [ ] `tool_search` 结果在历史中做摘要或截断（只保留 name + summary，不保留完整 schema）
- [ ] `web_extract` 返回内容在持久化时截断到合理长度（如 2000 字符）
- [ ] 考虑对连续工具调用轮次的历史做压缩（`on_pre_compress` 已有钩子）

### 问题 2：sanitize 频繁清理冗余消息（P3）

**现象**：最近 10 轮中，每轮都有 sanitize 操作：

```
12:44 sanitize: 移除残留 focus 消息         → cleaned 41 (original 42, removed 1)
12:47 sanitize: 移除孤立 tool response       → cleaned 36 (original 38, removed 2)
12:48 sanitize: 移除残留 focus 消息         → cleaned 37 (original 38, removed 1)
12:50 sanitize: 移除残留 focus 消息         → cleaned 39 (original 40, removed 1)
12:51 sanitize: 移除残留 focus 消息         → cleaned 41 (original 42, removed 1)
12:51 sanitize: 移除孤立 tool response       → cleaned 38 (original 40, removed 2)
```

**根因**：
1. **残留 focus 消息**：`focus_update` 工具的调用/返回被写入历史，sanitize 每轮清理后又因为 Honcho prefetch 注入 `focus` 相关内容而在 Agent 响应中重新产生
2. **孤立 tool response**：某些 `tool_call` 被历史截断移除，但对应的 `tool` response 仍残留

**影响**：
- 每轮多做一次 sanitize（额外 DB 写入）
- `original` 和 `cleaned` 的差值说明有无效数据被反复写入再清理

**建议修复**：
- [ ] `focus_update` 工具调用应在 Agent 响应处理阶段直接过滤，不写入持久化历史
- [ ] 历史截断时确保 `tool_call` 和对应的 `tool` response 成对移除（`sanitize_history` 已部分实现，但截断路径可能遗漏）

### 问题 3：Honcho prefetch 偶发失败（P3）

**现象**：
```
12:48 Honcho prefetch 失败: 'An unexpected error occurred'
    query_preview='AI作为一个新型事物渗透到我的生活...'
```

**根因**：Honcho 外部服务不稳定，偶发返回非 200 响应。

**影响**：
- 该轮次缺少 `<memory-context>` 注入，Agent 可能丢失上下文
- 系统已做静默降级（`MemoryManager` 记录 warning 并跳过），不中断对话

**建议修复**：
- [ ] 增加 retry（1 次，指数退避 2s）
- [ ] prefetch 失败时记录更详细的错误信息（HTTP status code、response body）
- [ ] 考虑在 L0 冻结快照中冗余存储关键记忆，减少对外部服务的运行时依赖

---

## 6. 优化建议汇总

| 优先级 | 问题 | 建议措施 | 预期效果 |
|:---:|------|---------|---------|
| P2 | 工具调用 token 爆炸 | 截断 web_extract 历史、摘要 tool_search 结果 | 减少 50-70% 的异常高 input |
| P3 | sanitize 冗余 | 过滤 focus_update 工具调用、确保 tool_call/tool 成对截断 | 消除每轮无效写入 |
| P3 | Honcho prefetch 失败 | 增加 retry + 更详细错误日志 | 提高记忆召回可靠性 |

---

## 7. 数据来源

- 日志文件：`logs/lumen.log`（尾部 3000 行）
- 会话 ID：`8595876131`（Telegram）
- 分析时间：2026-06-10 12:52 UTC（最后一条日志）
- 模型：`deepseek-v4-flash` via `https://api.deepseek.com`
