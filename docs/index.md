# CareerOS 项目文档

**项目名称**: CareerOS（码路领航）
**项目类型**: AI 职业规划助手
**最后更新**: 2026-05-06

---

## 项目概览

**目标用户**: 中国 CS 在校生（大一到毕业）
**核心价值**: 数据本地化、长期记忆、个性化建议
**技术栈**: FastAPI + SQLAlchemy + PydanticAI + React + Vite + Tailwind

---

## 快速参考

| 项目 | 值 |
|------|-----|
| 主要语言 | Python 3.11+ / TypeScript 6.0 |
| 后端框架 | FastAPI |
| 前端框架 | React 19 |
| 数据库 | SQLite |
| AI Agent | PydanticAI |
| 部署方式 | Docker Compose |

---

## 生成文档

### 核心文档

- [项目概览](./project-overview.md) — 项目简介和技术栈
- [系统架构](./architecture.md) — 架构设计和核心决策
- [源码结构](./source-tree-analysis.md) — 目录结构和文件说明
- [API 契约](./api-contracts.md) — API 端点文档
- [数据模型](./data-models.md) — 数据库模型文档
- [开发指南](./development-guide.md) — 开发环境搭建

### 需求文档

- [用户画像与场景](./需求/用户画像与场景.md) — 目标用户分析
- [功能需求清单](./需求/功能需求清单.md) — 功能列表

### 架构文档

- [系统整体架构](./架构/系统整体架构.md) — 详细架构设计
- [安全合规与运维部署](./架构/安全合规与运维部署.md) — 安全和部署

### 功能设计

- [智能对话咨询](./功能设计/智能对话咨询.md) — 对话功能设计
- [简历优化](./功能设计/简历优化.md) — 简历功能设计
- [学习路径与能力分析](./功能设计/学习路径与能力分析.md) — 学习功能设计
- [交互与数据模型](./功能设计/交互与数据模型.md) — 交互设计

### 记忆系统

- [记忆系统产品与架构计划](./记忆系统产品与架构计划.md) — 记忆系统规划
- [记忆层架构变更](./记忆层架构变更.md) — 架构变更记录
- [记忆架构方案分析](./记忆架构方案分析.md) — 方案对比分析
- [长期记忆模块实现规划](./长期记忆模块实现规划.md) — 实现细节

### 前端设计

- [前端设计文档](./frontend-design.md) — 前端架构设计

---

## 已有文档

- [README](../README.md) — 项目说明
- [AGENTS.md](../AGENTS.md) — AI Agent 配置
- [CLAUDE.md](../CLAUDE.md) — Claude Code 配置

---

## 开发入口

### 本地开发

```bash
# 后端
pip install -r requirements.txt
uvicorn app.backend.main:app --reload

# 前端
cd app/frontend && npm install && npm run dev
```

### Docker 部署

```bash
docker compose up -d
```

### 运行测试

```bash
pytest
```

---

## 技术栈详情

### 后端

| 依赖 | 版本 | 用途 |
|------|------|------|
| FastAPI | ≥0.111.0 | Web 框架 |
| SQLAlchemy | ≥2.0.0 | ORM |
| PydanticAI | 1.89.1 | AI Agent |
| LiteLLM | ≥1.30.0 | LLM 路由 |
| Cognee | 1.0.5 | 可选，当前未接入 |

### 前端

| 依赖 | 版本 | 用途 |
|------|------|------|
| React | 19.2.5 | UI 框架 |
| Vite | 8.0.10 | 构建工具 |
| Tailwind CSS | 4.2.4 | 样式框架 |
| React Router | 7.14.2 | 路由 |

### 开发工具

| 工具 | 用途 |
|------|------|
| ruff | Python lint + format |
| pytest | Python 测试 |
| ESLint | TypeScript lint |
| Docker | 容器化部署 |
| GitHub Actions | CI/CD |

---

## 项目结构

```
career-os/
├── app/
│   ├── backend/          # Python 后端
│   │   ├── agent/        # AI Agent
│   │   ├── db/           # 数据库
│   │   ├── models/       # 数据模型
│   │   ├── routers/      # API 路由
│   │   ├── schemas/      # Pydantic 模型
│   │   └── services/     # 业务逻辑
│   └── frontend/         # React 前端
│       └── src/
│           ├── pages/    # 页面
│           └── lib/      # 工具
├── docs/                 # 项目文档
├── tests/                # 测试
└── Dockerfile            # Docker 配置
```

---

## 文档维护

本文档由 BMad Document Project 工具自动生成。如需更新：

1. 运行 `bmad-document-project` 重新扫描
2. 或手动编辑对应文档文件
