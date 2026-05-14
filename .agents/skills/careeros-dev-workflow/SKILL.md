---
name: CareerOS Dev Workflow
description: Git branching, commit conventions, and project-specific development patterns for CareerOS
triggers:
  - "开新功能"
  - "创建分支"
  - "git workflow"
  - "分支管理"
  - "怎么提交"
  - "走 PR"
  - "feature branch"
  - "commit message"
  - "开发规范"
  - "命名规范"
  - "git 流程"
  - "开始开发"
  - "写代码前"
---

# CareerOS 开发工作流

本 skill 覆盖 CareerOS 项目的 Git 分支策略、Commit 规范、目录约定和双后端开发注意事项。

## 一、Git 分支流程（GitHub Flow）

### 每次开新功能的完整 checklist

```bash
# 1. 确保 main 最新
git checkout main
git pull origin main

# 2. 创建功能分支（见命名规范）
git checkout -b feat/xxx

# 3. 开发、提交（小步快跑）
git add <具体文件>        # 不要 git add -A，容易把无关文件带进去
git commit -m "feat(scope): 描述"

# 4. 推送到 GitHub
git push -u origin feat/xxx

# 5. 网页上创建 Pull Request -> Review diff -> Merge -> Delete branch

# 6. 本地同步并清理
git checkout main
git pull origin main
git branch -d feat/xxx
```

### 分支命名规范

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat/` | 新功能 | `feat/v2/parse-history` |
| `fix/` | Bug 修复 | `fix/frontend/upload-error` |
| `chore/` | 清理/杂务 | `chore/remove-dead-code` |
| `docs/` | 文档更新 | `docs/readme-update` |
| `refactor/` | 重构 | `refactor/extract-service` |
| `security/` | 安全相关 | `security/add-rate-limit` |

### Commit Message 规范

采用 Conventional Commits，**必须加 scope**：

```
feat(backend2): 新增 resume parser pipeline
fix(frontend): 修复预览模态框样式
chore: 清理过时脚本
docs: 更新 README 安装说明
refactor: 提取共享文本提取逻辑
security: 添加 server hardening
```

Scope 按项目模块划分：
- `backend2` — v2 后端（FastAPI，port 8001）
- `frontend` — v2 前端（React，port 5174）
- `v2` — 跨前后端的 v2 功能
- `brand` — 品牌/视觉相关

### 常见坑

1. **别用 `git add -A`** — 容易把未跟踪文件（如 `.Codex/`、临时文档）带进去
   - 正确：`git add 文件1 文件2`
   - 或：`git add -u`（只 stage 已跟踪文件的修改）

2. **main 永远保持可运行** — 合进去的东西至少能 `npm run build` + `python -m uvicorn backend.app:app`

3. **一个分支只做一件事** — 别在 `feat/xxx` 里顺手修别的 bug

4. **PR 描述写清楚** — 即使 solo 开发，也要写 "改了什么、为什么、怎么验证"

---

## 二、项目架构约定

### 双后端共存（v1 + v2）

```
CareerPlanningAgent/
├── backend/          # v1 后端（port 8000）— 聊天、报告、图谱等 legacy 功能
├── backend2/         # v2 后端（port 8001）— 简历解析、画像（pluggable architecture）
├── frontend/         # v1 前端（legacy，逐步废弃）
└── frontend-v2/      # v2 前端（React + Vite，当前主力）
```

**开发时务必注意端口和 API 版本：**
- `/api/*` -> proxy 到 `localhost:8000`（v1 后端）
- `/api/v2/*` -> proxy 到 `localhost:8001`（v2 后端）
- v1 和 v2 **共享同一个 SQLite 数据库**

### 目录约定

**backend2/**（Python, FastAPI）
```
backend2/
├── app.py              # FastAPI 入口
├── core/               # 配置、安全、错误处理
├── db/session.py       # SQLAlchemy engine + session
├── routers/            # HTTP 路由（只做边界校验，不碰业务逻辑）
├── schemas/            # Pydantic 输入输出契约
├── services/           # 业务逻辑
│   └── profile/
│       ├── parser/     # 简历解析（pluggable pipeline）
│       │   ├── extractors/     # 文本提取（pdf, docx, txt, ocr）
│       │   ├── strategies/     # 解析策略（llm_direct, resumesdk）
│       │   └── pipeline.py     # 编排管线
│       └── service.py  # 画像保存/读取
└── llm/                # LLM 客户端封装
```

**frontend-v2/**（React, TypeScript, Vite）
```
frontend-v2/src/
├── api/                # API 客户端（barrel export）
│   ├── client.ts       # 底层 fetch + auth
│   ├── profiles.ts     # v1 profile API
│   ├── profiles-v2.ts  # v2 profile API（parser pipeline）
│   └── index.ts        # barrel re-export
├── components/         # React 组件（按功能分子目录）
├── hooks/              # Custom hooks
├── pages/              # 页面级组件
└── types/              # 共享 TypeScript 类型
```

### API 开发原则

**backend2 routers 只做边界：**
- 接收/校验 HTTP 请求
- 调用 Service 层
- 返回 response schema
- **不做**：OCR、LLM prompt、数据库事务

**Service 层负责业务：**
- `backend2/services/profile/service.py` — 画像保存事务
- `backend2/services/profile/parser/pipeline.py` — 解析管线编排

**Parser 架构（关键设计）：**
```
ResumeFile -> Extractor -> ResumeDocument
                                    |
                              ParserPipeline
                         +---------+---------+
                    LLM Strategy    Evidence Collector
                         +---------+---------+
                                |
                               Merger
                                |
                          ProfileData（v2 格式）
```

---

## 三、启动命令速查

```bash
# 一键启动（推荐）
run.bat   # 按提示选 1 启动前端+后端

# 手动启动
python -m uvicorn backend.app:app --reload          # v1 后端 http://localhost:8000
cd backend2 && python -m uvicorn backend2.app:app --reload --port 8001  # v2 后端
cd frontend-v2 && npm run dev                       # 前端 http://localhost:5174
```

---

## 四、TypeScript / Python 跨语言注意事项

**v1 <-> v2 数据格式差异（画像相关）：**

| 字段 | v1 格式 | v2 格式 |
|------|---------|---------|
| education | 单个对象 `{degree, major, school}` | 数组 `[{degree, major, school, ...}]` |
| skills | `{name, level}[]` level: expert/proficient/... | `{name, level}[]` level: beginner/familiar/intermediate/advanced |
| projects | `string[]` 或 `{name, description, tech_stack[]}[]` | `{name, description, tech_stack[], duration, highlights}[]` |

**v2 save_profile 做了自动兼容转换：**
- 写入时，v2 `ProfileData` 通过 `_to_v1_profile_json()` 转成 v1 格式存进 `profiles.profile_json`
- 所以 v1 读取端仍然能正常显示

---

## 五、Code Review 自检清单（发 PR 前自己看一遍）

- [ ] 分支名符合 `feat/xxx`、`fix/xxx`、`chore/xxx` 规范
- [ ] Commit message 有 scope 前缀
- [ ] `git diff` 里没有 `console.log`、注释掉的代码、临时文件
- [ ] 前端 `npx tsc --noEmit` 通过
- [ ] 后端修改后确认没 break 现有测试
- [ ] 新增 API 端点有对应的前端/后端类型定义
- [ ] 双后端场景：确认改的是 v1 还是 v2，别改错
