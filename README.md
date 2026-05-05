# CareerOS

<p align="center">
  <b>面向中国 CS 学生的自托管 AI 职业规划助手</b><br>
  <i>从大一陪伴到毕业，越用越懂你</i>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#功能特性">功能</a> ·
  <a href="#项目结构">结构</a> ·
  <a href="#参与贡献">贡献</a>
</p>

---

## 这是什么

一个帮计算机专业学生做职业规划的 AI Agent。不是"拿了 JD 帮我匹简历"，而是"我不知道该学什么、该投什么"。

**目标用户**：普通本科/211 的 CS 在校生，大一迷茫或大三找实习碰壁。

**核心价值**：数据在本地、自己部署、用四年——不是一次性工具，是一个越用越懂你的系统。

**技术栈**：FastAPI + SQLAlchemy + Mem0 + LiteLLM（多 Provider 路由）+ React + Vite + Tailwind

---

## 功能特性

| 功能 | 状态 | 说明 |
|------|------|------|
| 智能对话 | ✅ | SSE 流式输出，Agent ReAct Loop，工具调用 |
| 记忆系统 | ✅ | Mem0 自动提取 + 语义检索，越用越懂你 |
| 用户画像 | ✅ | 上传简历 → LLM 自动提取 → 手动修正 |
| JD 诊断 | ✅ | 画像 vs 岗位要求 → 匹配评分 + 缺口分析 |
| 技能记录 | ✅ | 表单管理 + 对话中识别 |

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
│   │   ├── jd_diagnosis.py   # JDDiagnosis
│   │   ├── skill_record.py   # SkillRecord 技能成长记录
│   │   └── agent_trace.py    # AgentTrace 可观测性
│   ├── agent/
│   │   ├── pydantic_agent.py  # PydanticAI Agent 定义 + 动态系统提示词
│   │   ├── pydantic_tools.py  # Agent 工具（get_profile, update_profile）
│   │   ├── llm_router.py     # LLM 路由（多 Provider），流式+非流式
│   │   ├── mem0_client.py    # Mem0 记忆层封装
│   │   └── deps.py           # Agent 依赖注入（CareerOSDeps）
│   ├── routers/
│   │   ├── chat.py           # POST /api/chat (SSE), GET /api/chat/history
│   │   ├── profile.py        # GET/PATCH/DELETE /api/profile/me
│   │   ├── jd.py             # POST /api/jd/diagnose
│   │   ├── memory.py         # GET /api/memory/stats, /api/memory/list
│   │   ├── skills.py         # CRUD /api/skills
│   │   └── config_router.py  # GET/POST /api/config
│   ├── schemas/
│   │   ├── profile.py        # ProfileResponse, ProfileUpdate
│   │   └── jd.py             # JDDiagnoseRequest, JDDiagnoseResponse
│   └── services/
│       ├── chat_service.py   # 对话业务：Agent Loop 集成 + SSE 流式
│       ├── profile_service.py # 简历提取 + LLM 解析
│       ├── jd_service.py     # JD 诊断
│       └── skill_service.py  # 技能记录 CRUD
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
├── docs/                     # 设计文档
├── Dockerfile                # 多阶段构建（node + python）
├── docker-compose.yml        # 单服务 + 持久化 volume
├── pyproject.toml            # ruff + pytest 配置
└── run.ps1 / run.bat         # 本地开发启动脚本
```

---

## 数据架构

CareerOS 的"知识库"不是预置的静态数据，而是**用户自己的个人数据**：

```
用户画像（我是谁）     → 学校、年级、目标方向、技能
对话历史（我聊过什么） → 和 AI 的所有对话记录
技能记录（我会什么）   → 从对话中识别，用户确认后入库
JD/岗位（我投过什么） → JD 诊断结果
```

这些数据通过 Mem0 自动提取关键信息并语义检索，AI 在对话时自动注入相关上下文——越用越懂你。

所有数据存储在 `~/.careeros/`：
```
~/.careeros/
├── career_os.db      # SQLite（对话、画像、技能等）
├── chroma_db/        # Mem0 向量存储
└── config.json       # 用户设置（API Key 等）
```

---

## 技术选型

| 层级 | 选型 | 说明 |
|------|------|------|
| 后端 | FastAPI + SQLAlchemy 2.0 | async，类型安全 |
| 数据库 | SQLite（单文件） | 自托管首选，零运维 |
| LLM | LiteLLM（DashScope / OpenAI / DeepSeek / Anthropic / Gemini / Ollama / OpenRouter）| 多 Provider 统一路由，用户自选 |
| Agent | ReAct Loop + ToolRegistry | 流式推理，工具调用，可观测性 |
| 记忆层 | Mem0 + Chroma | 自动提取 + 语义检索 |
| 前端 | React 19 + Vite + Tailwind CSS 4 | OKLCH 配色，响应式 |
| 部署 | Docker Compose | 单容器，单端口，持久化 volume |

---

## Agent 系统

CareerOS 实现了完整的 Agent ReAct Loop：

```
用户消息
  ↓
意图分类（orchestrator）
  ↓
ReAct Loop（最多 5 步）
  ├─ 思考：分析下一步
  ├─ 行动：调用工具（get_profile / update_profile）
  ├─ 观察：获取工具结果
  └─ 循环直到得出最终答案
  ↓
流式输出（SSE）
  ↓
Mem0 后台提取记忆
```

**工具列表**：
- `get_profile` — 获取用户画像
- `update_profile` — 更新画像（含方向值校验）
- `POST /api/jd/diagnose` — JD 诊断（匹配评分 + 缺口分析，HTTP 接口，非对话工具）

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
| `POST` | `/api/jd/diagnose` | JD 诊断 |
| `GET`  | `/api/memory/stats` | 记忆统计 |
| `GET`  | `/api/memory/list` | 记忆列表 |
| `POST` | `/api/memory/reset` | 重置记忆 |
| `GET/POST/PATCH/DELETE` | `/api/skills` | 技能记录 CRUD |
| `GET/POST` | `/api/config` | 用户配置 |

---

## 设计理念

- **自托管优先**：数据在本地，不依赖外部服务，用户自己掌控
- **记忆驱动**：Mem0 自动从对话中提取关键信息，语义检索注入上下文
- **表单为主、对话为辅**：基础数据表单填写，AI 在对话中识别新信息并确认后入库
- **Agent 而非问答**：ReAct Loop + 工具调用，真正的 Agent 系统

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
- Pydantic v2
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
