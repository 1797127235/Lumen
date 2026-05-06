# CareerOS

<p align="center">
  <b>面向中国 CS 学生的自托管 AI 职业规划助手</b><br>
  <i>从大一陪伴到毕业，越用越懂你</i>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#功能特性">功能</a> ·
  <a href="#项目结构">结构</a> ·
  <a href="#文档">文档</a> ·
  <a href="#参与贡献">贡献</a>
</p>

---

## 这是什么

一个帮计算机专业学生做职业规划的 AI Agent。不是"拿了 JD 帮我匹简历"，而是"我不知道该学什么、该投什么"。

**目标用户**：普通本科/211 的 CS 在校生，大一迷茫或大三找实习碰壁。

**核心价值**：数据在本地、自己部署、用四年——不是一次性工具，是一个越用越懂你的系统。

**技术栈**：FastAPI + SQLAlchemy + PydanticAI + LiteLLM + React + Vite + Tailwind

---

## 功能特性

| 功能 | 状态 | 说明 |
|------|------|------|
| 智能对话 | ✅ | SSE 流式输出，Agent ReAct Loop，工具调用 |
| 长期记忆 | ✅ | 事件溯源（growth_events）+ 后台审查兜底 |
| 用户画像 | ✅ | 上传简历 → LLM 自动提取 → Agent 工具修正 |
| 技能记录 | ✅ | 表单管理 + 对话中识别 |
| 语义搜索 | ⚠️ | FTS5 全文搜索（Cognee 待调通） |

---

## 快速开始

### 方式一：Docker（推荐）

```bash
git clone https://github.com/1797127235/CareerOS.git
cd CareerOS
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

## 项目结构

```
career-os/
├── app/backend/
│   ├── main.py              # FastAPI 入口，lifespan 中自动 create_all
│   ├── config.py            # pydantic-settings，从根目录 .env 加载
│   ├── db/
│   │   ├── base.py          # SQLAlchemy AsyncEngine + Base 声明
│   │   └── session.py       # get_db 依赖注入（yield + commit/rollback）
│   ├── models/
│   │   ├── user.py           # User + UserProfile（含 profile_data JSON）
│   │   ├── conversation.py   # Conversation + Message
│   │   ├── growth_event.py   # GrowthEvent（记忆真相源）
│   │   ├── skill_record.py   # SkillRecord 技能成长记录
│   │   └── agent_trace.py    # AgentTrace 可观测性
│   ├── agent/
│   │   ├── pydantic_agent.py  # PydanticAI Agent 定义 + 动态系统提示词
│   │   ├── pydantic_tools.py  # Agent 工具（memory_search/save, update_profile）
│   │   ├── llm_router.py     # LLM 路由（多 Provider），流式+非流式
│   │   └── deps.py           # Agent 依赖注入（CareerOSDeps）
│   ├── routers/
│   │   ├── chat.py           # POST /api/chat (SSE), GET /api/chat/history
│   │   ├── profile.py        # GET/PATCH/DELETE /api/profile/me, POST /api/profile/resume
│   │   ├── memory.py         # /api/memory/me/stats/list/search/reset/rebuild/delete
│   │   ├── skills.py         # CRUD /api/skills
│   │   └── config_router.py  # GET/POST /api/config
│   ├── schemas/
│   │   └── profile.py        # ProfileResponse, ProfileUpdate
│   └── services/
│       ├── chat_service.py   # 对话业务：Agent Loop + SSE + 后台记忆审查
│       ├── profile_service.py # 简历提取 + LLM 解析
│       ├── memory_service.py # .md 文件记忆层
│       ├── growth_event_service.py # GrowthEvent CRUD + 去重
│       ├── md_projector.py   # SQLite → .md 投影器
│       └── careeros_memory.py # 统一记忆门面（FTS5 + Cognee + .md）
├── app/frontend/
│   └── src/
│       ├── pages/
│       │   ├── Chat.tsx      # SSE 流式对话 + 历史抽屉
│       │   ├── Profile.tsx   # 画像页：教育/技能/获奖 + inline 编辑
│       │   ├── Memories.tsx  # 记忆管理页
│       │   └── Settings.tsx  # 设置页：Provider 选择 + API Key
│       └── lib/
│           ├── api.ts        # 后端 API 调用 + SSE 解析
│           └── chatSession.tsx # 全局聊天状态管理（Context）
├── tests/                    # pytest 测试用例
├── docs/                     # 项目文档
├── Dockerfile                # 多阶段构建（node + python）
├── docker-compose.yml        # 单服务 + 持久化 volume
├── pyproject.toml            # ruff + pytest 配置
└── run.ps1 / run.bat         # 本地开发启动脚本
```

---

## 数据架构

CareerOS 使用**事件驱动的记忆系统**：

```
写入路径:
Agent 工具（memory_save / update_profile）
    |
    ├─ 对话中主动调用 → growth_events
    |                       ↓
    |                   sync_projections → .md（投影）
    |
    └─ Agent 没调 → 后台审查（asyncio.create_task）
                         ↓
                    独立 Agent + review prompt
                         ↓
                    有信息→growth_events→.md
                    无信息→跳过
```

**两层记忆模型**:

| 层级 | 存储 | 用途 |
|------|------|------|
| L1 | conversation messages | 短期上下文（最近 20 条 + 滚动摘要） |
| L2 | growth_events → .md | 结构化画像 + FTS5 全文搜索 |

所有数据存储在 `~/.careeros/`：
```
~/.careeros/
├── career_os.db      # SQLite（事件、对话、技能、FTS5 索引）
├── memory/           # .md 记忆文件
│   ├── memory.md     # 核心画像
│   ├── skills.md     # 技能
│   └── experiences.md # 经历
├── kuzu/             # Cognee 图谱数据（可选，待调通）
├── lancedb/          # Cognee 向量数据（可选，待调通）
└── config.json       # 用户设置（API Key 等）
```

---

## 技术选型

| 层级 | 选型 | 说明 |
|------|------|------|
| 后端 | FastAPI + SQLAlchemy 2.0 | async，类型安全 |
| 数据库 | SQLite（单文件） | 自托管首选，零运维 |
| LLM | LiteLLM（DashScope / OpenAI / DeepSeek / Anthropic / Gemini / Ollama / OpenRouter）| 多 Provider 统一路由，用户自选 |
| Agent | PydanticAI + ReAct Loop | 流式推理，工具调用，可观测性 |
| 记忆层 | growth_events → .md（FTS5 全文搜索 + Cognee 语义） | 事件溯源 + 投影架构 |
| 前端 | React 19 + Vite + Tailwind CSS 4 | OKLCH 配色，响应式 |
| 部署 | Docker Compose | 单容器，单端口，持久化 volume |

---

## Agent 系统

CareerOS 使用 PydanticAI 实现的 Agent ReAct Loop：

```
用户消息 → POST /api/chat
    ↓
chat_service.py
    ├─ 创建/获取 Conversation
    ├─ 保存用户消息
    ↓
@agent.system_prompt 注入：memory.md + 摘要 + 历史
    ↓
PydanticAI Agent (ReAct Loop)
    ├─ 工具调用（memory_save / update_profile / memory_search）
    └─ 流式输出（SSE）
    ↓
Agent 调了工具？ → sync_projections → .md
    ↓ 没调？
后台审查兜底（fork Agent + review prompt）
```

**工具列表**：
- `memory_search(query, files?)` — 搜索记忆（FTS5 全文搜索）
- `memory_save(entity_type, section, content)` — 保存记忆（目标/技能/经历/偏好/决策/状态）
- `update_profile(school_name, major, grade, ...)` — 更新结构化画像（14 个显式参数）
- `get_profile()` — 获取画像（很少需要，已在 system prompt）

**可观测性**：每个 Agent 运行记录在 `agent_traces` 表，包含推理步骤、工具调用、耗时。

---

## API 端点

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/health` | 健康检查 |
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
| `DELETE` | `/api/memory/{id}` | 删除单条事件记忆 |
| `GET`  | `/api/memory/search` | 搜索记忆（FTS5 + Cognee） |
| `GET/POST/PATCH/DELETE` | `/api/skills` | 技能记录 CRUD |
| `GET/POST` | `/api/config` | 用户配置 |

---

## 设计理念

- **自托管优先**：数据在本地，不依赖外部服务，用户自己掌控
- **事件驱动**：所有写入走 growth_events，通过投影器同步到 .md
- **Agent 工具驱动**：Agent 在对话中主动调用工具保存记忆，而非后台自动提取
- **后台审查兜底**：Agent 未主动保存时，后台 fork Agent 审查 → 决定是否保存
- **Agent 而非问答**：ReAct Loop + 工具调用，真正的 Agent 系统

---

## 文档

- [项目概览](docs/project-overview.md) — 项目简介和技术栈
- [系统架构](docs/architecture.md) — 架构设计和核心决策
- [API 契约](docs/api-contracts.md) — API 端点文档
- [数据模型](docs/data-models.md) — 数据库模型文档
- [开发指南](docs/development-guide.md) — 开发环境搭建
- [Project Context](docs/project-context.md) — AI Agent 实现规则

完整文档索引：[docs/index.md](docs/index.md)

---

## 开发规范

### 代码质量

```bash
# Lint + 格式化
ruff check . && ruff format --check .

# 测试
pytest

# 提交前自动检查（已配置 pre-commit hook）
```

CI 会在每次 push 时自动运行：ruff check → ruff format → pytest → frontend build。

### 分支与提交

```bash
git checkout -b feat/your-feature
git commit -m "feat: add something"
git push origin feat/your-feature
# 然后在 GitHub 上创建 PR
```

- Python 3.11+，类型提示
- SQLAlchemy 2.0 async 风格
- PydanticAI 1.89.1
- 中文 commit message

---

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `DASHSCOPE_API_KEY` | ✅ | DashScope API Key（也可在设置页填写） |
| `DATABASE_URL` | ❌ | 默认 `~/.careeros/career_os.db` |
| `DEBUG` | ❌ | `true`（开发）/ `false`（Docker 生产） |

完整配置见 `.env.example`。

---

## License

[MIT](LICENSE)
