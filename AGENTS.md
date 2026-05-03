# AGENTS.md

## What This Is

CareerOS — 面向中国 CS 学生的自托管 AI 职业规划助手。FastAPI + SQLAlchemy + DashScope (qwen-plus/qwen-max) + LlamaIndex + Chroma + SQLite。前端 React + Vite + Tailwind + shadcn/ui。

## How to Run

```bash
# 方式一：Docker（推荐）
docker compose up -d
# 打开 http://localhost:3000

# 方式二：本地开发
pip install -r requirements.txt
cd frontend && npm install && cd ..
# Windows: 双击 run.bat
# PowerShell: .\run.ps1
# 打开 http://localhost:5173
```

启动后自动建表（SQLite），无需手动迁移。首次启动自动构建用户记忆索引。

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
│   │   ├── job_target.py     # JobTarget 岗位追踪卡片
│   │   └── document.py       # Document + Chunk 知识库文档/文本块
│   ├── agent/
│   │   ├── llm_router.py    # LLM 路由（qwen-plus/qwen-max），流式+非流式，embed 向量化
│   │   ├── orchestrator.py  # Agent 编排 + Skill 系统（7 个 Skill 按需加载）
│   │   ├── rag.py           # RAG 检索（LlamaIndex + Chroma + DashScope embedding）
│   │   └── tools.py         # 工具注册中心
│   ├── routers/
│   │   ├── health.py        # GET /api/health
│   │   ├── chat.py          # POST /api/chat (SSE), GET /api/chat/history, GET /api/chat/{id}
│   │   ├── profile.py       # POST /api/profile/resume, GET/PATCH/DELETE /api/profile/me
│   │   └── jd.py            # POST /api/jd/diagnose
│   ├── schemas/
│   │   ├── profile.py       # ProfileResponse, ProfileUpdate, SkillItem（含 context）
│   │   └── jd.py            # JDDiagnoseRequest, JDDiagnoseResponse, GapSkill
│   ├── services/
│   │   ├── chat_service.py  # 对话业务逻辑：上下文加载 → 意图分类 → 流式生成 → 存 DB
│   │   ├── profile_service.py  # 简历提取 + LLM 解析 + DB 写入 + 重置
│   │   ├── jd_service.py    # JD 诊断：画像 + JD → LLM → 匹配评分 + 缺口 + 建议
│   │   ├── target_service.py  # 岗位追踪卡片 CRUD + AI 建议
│   │   └── document_service.py  # 知识库文档/块 DB 操作（审计）
│   └── agent/skills/        # 7 个 SKILL.md 目录（按意图目录名匹配）
├── app/frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Chat.tsx     # SSE 流式对话 + 历史抽屉 + 空态示例
│   │   │   ├── Profile.tsx  # 画像页：教育详情/技能/获奖 + inline 编辑 + 重置
│   │   │   └── JD.tsx       # JD 诊断页
│   │   └── lib/api.ts       # 后端 API 调用 + SSE 解析
│   └── vite.config.ts       # Vite + Tailwind + proxy → 8001
├── tests/                   # pytest 测试用例
├── docs/                    # 设计文档
│   ├── 功能设计/            # 各功能详细设计
│   ├── 架构/                # 系统架构
│   └── 需求/                # 需求文档
├── .github/workflows/       # CI/CD（lint + test + build）
├── Dockerfile               # 多阶段构建（node + python）
├── docker-compose.yml       # 单服务 + 持久化 volume
├── pyproject.toml           # ruff + pytest 配置
└── run.ps1                  # 一键启动后端 + 前端
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/health` | 健康检查 |
| `POST` | `/api/chat` | SSE 流式对话，body: `{ message, conversation_id?, user_id? }` |
| `GET`  | `/api/chat/history?user_id=&limit=` | 对话历史列表 |
| `GET` | `/api/chat/{conversation_id}` | 单条会话消息详情 |
| `POST` | `/api/profile/resume?user_id=` | 上传简历（PDF/DOCX/TXT），LLM 自动提取画像 |
| `GET` | `/api/profile/me?user_id=` | 获取当前用户画像 |
| `PATCH` | `/api/profile/me?user_id=` | 局部更新用户画像（null 可清空字段） |
| `DELETE` | `/api/profile/me?user_id=` | 重置用户画像（保留 nickname） |
| `POST` | `/api/jd/diagnose?user_id=` | JD 诊断：LLM 对比画像输出匹配评分+缺口+建议 |
| `GET`  | `/api/targets?user_id=` | 获取当前用户岗位看板 |
| `POST` | `/api/targets?user_id=` | 新建岗位目标卡片 |
| `PATCH` | `/api/targets/{target_id}?user_id=` | 更新卡片（含阶段流转） |
| `DELETE` | `/api/targets/{target_id}?user_id=` | 删除卡片 |
| `GET`  | `/api/config` | 获取当前用户配置 |
| `POST` | `/api/config` | 更新用户配置（API Key 等） |

## Key Architecture Decisions

- **Agent 编排**：自发现 Skill 系统 + 纯函数编排。`discover_skills()` 扫描 `skills/` 目录自动生成意图分类 prompt 和映射表，`run_orchestrator()` 统一返回 (intent, task_type, system_prompt)。加新 Skill 只需建目录放 `SKILL.md`，零代码改动
- **Skill 系统**：7 个 Skill 按需加载，`_load_skill_body()` 懒加载 SKILL.md，节省 token
- **LLM 路由硬编码**：`llm_router.py` 的 `_ROUTE_MAP` 按任务类型选模型，不要设 `LLM_MODEL` 环境变量（注释里有说明）
- **数据库**：开发阶段 SQLite（`career_os.db`），生产切 PostgreSQL — 切换方式：改 `.env` 中 `DATABASE_URL`
- **记忆层**：LlamaIndex + Chroma 向量检索。`ingest_user_memory()` 从用户个人数据（画像+对话+技能+项目）构建向量索引，`search()` 返回 top_k 语义匹配块。SQL 层存元数据，与 Chroma 解耦
- **ORM 建表**：`lifespan` 中 `Base.metadata.create_all`，无需 Alembic 迁移
- **画像数据模型**：扩展字段（GPA/排名/获奖/技能场景）存入 `profile_data` JSON 列，双写方案，零 ORM 列新增
- **会话上下文**：加载最近 20 条消息做上下文窗口，无滑动窗口或摘要（后续迭代）

## .env

根目录有 `.env` 文件，包含 DashScope API Key、Firecrawl、ResumeSDK、讯飞等。**不要提交到 git**（当前无 .gitignore，需要补上）。

关键配置：
- `DASHSCOPE_API_KEY` — LLM 调用
- `EMBEDDING_MODEL=text-embedding-v4` — 向量化
- `FRONTEND_URL=http://localhost:5173` — CORS 白名单

## Code Style

- Python 3.11+，类型提示（`from __future__ import annotations`）
- SQLAlchemy 2.0 async（`Mapped[...]`, `mapped_column()`）
- Pydantic v2（`BaseSettings`, `BaseModel`）
- 前端 React + TypeScript + Tailwind CSS + shadcn/ui
- ruff 做 lint + format（`pyproject.toml` 配置）
- pytest + pytest-asyncio（14 条测试，`pytest` 运行）

## Gotchas

- `chat.py` 路由函数已重命名为 `send_message`，避免模块名遮蔽（2024-05 已修复）
- `chat_service.py` 流式对话使用 `db.commit()` 而非 `flush()`，确保用户消息立即落库，流中断不丢失
- `update_profile` 中 `if value is not None` 已移除，`null` 现在可以清空字段（通过 `model_fields_set` 区分"未传"和"传 null"）
- `current_skills` ORM 类型标注为 `dict` 但实际存 `list`，Pyright 会报错，不影响运行

## Known Limitations

- **无认证**：`user_id` 由客户端 localStorage 控制，无 JWT 鉴权。生产环境需加认证
- **会话劫持**：POST /api/chat 只按 conversation_id 取会话，未校验归属。生产需加会话所有权验证
- **单用户模式**：demo_user 硬编码，多用户需改造

## docs/

设计文档在 `docs/` 下，已拆分为模块化文件：
- `docs/需求/` — 用户画像 + 功能需求清单
- `docs/架构/` — 系统架构、AI Agent、技术栈、安全合规
- `docs/功能设计/` — 模块总览、各核心功能详细设计
- `docs/dev-guide.md` — 软件工程实践指南（以 career-os 为例）
- `docs/frontend-design.md` — 前端设计文档（字体/配色/线框/文案语气）
