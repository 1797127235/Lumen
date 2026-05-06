# CareerOS 系统架构

**项目**: CareerOS（码路领航）
**架构模式**: 分层架构 + 事件驱动记忆系统
**最后更新**: 2026-05-06

---

## 1. 架构概览

CareerOS 采用前后端分离的分层架构，后端使用 FastAPI + SQLAlchemy，前端使用 React + Vite。
核心创新在于**事件驱动的记忆系统 + Agent 工具驱动写入**，实现了"越用越懂你"的个性化体验。

### 1.1 系统层次

```
┌──────────────────────────────────────────────────────────────┐
│                      Presentation Layer                       │
│                    React 19 + TypeScript                      │
│         Chat / Profile / Memories / Settings                 │
└──────────────────────────────────────────────────────────────┘
                              │
                         REST + SSE
                              │
┌──────────────────────────────────────────────────────────────┐
│                       API Layer (FastAPI)                     │
│    health / chat / profile / memory / skills / config        │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│                     Service Layer                             │
│   chat_service / profile_service / memory_service            │
│   growth_event_service / md_projector                        │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│                      Agent Layer                              │
│         PydanticAI Agent + Tools + LLM Router                │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│                    Data Layer                                 │
│   growth_events (truth) → .md files (projection)             │
│   FTS5 index (search)                                        │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. 核心架构决策

### 2.1 事件驱动记忆系统

**决策**: 使用 growth_events 表作为唯一真相源，通过投影器同步到 .md 文件。
Agent 工具（memory_save / update_profile）负责写入，后台审查负责兜底。

**理由**:
- 事件溯源支持时间旅行和审计
- 投影器可以重建任意时间点的状态
- 去重机制防止重复事件
- Agent 有完整上下文，最清楚什么值得记
- 后台审查兜底 Agent 遗漏的信息

**实现**:
```
写入路径: Agent 工具 (memory_save / update_profile)
    |
    ├─ 对话中主动调用 → growth_events
    |                       ↓
    |                   sync_projections → .md
    |
    └─ Agent 没调 → 后台审查 (asyncio.create_task)
                         ↓
                    fork Agent + review prompt
                         ↓
                    有信息→growth_events→.md
                    无信息→跳过
```

### 2.2 单用户模式

**决策**: 当前实现为单用户模式，user_id 由客户端 localStorage 控制。

**理由**:
- 简化初始实现
- 避免认证复杂性
- 适合自托管场景

**未来**: 生产环境需加 JWT 认证。

### 2.3 SQLite 作为主数据库

**决策**: 使用 SQLite + aiosqlite 作为主数据库。

**理由**:
- 零运维，单文件部署
- 适合自托管场景
- 支持 JSON 字段（profile_data）
- 足够单用户性能

---

## 3. 记忆系统架构

### 3.1 两层记忆模型

| 层级 | 存储 | 用途 | 注入时机 |
|------|------|------|---------|
| L1 | conversation messages | 短期上下文 | 最近 20 条消息 + 滚动摘要 |
| L2 | .md 文件 + growth_events | 结构化画像 + 语义检索 | system prompt + FTS5 |

**Cognee** 曾作为 L3 语义检索引入，但从未真正接入。当前语义搜索由 **FTS5 虚拟表** 提供（支持中文 trigram 分词）。

### 3.2 事件类型

| 事件类型 | 实体类型 | 触发工具 | 合并规则 |
|---------|---------|---------|---------|
| profile_updated | profile | update_profile | 递归深合并 |
| skill_added | skills | memory_save | 覆盖 |
| experience_added | experiences | memory_save | 追加 |
| preference_learned | preferences | memory_save | 覆盖 |
| decision_made | decisions | memory_save | 追加 |
| status_changed | status | memory_save | 覆盖 |
| goal_updated | goals | memory_save | 覆盖 |

### 3.3 FTS5 全文搜索

**两张虚拟表（建在 lifespan 中）**:
- `growth_events_fts` — 标准 FTS5（英文/拼音搜索）
- `growth_events_fts_trigram` — trigram 分词（中文子串搜索，如"AI Agent"）

**同步触发器**:
- `trg_growth_events_ai` / `trg_growth_events_tri_ai` — INSERT 时同步到 FTS
- `trg_growth_events_ad` / `trg_growth_events_tri_ad` — DELETE 时同步到 FTS
- `trg_growth_events_au` / `trg_growth_events_tri_au` — UPDATE 时同步到 FTS

> **已知问题**: SQLite 3.45.3 的 FTS5 DELETE 触发器存在兼容 bug（`INSERT ... VALUES('delete', ...)` 报 `SQL logic error`）。
> 在 DELETE 操作时先 `DROP TRIGGER`、执行删除、`DROP TABLE` 重建 FTS、再 `CREATE TRIGGER` 重建。
> 见 `routers/memory.py` 中 DELETE 端点的实现。

### 3.4 去重机制

- **UNIQUE 约束**: (user_id, dedupe_key)
- **dedupe_key 格式**: `{event_type}:{entity_type}:{entity_id}`

### 3.5 投影追踪

- `projected_md_at` — 最后投影到 .md 的时间
- `sync_projections()` — 在 chat_service.py 中由 `pending_event_ids` 触发

---

## 4. Agent 系统架构

### 4.1 PydanticAI Agent

```python
Agent(
    model=OpenAIChatModel,  # LiteLLM 路由
    deps_type=CareerOSDeps,
    output_type=str,
    system_prompt="...",  # 静态提示词：工具调用规则
    end_strategy="graceful",  # 流式模式同时返回文本+工具调用
)
```

**两层 system prompt**:
- **静态** (构造时传入): 工具调用规则（"目标→memory_save | 技能→memory_save | ..."）
- **动态** (`@agent.system_prompt`): 注入结构化画像 + 对话摘要 + 近期历史
  - 注意：context 在 system prompt 而非用户消息中——之前拼在用户消息会导致指令被淹没

**context 注入位置的历史**:
- 旧: 拼接到 user_input（模型混淆上下文和用户请求，工具调用指令被淹没）
- 新: `@agent.system_prompt` 装饰器注入（语义正确，模型能区分背景信息和当前请求）

### 4.2 工具注册

| 工具 | 功能 | 写入路径 | 触发时机 |
|------|------|---------|---------|
| memory_search | 搜索记忆（FTS5） | 只读 | Agent 主动调 |
| memory_save | 保存记忆（目标/技能/经历/偏好/决策/状态） | growth_events → .md | Agent 主动调 |
| update_profile | 更新结构化画像 | growth_events (profile) → .md | Agent 主动调 |
| get_profile | 获取画像 | 只读 | Agent 主动调（很少需要，已在 system prompt） |

**memory_save 参数演化**:
- memory_update/memory_add（旧，Cognee 时代）→ 已废弃
- memory_save(entity_type, section, content)（当前）：支持 6 种实体类型
- update_profile：14 个显式参数（原为 dict[str, Any]，PydanticAI 无法正确序列化任意 dict）

### 4.3 后台记忆审查

**当 Agent 本轮未调用 memory_save / update_profile 时兜底**:

```
Agent 回复完毕
  ↓ pending_event_ids 为空？
asyncio.create_task(_background_memory_review(...))
  ↓ 独立 db session
同模型 Agent + review prompt + 本轮对话
  ↓
模型决定是否有值得保存的信息
  ├─ 有 → memory_save → growth_events → sync_projections
  └─ 无 → "无需保存" → 跳过
```

**参考实现**: Hermes Agent (Nous Research) 的 `_spawn_background_review` 模式。

### 4.4 LLM 路由

- **统一层**: LiteLLM
- **Provider**: DashScope / OpenAI / DeepSeek / Anthropic / Gemini / Ollama / OpenRouter
- **DeepSeek 注意**: base_url 为 `https://api.deepseek.com`，兼容 OpenAI API 格式。PydanticAI 和 liteLLM 两条路径都已修复 base_url 处理。

---

## 5. 数据流

### 5.1 对话流程

```
用户消息 → POST /api/chat
    ↓
chat_service.py
    ├─ 创建/获取 Conversation
    ├─ 保存用户消息 → DB (commit)
    ↓
PydanticAI Agent (ReAct Loop)
    ├─ @agent.system_prompt 注入: memory.md + 摘要 + 历史
    ├─ user_input 原样传入（不拼接上下文）
    ↓
┌─────────┴─────────┐
↓                   ↓
工具调用          直接回复
↓                   ↓
growth_events     SSE 流式输出
↓
sync_projections → .md
        ↓
pending_event_ids 为空？
        ↓ 是
asyncio.create_task(后台记忆审查)
        ↓
独立 db session + Agent + review prompt
        ↓
有信息→growth_events→.md | 无→跳过
```

### 5.2 简历上传流程

```
上传文件 → POST /api/profile/resume
    ↓
profile_service.py
    ↓
markitdown (文本提取)
    ↓
LLM 解析 (结构化)
    ↓
growth_events (resume_uploaded + profile_updated)
    ↓
.md 投影器 (同步)
    ↓
memory.md + entities/*.md
```

---

## 6. 部署架构

### 6.1 Docker 部署

```yaml
services:
  career-os:
    image: ghcr.io/1797127235/career-os:latest
    ports:
      - "3000:3000"
    volumes:
      - career-data:/root/.careeros
```

### 6.2 数据持久化

```
~/.careeros/
├── career_os.db      # SQLite 数据库（growth_events + FTS5）
├── memory/           # .md 记忆文件
│   ├── memory.md     # 核心画像
│   ├── skills.md     # 技能
│   └── experiences.md # 经历
├── config.json       # 用户配置（API Key 等）
└── cognee_data/      # Cognee 索引（可选，当前未接入）
```

---

## 7. 安全考虑

### 7.1 当前状态

- ❌ 无认证（user_id 由客户端控制）
- ❌ 无 HTTPS（需反向代理）
- ❌ 无输入验证（LLM 提示词注入风险）

### 7.2 生产环境建议

- ✅ 添加 JWT 认证
- ✅ 使用 HTTPS
- ✅ 输入验证和净化
- ✅ API 限流
- ✅ 日志脱敏

---

## 8. 性能考虑

### 8.1 当前优化

- SQLite WAL 模式（并发读）
- 异步 IO（aiosqlite）
- SSE 流式输出（避免长等待）
- Fire-and-forget 事件创建

### 8.2 潜在瓶颈

- .md 投影器全量重建（大量事件时）
- LLM 调用延迟

---

## 9. 扩展性

### 9.1 水平扩展

- 当前为单实例部署
- SQLite 不支持多实例写入
- 未来可迁移到 PostgreSQL

### 9.2 功能扩展

- 多用户支持（加认证）
- 团队协作（共享记忆）
- 插件系统（自定义工具）
