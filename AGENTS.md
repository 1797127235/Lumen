# AGENTS.md

## What This Is

CareerOS — 面向中国 CS 学生的自托管 AI 职业规划助手。FastAPI + SQLAlchemy + Mem0 + DashScope (qwen-plus/qwen-max) + LiteLLM + SQLite。前端 React 19 + Vite + Tailwind CSS 4。

## How to Run

```bash
# 方式一：Docker（推荐）
docker compose up -d
# 打开 http://localhost:3000

# 方式二：本地开发
pip install -r requirements.txt
cd app/frontend && npm install && cd ../..
# Windows: 双击 run.bat
# PowerShell: .\run.ps1
# 打开 http://localhost:5173
```

启动后自动建表（SQLite），无需手动迁移。首次启动自动初始化 Mem0 记忆层。

## Project Structure

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
│   │   ├── health.py         # GET /api/health
│   │   ├── chat.py           # POST /api/chat (SSE), GET /api/chat/history, DELETE /api/chat/{id}
│   │   ├── profile.py        # POST /api/profile/resume, GET/PATCH/DELETE /api/profile/me
│   │   ├── jd.py             # POST /api/jd/diagnose
│   │   ├── memory.py         # GET /api/memory/stats, /api/memory/list, POST /api/memory/reset
│   │   ├── skills.py         # GET/POST/PATCH/DELETE /api/skills
│   │   └── config_router.py  # GET/POST /api/config
│   ├── schemas/
│   │   ├── profile.py        # ProfileResponse, ProfileUpdate, SkillItem（含 context）
│   │   └── jd.py             # JDDiagnoseRequest, JDDiagnoseResponse, GapSkill
│   └── services/
│       ├── chat_service.py   # 对话业务：Agent Loop 集成 + SSE 流式输出
│       ├── profile_service.py # 简历提取 + LLM 解析 + DB 写入
│       ├── jd_service.py     # JD 诊断：画像 + JD → LLM → 匹配评分 + 缺口 + 建议
│       └── skill_service.py  # 技能记录 CRUD
├── app/frontend/
│   └── src/
│       ├── pages/
│       │   ├── Chat.tsx      # SSE 流式对话 + 历史抽屉 + 空态示例
│       │   ├── Profile.tsx   # 画像页：教育详情/技能/获奖 + inline 编辑
│       │   ├── Memories.tsx  # 记忆管理页
│       │   └── Settings.tsx  # 设置页：Provider 选择 + API Key
│       └── lib/
│           ├── api.ts        # 后端 API 调用 + SSE 解析
│           ├── chatSession.tsx # 全局聊天状态管理（Context Provider）
│           └── userId.ts     # 用户 ID 管理
├── tests/                    # pytest 测试用例
├── docs/                     # 设计文档
│   ├── 需求/                 # 用户画像 + 功能需求清单
│   ├── 架构/                 # 系统架构、安全合规
│   └── 功能设计/             # 各核心功能详细设计
├── .github/workflows/        # CI/CD（lint + test + build）
├── Dockerfile                # 多阶段构建（node + python）
├── docker-compose.yml        # 单服务 + 持久化 volume
├── pyproject.toml            # ruff + pytest 配置
└── run.ps1 / run.bat         # 本地开发启动脚本
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/health` | 健康检查 |
| `POST` | `/api/chat` | SSE 流式对话（Agent Loop），body: `{ message, conversation_id?, user_id? }` |
| `GET`  | `/api/chat/history?user_id=&limit=` | 对话历史列表 |
| `GET`  | `/api/chat/{conversation_id}` | 单条会话消息详情 |
| `DELETE` | `/api/chat/{conversation_id}` | 删除对话及其消息 |
| `POST` | `/api/profile/resume?user_id=` | 上传简历（PDF/DOCX/TXT），LLM 自动提取画像 |
| `GET`  | `/api/profile/me?user_id=` | 获取当前用户画像 |
| `PATCH` | `/api/profile/me?user_id=` | 局部更新用户画像（null 可清空字段） |
| `DELETE` | `/api/profile/me?user_id=` | 重置用户画像（保留 nickname） |
| `POST` | `/api/jd/diagnose?user_id=` | JD 诊断：LLM 对比画像输出匹配评分+缺口+建议 |
| `GET`  | `/api/memory/stats?user_id=` | 记忆统计（状态、数量） |
| `GET`  | `/api/memory/list?user_id=` | 记忆列表 |
| `POST` | `/api/memory/reset?user_id=` | 重置记忆 |
| `GET`  | `/api/skills?user_id=` | 获取用户所有技能记录 |
| `POST` | `/api/skills?user_id=` | 创建技能记录 |
| `PATCH` | `/api/skills/{skill_id}?user_id=` | 更新技能记录 |
| `DELETE` | `/api/skills/{skill_id}?user_id=` | 删除技能记录 |
| `GET`  | `/api/config` | 获取当前用户配置 |
| `POST` | `/api/config` | 更新用户配置（API Key 等） |

## Key Architecture Decisions

- **Agent 系统**：PydanticAI 实现（`pydantic_agent.py`），支持工具调用、动态系统提示词、流式输出
- **工具注册**：`@agent.tool` 装饰器注册工具，2 个内置工具：`get_profile`、`update_profile`（JD 结构化诊断走 `POST /api/jd/diagnose`，非 Agent 工具）
- **LLM 路由**：`llm_router.py` 支持多 Provider（DashScope/OpenAI/DeepSeek 等），通过 LiteLLM 统一调用
- **记忆层**：Mem0 + Chroma，自动从对话中提取关键信息，语义检索注入上下文
- **数据库**：SQLite（`career_os.db`），`lifespan` 中 `Base.metadata.create_all` 自动建表
- **画像数据模型**：扩展字段存入 `profile_data` JSON 列，零 ORM 列新增
- **聊天状态**：`chatSession.tsx` 全局 Context Provider，跨页面保持对话状态
- **可观测性**：`agent_traces` 表记录 Agent 推理步骤、工具调用、耗时

## .env

根目录有 `.env` 文件，包含 DashScope API Key 等。**不要提交到 git**。

关键配置：
- `DASHSCOPE_API_KEY` — LLM 调用
- `DATABASE_URL` — 默认 `~/.careeros/career_os.db`
- `DEBUG` — `true`（开发）/ `false`（生产）

## Code Style

- Python 3.11+，类型提示（`from __future__ import annotations`）
- SQLAlchemy 2.0 async（`Mapped[...]`, `mapped_column()`）
- Pydantic v2（`BaseSettings`, `BaseModel`）
- 前端 React 19 + TypeScript + Tailwind CSS 4
- ruff 做 lint + format（`pyproject.toml` 配置）
- pytest + pytest-asyncio（16 条测试，`pytest` 运行）

## Gotchas

- `chat_service.py` 流式对话使用 `db.commit()` 而非 `flush()`，确保用户消息立即落库，流中断不丢失
- `update_profile` 中 `null` 可以清空字段（通过 `model_fields_set` 区分"未传"和"传 null"）
- `chatSession.tsx` 使用 `sessionStorage` 持久化 conversationId，刷新页面不丢失对话

## Known Limitations

- **无认证**：`user_id` 由客户端 localStorage 控制，无 JWT 鉴权。生产环境需加认证
- **单用户模式**：demo_user 硬编码，多用户需改造

## docs/

设计文档在 `docs/` 下：
- `docs/需求/` — 用户画像 + 功能需求清单
- `docs/架构/` — 系统架构、安全合规
- `docs/功能设计/` — 各核心功能详细设计
- `docs/frontend-design.md` — 前端设计文档
