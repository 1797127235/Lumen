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
| 记忆系统 | ✅ | growth_events → .md + Cognee 投影，越用越懂你 |
| 用户画像 | ✅ | 上传简历 → LLM 自动提取 → 手动修正 |
| 技能记录 | ✅ | 表单管理 + 对话中识别 |
| 长期记忆 | ✅ | 事件溯源 + 语义检索 + 自动去重 |

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
│   │   ├── pydantic_tools.py  # Agent 工具（memory_search/update/add）
│   │   ├── llm_router.py     # LLM 路由（多 Provider），流式+非流式
│   │   └── deps.py           # Agent 依赖注入（CareerOSDeps）
│   ├── routers/
│   │   ├── chat.py           # POST /api/chat (SSE), GET /api/chat/history
│   │   ├── profile.py        # GET/PATCH/DELETE /api/profile/me
│   │   ├── memory.py         # /api/memory/stats/list/reset/rebuild/search
│   │   ├── skills.py         # CRUD /api/skills
│   │   └── config_router.py  # GET/POST /api/config
│   ├── schemas/
│   │   └── profile.py        # ProfileResponse, ProfileUpdate
│   └── services/
│       ├── chat_service.py   # 对话业务：Agent Loop 集成 + SSE 流式
│       ├── profile_service.py # 简历提取 + LLM 解析
│       ├── memory_service.py # .md 文件记忆层
│       ├── growth_event_service.py # GrowthEvent CRUD + 去重
│       ├── md_projector.py   # SQLite → .md 投影器
│       ├── cognee_service.py # Cognee 语义检索
│       └── memory_extractor.py # 对话后记忆提取
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
Agent 工具 / 对话提取 / 简历上传
    ↓
growth_events (SQLite 真相源)
    ↓
┌─────────┴─────────┐
↓                   ↓
.md 投影器      Cognee 投影器
↓                   ↓
memory.md         Cognee 索引
entities/*.md     (Kuzu + LanceDB)
```

**三层记忆模型**:

| 层级 | 存储 | 用途 |
|------|------|------|
| L1 | conversation messages | 短期上下文（最近 20 条） |
| L2 | .md 文件 | 结构化画像（system prompt 注入） |
| L3 | Cognee | 语义检索（按相关性召回） |

所有数据存储在 `~/.careeros/`：
```
~/.careeros/
├── career_os.db      # SQLite（对话、事件、技能等）
├── memory/           # .md 记忆文件
│   ├── memory.md     # 核心记忆
│   └── entities/     # 实体记忆
├── cognee_data/      # Cognee 索引
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
| 记忆层 | growth_events → .md + Cognee | 事件溯源 + 投影架构 |
| 前端 | React 19 + Vite + Tailwind CSS 4 | OKLCH 配色，响应式 |
| 部署 | Docker Compose | 单容器，单端口，持久化 volume |

---

## Agent 系统

CareerOS 实现了完整的 Agent ReAct Loop：

```
用户消息
  ↓
PydanticAI Agent (ReAct Loop)
  ├─ 动态 system prompt（注入记忆 + 历史）
  ├─ 工具调用（memory_search / memory_update / memory_add）
  └─ 流式输出（SSE）
  ↓
后台记忆提取（fire-and-forget）
  ↓
growth_events（事件入库）
```

**工具列表**：
- `memory_search` — 搜索记忆（支持实体类型过滤）
- `memory_update` — 更新记忆（写入 growth_events → .md 投影）
- `memory_add` — 添加记忆（写入 growth_events → .md 投影）

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
| `GET`  | `/api/memory/stats` | 记忆统计 |
| `GET`  | `/api/memory/list` | 记忆列表 |
| `POST` | `/api/memory/reset` | 重置记忆（SQLite + .md + Cognee） |
| `POST` | `/api/memory/rebuild` | 重建记忆（从 SQLite 重建 .md + Cognee） |
| `GET`  | `/api/memory/search` | 搜索记忆（SQLite + .md） |
| `GET/POST/PATCH/DELETE` | `/api/skills` | 技能记录 CRUD |
| `GET/POST` | `/api/config` | 用户配置 |

---

## 设计理念

- **自托管优先**：数据在本地，不依赖外部服务，用户自己掌控
- **事件驱动**：所有写入走 growth_events，通过投影器同步到 .md 和 Cognee
- **记忆驱动**：自动从对话中提取关键信息，语义检索注入上下文
- **表单为主、对话为辅**：基础数据表单填写，AI 在对话中识别新信息并确认后入库
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
