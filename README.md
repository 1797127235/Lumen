# Lumen

<p align="center">
  <b>一个真正认识你的 AI 伴侣</b><br>
  <i>伴你从学生到未来，深谋远虑，始终平易近人</i>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#数据架构">架构</a> ·
  <a href="#agent-系统">Agent</a> ·
  <a href="#项目结构">结构</a> ·
  <a href="#文档">文档</a>
</p>

---

## 这是什么

一个单用户 AI 伴侣系统，持续积累对你的了解——你的经历、目标、困惑、决定——随时间真正认识你这个人。

**目标用户**：处于学生到职场初期，面对方向选择、个人成长、人生规划的人。

**核心价值**：你不需要每次重新解释自己。数据在本地，不依赖云端，越用越懂你。

**技术栈**：FastAPI + SQLAlchemy + PydanticAI + Cognee + LiteLLM + React + Vite + Tailwind

---

## 快速开始

### 方式一：Docker（推荐）

```bash
git clone https://github.com/1797127235/Lumen.git
cd Lumen
docker compose up -d
```

打开 `http://localhost:3000`，在设置页选择 LLM Provider 并填入 API Key，开始使用。

### 方式二：本地开发

```bash
# 后端
pip install -r requirements.txt

# 前端
cd app/frontend && npm install && cd ../..

# 配置
cp .env.example .env
# 编辑 .env，配置 API Key（或在浏览器设置页填写）

# 启动
# Windows: 双击 run.bat
# PowerShell: .\run.ps1
```

打开 `http://localhost:5173`，开始使用。

---

## 数据架构

Lumen 使用**事件驱动的记忆系统**：

```
写入路径:
Agent 工具（memory_save / update_profile）
    |
    ├─ 对话中主动调用 → growth_events
    |                       ↓
    |                   sync_projections → .md（投影）
    |                                     → Cognee（语义索引）
    |
    └─ Agent 没调 → 后台审查（asyncio.create_task）
                         ↓
                    独立 Agent + review prompt
                         ↓
                    有信息→growth_events→.md+Cognee
                    无信息→跳过
```

**三层记忆模型**：

| 层级 | 存储 | 用途 |
|------|------|------|
| L0 固定块 | growth_events 全量 | 身份、目标、技能、偏好（800 字预算，动态分配） |
| L1 近期块 | growth_events（30 天内） | 最近动态，按事件类型衰减过滤 |
| L2 语义召回 | Cognee + FTS5 + .md | 根据对话内容实时检索相关记忆 |

**注入流程**（在 Agent system prompt 中）：

```
build_snapshot() → L0 固定块（全量事件构建，5 分钟 TTL 缓存）
                → L1 近期块（30 天内事件，类型权重衰减过滤）
       +
build_context() → L2 语义召回（根据 user_input 检索，去重 L1）
       ↓
<memory-context> 围栏标签注入 system prompt
```

所有数据存储在 `~/.lumen/`：

```
~/.lumen/
├── lumen.db      # SQLite（事件、对话、FTS5 索引）
├── memory/       # .md 记忆投影文件
│   ├── memory.md       # 核心画像
│   ├── skills.md       # 技能
│   └── experiences.md  # 经历
├── kuzu/         # Cognee 图谱数据（首次写入后自动创建）
├── lancedb/      # Cognee 向量数据（首次写入后自动创建）
└── config.json   # 用户设置（API Key 等）
```

---

## Agent 系统

Lumen 使用 PydanticAI 实现的 Agent ReAct Loop，前端实时展示工具调用过程：

```
用户消息 → POST /api/chat
    ↓
chat_service.py
    ├─ 创建/获取 Conversation
    ├─ 保存用户消息
    ↓
@agent.system_prompt 注入：三层记忆（build_context）
    ↓
PydanticAI Agent (ReAct Loop)
    ├─ 工具调用 → 前端实时展示 TracePanel
    │   ├─ memory_save / update_profile → sync_projections → .md + Cognee
    │   └─ memory_search → 语义检索 → 返回结果
    └─ 流式输出（SSE）→ 前端逐字渲染
    ↓
Agent 没调工具？ → 后台审查兜底（fork Agent + review prompt）
```

**工具列表**：

| 工具 | 用途 |
|------|------|
| `memory_search(query, files?)` | 搜索记忆（FTS5 + Cognee 语义） |
| `memory_save(entity_type, section, content)` | 保存记忆（目标/技能/经历/偏好/决策/状态） |
| `update_profile(school_name, major, grade, ...)` | 更新结构化画像（14 个显式参数） |
| `get_profile()` | 获取画像（很少需要，已在 system prompt） |

**可观测性**：每个 Agent 运行记录在 `agent_traces` 表，前端实时展示工具调用（思考中 → 调用某个工具 → 完成 + 耗时）。

---

## 记忆管理

- **分层注入**：L0 固定块（800 字预算，动态分配）+ L1 近期块（10 条，类型衰减）+ L2 语义召回（Cognee→FTS5→.md）
- **缓存**：5 分钟 TTL，L1 时间窗口准实时
- **搜索链**：Cognee 语义 → SQLite FTS5 全文 → .md 子串兜底
- **补偿扫描**：`POST /api/memory/compensate` 重试未投影到 Cognee 的事件
- **衰减策略**：事件类型权重（0.0~0.3）× 年龄（天）＞ 5.0 时丢弃

---

## API 端点

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/health` | 健康检查（含 Cognee 状态） |
| `POST` | `/api/chat` | SSE 流式对话（Agent Loop） |
| `GET`  | `/api/chat/history` | 对话历史列表 |
| `GET`  | `/api/chat/{id}` | 单条会话消息 |
| `DELETE` | `/api/chat/{id}` | 删除对话 |
| `POST` | `/api/profile/resume` | 上传简历，LLM 自动提取画像 |
| `GET`  | `/api/profile/me` | 获取用户画像 |
| `PATCH` | `/api/profile/me` | 更新画像 |
| `DELETE` | `/api/profile/me` | 重置画像 |
| `GET`  | `/api/memory/me` | 读取 `.md` 画像内容 |
| `GET`  | `/api/memory/stats` | 记忆统计 |
| `GET`  | `/api/memory/list` | 记忆列表 |
| `POST` | `/api/memory/reset` | 重置记忆（SQLite + .md + Cognee） |
| `POST` | `/api/memory/rebuild` | 重建记忆投影 |
| `POST` | `/api/memory/compensate` | 补偿 Cognee 投影 |
| `DELETE` | `/api/memory/{id}` | 删除单条事件记忆 |
| `GET`  | `/api/memory/search` | 搜索记忆（FTS5 + Cognee） |
| `GET/POST/PATCH/DELETE` | `/api/skills` | 技能记录 CRUD |
| `GET/POST` | `/api/config` | 读取/更新用户配置 |
| `POST` | `/api/config/test` | 测试 LLM Provider 连接 |

---

## 项目结构

```
career-os/
├── app/backend/
│   ├── main.py                 # FastAPI 入口，lifespan 中 init Cognee + cognify_loop
│   ├── config.py               # 环境变量 + config.json 双层配置
│   ├── db/
│   │   ├── base.py             # SQLAlchemy AsyncEngine + Base
│   │   └── session.py          # get_db 依赖注入
│   ├── models/
│   │   ├── user.py             # User + UserProfile
│   │   ├── conversation.py     # Conversation + Message
│   │   ├── growth_event.py     # GrowthEvent 事件溯源（带投影追踪）
│   │   └── agent_trace.py      # AgentTrace 可观测性
│   ├── agent/
│   │   ├── pydantic_agent.py   # PydanticAI Agent 定义
│   │   ├── tools/              # 4 个 Agent 工具
│   │   ├── llm_router.py       # LLM 路由（多 Provider）
│   │   └── deps.py             # Agent 依赖注入
│   ├── memory/
│   │   ├── facade.py           # LumenMemory 统一入口
│   │   ├── search.py           # 三层搜索（Cognee→FTS5→.md）
│   │   ├── stores/
│   │   │   ├── relational.py   # GrowthEventRepository（CRUD + FTS）
│   │   │   └── semantic.py     # SemanticStore（Cognee 封装）
│   │   ├── projections/
│   │   │   ├── snapshot.py     # 分层注入（L0 固定块 + L1 近期块）
│   │   │   ├── events_merger.py # 事件合并纯函数
│   │   │   └── markdown.py     # .md 文件投影
│   │   └── cognee_admin/
│   │       ├── cognify_loop.py # Cognee 初始化 + 后台 cognify 循环
│   │       └── datasets.py     # Dataset 命名常量
│   ├── routers/
│   │   ├── chat.py, health.py, memory.py, config_router.py
│   ├── schemas/                # Pydantic 请求/响应模型
│   └── services/
│       ├── chat_service.py     # 对话业务 + Agent Loop 集成
│       ├── memory_service.py   # 记忆投影
│       ├── review_service.py   # 后台审查兜底
│       └── summary_service.py  # 对话摘要生成
├── app/frontend/
│   └── src/
│       ├── pages/
│       │   ├── Chat.tsx        # SSE 流式 + TracePanel 工具调用可视化
│       │   ├── Profile.tsx     # 画像页 inline 编辑
│       │   ├── Memories.tsx    # 记忆管理
│       │   └── Settings.tsx    # LLM Provider + API Key 配置
│       └── lib/
│           ├── api.ts          # API 调用 + SSE 解析
│           ├── chatSession.tsx # 聊天状态管理
│           └── userId.ts       # 用户 ID 管理
├── docs/
│   ├── architecture/           # 系统架构核心决策
│   ├── memory-structure/       # 记忆数据模型
│   ├── stories/                # 功能实现记录
│   └── project-context.md      # AI Agent 编码规则
└── tests/                      # pytest 测试
```

---

## 技术选型

| 层级 | 选型 | 说明 |
|------|------|------|
| 后端 | FastAPI + SQLAlchemy 2.0 | async，类型安全 |
| 数据库 | SQLite（单文件） | 自托管首选，零运维 |
| LLM | LiteLLM（7+ Provider） | 多 Provider 统一路由，用户自选 |
| Agent | PydanticAI + ReAct Loop | 流式推理，4 个工具，可观测性 |
| 记忆层 | growth_events → .md（FTS5） + Cognee（语义） | 事件溯源 + 投影架构 + 分层注入 |
| 前端 | React 19 + Vite + Tailwind CSS 4 | OKLCH 配色，TracePanel 工具可视化 |

---

## 设计理念

- **自托管优先**：数据在本地，不依赖外部服务，用户自己掌控
- **事件驱动**：所有写入走 growth_events，通过投影器同步到 .md + Cognee
- **Agent 工具驱动**：Agent 在对话中主动调用工具保存记忆，而非后台自动提取
- **后台审查兜底**：Agent 未主动保存时，后台 fork Agent 审查 → 决定是否保存
- **分层注入**：固定块（长期画像）+ 近期块（时间衰减）+ 语义召回（Cognee→FTS5），控制 token 预算

---

## 文档

- [产品简介](docs/product-brief.md) — 产品定位和价值主张
- [系统架构](docs/architecture/) — 架构设计和核心决策
- [记忆结构](docs/memory-structure/) — 记忆数据模型和实体定义
- [Project Context](docs/project-context.md) — AI Agent 编码规则
- [Stories](docs/stories/) — 功能实现记录

---

## 已知限制

- **无认证**：`user_id` 由客户端 localStorage 控制，无 JWT 鉴权。单用户场景无需，多用户需加认证
- **单机部署**：SQLite 不支持并发写入，不适合多实例部署
- **Cognee 依赖外部 LLM**：Embedding 和 cognify 需要 LLM API 可用

---

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `LLM_API_KEY` | ✅ | LLM API Key（也可在设置页填写） |
| `LLM_PROVIDER` | ❌ | 默认 `dashscope`（可选 openai/deepseek 等） |
| `LLM_MODEL` | ❌ | 默认 `qwen-plus` |
| `DATABASE_URL` | ❌ | 默认 `~/.lumen/lumen.db` |
| `DEBUG` | ❌ | `true`（开发）/ `false`（Docker 生产） |
| `COGNEE_COGNIFY_INTERVAL_SEC` | ❌ | Cognee 批量处理间隔（默认 60） |

完整配置见 `.env.example`。

---

## License

[MIT](LICENSE)
