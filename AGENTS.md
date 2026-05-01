# AGENTS.md

## What This Is

CodePilot · 码路领航 — AI职业规划智能体（后端）。FastAPI + SQLAlchemy + DashScope (qwen-plus/qwen-max) + SQLite。

## How to Run

```bash
# 安装依赖
pip install -r requirements.txt

# 启动后端（含热重载）
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后自动建表（SQLite `career_os.db`），无需手动迁移。

## Project Structure

```
app/
├── backend/
│   ├── main.py              # FastAPI 入口，lifespan 中自动 create_all
│   ├── config.py            # pydantic-settings，从根目录 .env 加载
│   ├── db/
│   │   ├── base.py          # SQLAlchemy AsyncEngine + Base 声明
│   │   └── session.py       # get_db 依赖注入（yield + rollback）
│   ├── models/              # ORM：User, UserProfile, Conversation, Message
│   │   ├── user.py           # User + UserProfile
│   │   └── conversation.py   # Conversation + Message
  │   ├── agent/
│   │   ├── llm_router.py    # LLM 路由（qwen-plus/qwen-max），流式+非流式
│   │   ├── orchestrator.py  # Agent 编排（LangGraph StateGraph：意图分类 → 路由 → Agent节点）
│   │   ├── rag.py           # 简化版 RAG（MVP，无 Milvus）
│   │   └── tools.py         # 工具注册中心
│   ├── routers/
│   │   ├── health.py        # GET /api/health
│   │   ├── chat.py          # POST /api/chat (SSE), GET /api/chat/history, GET /api/chat/{id}
│   │   ├── profile.py       # POST /api/profile/resume, GET /api/profile/me, PATCH /api/profile/me
│   │   └── jd.py            # POST /api/jd/diagnose
│   ├── schemas/
│   │   ├── profile.py       # ProfileResponse, ProfileUpdate, SkillItem 等 Pydantic 模型
│   │   └── jd.py            # JDDiagnoseRequest, JDDiagnoseResponse, GapSkill
│   ├── services/
│   │   ├── chat_service.py  # 对话业务逻辑：上下文加载 → 意图分类 → 流式生成 → 存 DB
│   │   ├── profile_service.py  # 简历文本提取 + LLM 画像解析 + DB 写入
│   │   └── jd_service.py    # JD 诊断：画像 + JD → LLM → 匹配评分 + 缺口 + 建议
│   └── utils/
├── frontend/                # 预留，未实现
docs/                        # 产品设计文档（需求/竞品/架构/功能设计）
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
| `PATCH` | `/api/profile/me?user_id=` | 局部更新用户画像 |
| `POST` | `/api/jd/diagnose?user_id=` | JD 诊断：上传岗位描述，LLM 对比画像输出匹配评分+缺口+建议 |

## Key Architecture Decisions

- **Agent 编排**：LangGraph StateGraph 实现意图分类 → `classify()` 返回 (intent, task_type)，流式生成绕开图直接走 `chat_stream`
- **意图分类**：`orchestrator.py` 的 `classify_intent_node` 用 LangGraph + LLM 分类，返回后可路由到对应 task_type
- **系统提示词**：`orchestrator.py` 的 `build_system_prompt(user_profile, intent)` 统一组装
- **LLM 路由硬编码**：`llm_router.py` 的 `_ROUTE_MAP` 按任务类型选模型，不要设 `LLM_MODEL` 环境变量（注释里有说明）
- **数据库**：开发阶段 SQLite（`career_os.db`），生产切 PostgreSQL — 切换方式：改 `.env` 中 `DATABASE_URL`
- **ORM 建表**：`lifespan` 中 `Base.metadata.create_all`，无需 Alembic 迁移（MVP 阶段）
- **会话上下文**：加载最近 20 条消息做上下文窗口，无滑动窗口或摘要（后续迭代）
- **Agent 人设**：系统提示词写在 `orchestrator.py` 的 `build_system_prompt()`

## .env

根目录有 `.env` 文件，包含 DashScope API Key、Firecrawl、ResumeSDK、讯飞等。**不要提交到 git**（当前无 .gitignore，需要补上）。

关键配置：
- `DASHSCOPE_API_KEY` — LLM 调用
- `EMBEDDING_MODEL=text-embedding-v4` — 向量化
- `PDF_EXPORT_ENGINE=playwright` — 简历 PDF 导出
- `FRONTEND_URL=http://localhost:5173` — CORS 白名单

## Code Style

- Python 3.11+，类型提示（`from __future__ import annotations`）
- SQLAlchemy 2.0 async（`Mapped[...]`, `mapped_column()`）
- Pydantic v2（`BaseSettings`, `BaseModel`）
- 无测试框架（MVP 阶段）
- 无 linter/formatter 配置

## Gotchas

- `chat.py` 路由函数已重命名为 `send_message`，避免模块名遮蔽（2024-05 已修复）
- `db/session.py` 的 `get_db` 在异常时 rollback，但 commit 在 yield 后 — 如果 Agent 流式返回中途断开，已写入的 Message 可能丢失

## docs/

设计文档在 `docs/` 下，已拆分为模块化文件：
- `docs/需求/` — 用户画像 + 功能需求清单
- `docs/架构/` — 系统架构、AI Agent、技术栈、安全合规
- `docs/功能设计/` — 模块总览、各核心功能详细设计
- `docs/竞品分析报告.md` / `docs/总体规划.md` / `docs/系统架构设计.md` — 原始文档（保留）
