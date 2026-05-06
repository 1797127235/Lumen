# CareerOS 项目概览

**项目名称**: CareerOS（码路领航）
**项目类型**: AI 职业规划助手
**目标用户**: 中国 CS 在校生（大一到毕业）
**仓库类型**: Monorepo（前后端分离）

---

## 执行摘要

CareerOS 是一个自托管的 AI 职业规划助手，帮助计算机专业学生从大一到毕业的职业发展。核心特点是数据本地化、长期记忆、个性化建议。

---

## 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 后端框架 | FastAPI | ≥0.111.0 |
| ORM | SQLAlchemy (async) | ≥2.0.0 |
| 数据库 | SQLite + aiosqlite | ≥0.20.0 |
| AI Agent | PydanticAI | 1.89.1 |
| LLM 路由 | LiteLLM | ≥1.30.0 |
| 记忆层 | Cognee + Kuzu + LanceDB | 1.0.5 / 0.11.3 / 0.30.2 |
| 前端框架 | React | 19.2.5 |
| 构建工具 | Vite | 8.0.10 |
| 样式 | Tailwind CSS | 4.2.4 |
| 语言 | Python 3.11+ / TypeScript 6.0 |
| 部署 | Docker Compose | — |

---

## 架构类型

**模式**: 分层架构 + 事件驱动记忆系统

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React)                      │
│   Chat / Profile / Memories / Settings                  │
└─────────────────────────────────────────────────────────┘
                           │
                     REST + SSE
                           │
┌─────────────────────────────────────────────────────────┐
│                    Backend (FastAPI)                      │
│   Routers → Services → Agent (PydanticAI)               │
└─────────────────────────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
    ┌────▼────┐      ┌─────▼─────┐    ┌──────▼──────┐
    │ SQLite  │      │  .md 文件  │    │   Cognee    │
    │ (真相源) │      │ (投影)     │    │  (投影)     │
    └─────────┘      └───────────┘    └─────────────┘
```

---

## 核心功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 智能对话 | ✅ | SSE 流式输出，Agent ReAct Loop，工具调用 |
| 记忆系统 | ✅ | 事件驱动（growth_events → .md 投影），后台审查兜底 |
| 用户画像 | ✅ | 上传简历 → LLM 自动提取 → Agent 工具手动修正 |
| 技能记录 | ✅ | 表单管理 + 对话中识别 |
| 长期记忆 | ✅ | FTS5 全文搜索（支持中文 trigram） |

---

## 仓库结构

```
career-os/
├── app/
│   ├── backend/          # Python 后端
│   │   ├── agent/        # AI Agent 实现
│   │   ├── db/           # 数据库配置
│   │   ├── models/       # SQLAlchemy 模型
│   │   ├── routers/      # API 路由
│   │   ├── schemas/      # Pydantic 模型
│   │   └── services/     # 业务逻辑
│   └── frontend/         # React 前端
│       └── src/
│           ├── pages/    # 页面组件
│           └── lib/      # 工具函数
├── docs/                 # 项目文档
├── tests/                # 测试用例
├── Dockerfile            # Docker 构建
└── docker-compose.yml    # Docker Compose
```

---

## 快速开始

### Docker 部署（推荐）

```bash
git clone https://github.com/1797127235/CareerOS.git
cd CareerOS
docker compose up -d
```

打开 `http://localhost:3000`

### 本地开发

```bash
# 后端
pip install -r requirements.txt

# 前端
cd app/frontend && npm install && cd ../..

# 启动
# Windows: 双击 run.bat
# PowerShell: .\run.ps1
```

打开 `http://localhost:5173`

---

## 文档导航

- [系统架构](./architecture.md) - 详细架构设计
- [源码结构](./source-tree-analysis.md) - 目录结构说明
- [API 契约](./api-contracts.md) - API 端点文档
- [数据模型](./data-models.md) - 数据库模型文档
- [开发指南](./development-guide.md) - 开发环境搭建
