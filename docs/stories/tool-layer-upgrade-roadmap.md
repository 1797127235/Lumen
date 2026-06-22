# Story：工具层全面升级路线图（ToolReturn + 新工具 + 架构升级）

> 状态：方案设计阶段
> 日期：2026-05-26
> 背景：ToolReturn 统一输出已完成，但工具层仍有多处痛点和缺口，需系统性升级。

---

## 背景与动机

2026-05-26 完成了工具层 **ToolReturn 结构化输出** 改造：所有 12+ 个内置工具 + MCP 桥接统一返回 `ToolReturn`（`return_value` 给 LLM，`metadata` 给应用层）。这是第一步。

但代码审计和行业调研显示，工具层仍有大量结构性问题：

- **现有工具问题**：输出格式不一致、token 浪费、错误信息缺乏恢复上下文、MCP 工具绕过中间件
- **发现机制问题**：tool_search 每次返回 pretty-print JSON 浪费 token，deferred discovery 消耗 tool call 预算，always-on 工具仍需搜索
- **能力缺口**：作为个人 AI 伙伴，缺少日历、邮件、笔记、任务规划、浏览器交互等核心工具
- **架构缺口**：无权限系统、无审计日志、无输出大小限制

本方案分为 **4 个阶段**，按 ROI 排序实施。

---

## 阶段 1：修痛点（优化现有工具）

### 1.1 修复 memory 工具 schema 合约不符

**问题**：`memory` 工具的 schema 广告了 `replace`/`remove` 操作，但实现直接拒绝：

```python
if action not in ("add",):
    return tool_error("暂仅支持 add")
```

LLM 看到 schema 里有 replace/remove，尝试调用后被拒绝，浪费一次 tool call。

**方案**：
- 方案 A：实现 replace/remove（推荐）—— 用字符串匹配定位旧条目，替换/删除
- 方案 B：从 schema 中移除 replace/remove，只保留 add

### 1.2 MCP 工具包中间件

**问题**：MCP 工具在 `tool_bridge.py` 中直接调用 `client_manager.call_tool()`，**完全绕过** `_middleware.py` 的日志和预算中间件：

```python
# tool_bridge.py
async def handler(args, deps):
    return await manager.call_tool(server_name, tool_name, args)  # 无日志、无预算
```

**方案**：MCP handler 也要经过 `wrap_with_logging` + `wrap_with_budget`。需要在 `factory.py` 的 MCP 注册逻辑中为每个 MCP 工具包装中间件。

### 1.3 工具输出精简模式

**问题**：多个工具返回 verbose 输出，浪费 token：

| 工具 | 问题 | 影响 |
|---|---|---|
| `tool_search` | 返回 pretty-print JSON（indent=2） | 每次搜索浪费 200-500 tokens |
| `web_search` | 返回完整 markdown 格式结果 | 每次搜索浪费 500-2000 tokens |
| `shell` (task_output) | 每次轮询返回完整日志 | 后台任务轮询浪费大量 tokens |
| `file_write` | 返回完整 diff（ fenced code block） | 大文件写入浪费 tokens |
| `get_profile` | 返回完整 about_you.md 内容 | 每次都把画像全文塞进 context |

**方案**：
- `tool_search`：返回紧凑单行摘要，去掉 pretty-print
- `web_search`：返回结构化 JSON（标题/链接/摘要），不在工具结果里做 markdown 排版
- `task_output`：支持增量读取（cursor 参数），只返回新增内容
- `file_write`：大文件只返回 "写入成功，前 20 行预览..."
- `get_profile`：返回结构化摘要（key-value），不是完整 markdown

### 1.4 错误信息加恢复上下文

**问题**：当前 `tool_error` 只返回人类可读文本：

```python
return tool_error("文件不存在: /path/to/file")
```

LLM 看到这个错误后不知道下一步该做什么（是创建文件？还是检查路径？还是询问用户？）。

**方案**：错误信息增加 `hint` 字段，指导 LLM 如何恢复：

```python
return tool_error(
    "文件不存在: /path/to/file",
    code="FILE_NOT_FOUND",
    hint="可用 file_ls 查看目录内容，或用 file_write 创建新文件"
)
```

所有工具的 error 路径都需要补充 `hint`。

### 1.5 中间件管道化

**问题**：`_middleware.py` 只有两个包装器：`logged` 和 `budgeted`。缺：
- 输入校验（参数类型/范围检查）
- 输出截断（防止单次返回撑爆 context）
- Redaction（敏感信息脱敏）
- 审计日志（谁调用了什么）
- 重试逻辑

**方案**：把中间件改成 **管道模式**：

```python
def wrap_tool(handler, name, risk):
    pipeline = compose(
        validate_input,      # 参数校验
        logged,              # 日志
        budgeted,            # 预算控制
        truncate_output,     # 输出截断（新增）
        redact_sensitive,    # 敏感信息脱敏（新增）
        audit_log,           # 审计日志（新增）
    )
    return pipeline(handler)
```

---

## 阶段 2：发现机制升级

### 2.1 always-on 工具免搜索

**问题**：当前所有非核心工具都 deferred，即使是 `get_profile`、`memory_search` 这种低风险、高频使用的工具也需要先 `tool_search` 才能调用。这浪费了 tool call 预算，也增加了延迟。

**方案**：
- 低风险 + 高频工具默认 always-on：`get_profile`, `memory_search`, `tool_search`
- 在 `factory.py` 中配置 `ALWAYS_ON_TOOLS = {...}`
- 这些工具直接注入 schema，不走 deferred discovery

### 2.2 tool_search 返回紧凑格式

**问题**：当前返回 pretty-print JSON：

```json
{
  "matched": [
    {
      "name": "shell",
      "summary": "在系统 shell 中执行命令...",
      "why_matched": ["名称:精确匹配"],
      "risk": "destructive"
    }
  ]
}
```

**方案**：返回单行摘要：

```
✓ shell (destructive) — 系统命令执行
```

### 2.3 deferred hint 压缩

**问题**：`build_deferred_tools_hint()` 每轮 system prompt 都注入完整工具目录，当 MCP 工具多时（20+ 个），这部分可能占 2000+ tokens。

**方案**：
- 只注入 **名称 + 一句话描述**，不注入参数 schema
- schema 在 tool_search 结果里提供
- 或改为按需注入：只有当工具数量 > 5 时才注入 hint

### 2.4 语义搜索替代子串匹配

**问题**：`_registry.search()` 当前只用 `name in query or description in query`，不支持同义词、模糊匹配。

**方案**：
- 短中期：用 BM25 或 simple-rank 替代子串匹配
- 长期：为每个工具生成 embedding，语义搜索

---

## 阶段 3：新增核心工具（个人 AI 伙伴定位）

### 3.1 Todo / Task Planner（P0）

**需求**：结构化任务列表，跨会话持久化。Agent 可以创建、完成、查询任务。

**场景**：
- 用户："帮我记着周三之前把简历更新了"
- Agent 调用 `todo_create(task="更新简历", due="2026-05-28")`
- 后续会话 Agent 主动提醒："你之前说要周三前更新简历，进展如何？"

**实现**：
- 数据存 SQLite（`tasks` 表：id, title, description, status, due_date, created_at, completed_at）
- 工具：`todo_create`, `todo_list`, `todo_complete`, `todo_delete`

### 3.2 File Edit / Patch（P0）

**需求**：安全的局部文件编辑，非整文件覆写。

**问题**：当前 `file_write` 只能整文件覆写，无法做 SEARCH/REPLACE 式编辑。

**实现**：
- 新增 `file_edit(file_path, old_text, new_text)`
- 用字符串匹配定位 old_text，替换为 new_text
- 支持多行匹配
- 匹配失败时返回详细错误（第几行不匹配、期望什么、实际是什么）

参考：Aider 的 SEARCH/REPLACE block、OpenAI 的 ApplyPatchTool。

### 3.3 Calendar / Reminder（P1）

**需求**：日程管理 + 定时提醒。

**场景**：
- 用户："下周三下午 3 点我有个会"
- Agent：`calendar_add_event(title="会议", start="2026-05-28T15:00", reminder="30min")`
- 到时间前 Agent 主动提醒

**实现**：
- 对接系统日历（Windows: Outlook/Calendar；macOS: Calendar；Linux: caldav）
- 或简单实现：SQLite `events` 表 + 后台轮询检查
- 工具：`calendar_add`, `calendar_list`, `calendar_delete`

### 3.4 Notes / Bookmarks Vault（P1）

**需求**：笔记、书签、阅读列表，Obsidian 兼容。

**场景**：
- 用户："这篇文章不错，帮我记下来"
- Agent：`note_save(title="AI Agent 开发指南", content="...", tags=["ai", "agent"])`
- 后续：`note_search(query="agent 框架")`

**实现**：
- 存为 Markdown 文件（`~/.lumen/notes/*.md`）
- 支持 frontmatter（tags, created, source URL）
- 工具：`note_save`, `note_read`, `note_search`, `note_list`, `note_delete`
- 可选：与 Obsidian vault 双向同步

### 3.5 Browser / Web Interaction（P1）

**需求**：网页交互（点击、填表、截图、提取内容）。

**场景**：
- 用户："帮我查一下这个网页上的价格"
- Agent：`browser_navigate(url="...")` → `browser_extract(selector=".price")`

**实现**：
- 用 Playwright 或 Selenium
- 工具：`browser_navigate`, `browser_click`, `browser_fill`, `browser_extract`, `browser_screenshot`
- 安全：只允许 HTTP/HTTPS，禁止访问 localhost/内网

### 3.6 Email（P2）

**需求**：邮件起草、搜索、发送。

**场景**：
- 用户："帮我起草一封感谢信"
- Agent：`email_draft(to="boss@company.com", subject="感谢", body="...")`
- 用户确认后：`email_send(draft_id="...")`

**实现**：
- 对接邮件服务商（SMTP/IMAP）
- 或对接 Gmail API（OAuth2）
- 工具：`email_draft`, `email_send`, `email_search`, `email_read`

### 3.7 Git Ops（P2）

**需求**：Git 操作（diff、blame、log、status）。

**场景**：
- 用户："最近谁改了这段代码？"
- Agent：`git_blame(file="main.py", line=42)`

**实现**：
- 调用 `git` CLI（通过 shell 工具即可，但建议包装为结构化输出）
- 工具：`git_status`, `git_diff`, `git_log`, `git_blame`

### 3.8 Document Ingest + Semantic Search（P2）

**需求**：PDF/网页/笔记摄入 + 语义搜索。

**场景**：
- 用户上传 PDF → Agent 提取文本 → 存入向量数据库
- 用户："我之前上传的那份合同里怎么说的？"
- Agent：`doc_search(query="违约条款")`

**实现**：
- 文本提取：PyPDF2 / docling / unstructured
- 向量化：Sentence Transformers + SQLite FTS5（或可选 FAISS）
- 工具：`doc_ingest`, `doc_search`, `doc_list`

### 3.9 Notifications（P3）

**需求**：Slack / SMS / 桌面推送。

**实现**：
- Slack：Webhook API
- 桌面：Windows Toast / macOS Notification / Linux notify-send
- 工具：`notify_send(title, message, channel="desktop")`

### 3.10 JSON / CSV Tools（P3）

**需求**：结构化数据处理。

**实现**：
- `json_query(file_path, query)` — JSONPath / jq 式查询
- `csv_query(file_path, sql)` — 用 DuckDB 做 SQL 查询

---

## 阶段 4：架构升级

### 4.1 工具权限系统

**需求**：按用户/会话/渠道控制工具可见性和风险等级。

**实现**：
- 配置层：`~/.lumen/tool_permissions.json`
- 支持按渠道限制（Telegram 禁止 shell）
- 支持按用户限制（访客只能 read-only 工具）
- 支持审批流（destructive 工具需要用户确认）

### 4.2 审计日志

**需求**：每次工具调用记录到 DB。

**实现**：
- SQLite `tool_audit_log` 表：
  - `id`, `conversation_id`, `user_id`, `tool_name`, `arguments`, `result_preview`, `duration_ms`, `timestamp`
- 不存完整 result（可能太大），只存 preview（前 200 字符）
- 可用于：调试、安全审查、用量分析

### 4.3 工具链编排

**需求**：工具返回"建议下一步调用 X"的 hint。

**场景**：
- `file_read` 返回 "文件不存在" + hint="建议调用 file_ls 查看目录"
- LLM 看到 hint 后自动调用 file_ls

**实现**：
- 在 `tool_error` / `tool_ok` 中增加 `suggest_next` 字段
- Agent 的 system prompt 中说明：如果工具返回 suggest_next，优先执行建议

### 4.4 输出大小限制

**需求**：全局截断策略，防止单次工具返回撑爆 context。

**实现**：
- 中间件层 `truncate_output`：
  - 默认截断阈值：10000 字符
  - 超过阈值时返回："结果过长，已截断。完整内容保存至 {path}"
- 不同工具可配置不同阈值（shell 可以更高，file_read 更低）

---

## 实施优先级

| 优先级 | 阶段 | 改动 | 预计工时 | ROI |
|---|---|---|---|---|
| P0 | 阶段 1.1 | memory schema 修复 | 2h | 高（立即节省 tool call） |
| P0 | 阶段 1.2 | MCP 中间件 | 4h | 高（补齐架构完整性） |
| P0 | 阶段 2.1 | always-on 工具 | 2h | 高（减少搜索调用） |
| P0 | 阶段 2.2 | tool_search 精简 | 2h | 高（节省 token） |
| P0 | 阶段 3.1 | todo/task planner | 1d | 高（核心功能） |
| P0 | 阶段 3.2 | file_edit (patch) | 1d | 高（核心功能） |
| P1 | 阶段 1.3 | 输出精简 | 1d | 中（长期节省 token） |
| P1 | 阶段 1.4 | 错误 hint | 4h | 中（改善 LLM 恢复） |
| P1 | 阶段 1.5 | 中间件管道 | 1d | 中（架构升级） |
| P1 | 阶段 2.3 | deferred hint 压缩 | 4h | 中（节省 prompt token） |
| P1 | 阶段 3.3 | calendar | 2d | 高（核心功能） |
| P1 | 阶段 3.4 | notes vault | 1d | 高（核心功能） |
| P1 | 阶段 3.5 | browser | 2d | 中（功能扩展） |
| P2 | 阶段 3.6 | email | 2d | 中（功能扩展） |
| P2 | 阶段 3.7 | git ops | 1d | 低（开发者场景） |
| P2 | 阶段 3.8 | document ingest | 2d | 中（功能扩展） |
| P2 | 阶段 4.1 | 权限系统 | 2d | 低（安全） |
| P2 | 阶段 4.2 | 审计日志 | 1d | 低（可观测） |
| P3 | 阶段 2.4 | 语义搜索 | 2d | 低（体验优化） |
| P3 | 阶段 3.9 | notifications | 1d | 低（功能扩展） |
| P3 | 阶段 3.10 | JSON/CSV | 1d | 低（功能扩展） |
| P3 | 阶段 4.3 | 工具链编排 | 1d | 低（体验优化） |
| P3 | 阶段 4.4 | 输出限制 | 4h | 低（安全） |

**建议前两周先做 P0 + P1（约 5-7 天）**，后两周做 P2（约 7-10 天）。

---

## 参考

- PydanticAI ToolReturn 设计（历史参考）：[pydantic-ai profiles](https://github.com/pydantic/pydantic-ai/blob/efd468f3692f9115f58e3233e82065d97d3799f6/pydantic_ai_slim/pydantic_ai/profiles/openai.py)
- OpenAI Agents SDK 工具集：[agents/tool.py](https://github.com/openai/openai-agents-python/blob/fedc809afd5abb492df21c8e6bf365653b06c21f/src/agents/tool.py)
- LangChain 工具生态：[langchain-community tools](https://github.com/langchain-ai/langchain-community/tree/main/libs/community/langchain_community/tools)
- Aider 编辑模式：[editblock_prompts.py](https://github.com/Aider-AI/aider/blob/main/aider/coders/editblock_prompts.py)

---

## 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-05-26 | AI Agent | 初稿：基于代码审计 + 行业调研 |
