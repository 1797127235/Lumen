# 码路领航 · CodePilot

<p align="center">
  <b>面向计算机专业学生的 AI 职业规划智能体</b><br>
  <i>An AI Career Planning Agent for CS Students</i>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#功能特性">功能</a> ·
  <a href="#项目结构">结构</a> ·
  <a href="#参与贡献">贡献</a>
</p>

---

## 这是什么

一个帮计算机专业学生做职业规划的 AI Agent。上传简历，和 AI 聊聊，它会告诉你：

- 你的背景适合走什么方向
- 想投的岗位够不够格
- 简历哪里需要改
- 下一步该补什么技能

**目标用户**：计算机相关专业在校生（本科/研究生）

**技术栈**：FastAPI + SQLAlchemy + DashScope (qwen-plus/qwen-max) + LlamaIndex + Chroma + React + Vite

---

## 功能特性

| 功能 | 状态 | 说明 |
|------|------|------|
| 智能对话 | ✅ | SSE 流式输出，7 大意图分类 |
| 知识库检索 | ✅ | LlamaIndex + Chroma 向量检索，覆盖职位/面试/技能/行业知识 |
| 用户画像 | ✅ | 上传简历 → LLM 自动提取 → 手动修正 |
| JD 诊断 | ✅ | 画像 vs 岗位要求 → 匹配评分 + 缺口分析 |
| 岗位追踪 | ✅ | 拖拽看板管理求职进度 |
| 学习路径 | 🚧 | 根据目标岗位生成学习路线 |
| 模拟面试 | 🚧 | 八股/算法/系统设计面试练习 |
| 简历优化 | 🚧 | STAR 法则改写、关键词匹配 |

---

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+（前端）
- [DashScope API Key](https://dashscope.aliyun.com/)（免费额度够用）

### 安装

```bash
git clone https://github.com/1797127235/CareerOS.git
cd CareerOS

# 后端
pip install -r requirements.txt

# 前端
cd frontend && npm install && cd ..

# 配置
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY

# 导入知识库（首次运行必需）
python scripts/import_knowledge_base.py
```

### 启动

```bash
# 方式一：一键启动（推荐）
# Windows: 双击 run.bat
# PowerShell: .\run.ps1

# 方式二：手动
# 终端 1：后端
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8001 --reload

# 终端 2：前端
cd frontend && npm run dev
```

打开 `http://localhost:5173`，开始使用。

---

## 项目结构

```
career-os/
├── app/backend/
│   ├── agent/           # AI 核心（RAG 检索、意图分类、LLM 路由、Skill 系统）
│   ├── routers/         # API 路由
│   ├── services/        # 业务逻辑
│   ├── models/          # ORM 模型
│   ├── schemas/         # Pydantic 模型
│   └── skills/          # Skill 定义（SKILL.md）
├── app/frontend/
│   └── src/pages/       # 对话、画像、JD 诊断、岗位看板
├── data/                # 知识库 JSON 数据源（职位/面试/技能/行业/案例）
├── chroma_db/           # Chroma 向量库持久化目录
├── scripts/             # 工具脚本（知识库导入等）
├── tests/               # pytest 测试用例
├── docs/                # 设计文档
│   ├── 功能设计/        # 各功能详细设计
│   ├── 架构/            # 系统架构
│   └── 需求/            # 需求文档
├── .github/workflows/   # CI/CD（lint + test + build）
├── pyproject.toml       # ruff + pytest 配置
└── run.ps1 / run.bat    # 启动脚本
```

---

## 技术选型

| 层级 | 选型 | 说明 |
|------|------|------|
| 后端 | FastAPI + SQLAlchemy 2.0 | async，类型安全 |
| 数据库 | SQLite → PostgreSQL | 开发用 SQLite，生产切 PG |
| LLM | DashScope (qwen-plus/qwen-max) | 国内访问快，性价比高 |
| Agent | 自发现 Skill + 纯函数编排 | 扫描 skills/ 目录自动生成意图分类 |
| RAG | LlamaIndex + Chroma | 向量检索 + 块切割 + DashScope embedding |
| 前端 | React + Vite + Tailwind | shadcn/ui 组件，OKLCH 配色 |
| CI/CD | GitHub Actions | ruff lint + format + pytest + frontend build |

---

## 知识库

知识库覆盖 5 个类别、175 条文档，存储在 `data/` 目录：

| 分类 | 文件 | 内容 |
|------|------|------|
| 职位数据 | `knowledge_base.json` | 前端/后端/算法/数据/测试等岗位介绍、技能要求、职级薪资 |
| 面试题库 | `interview_qa.json` | 算法八股、系统设计、HR 面常见问题 |
| 技能图谱 | `skill_graph.json` | 分岗位、分阶段的学习路线和工具栈推荐 |
| 行业报告 | `industry_report.json` | 薪资行情、城市差异、技术趋势 |
| 用户案例 | `user_cases.json` | 真实转行故事 |

首次运行需导入：`python scripts/import_knowledge_base.py`。之后 Chroma 会自动从 `chroma_db/` 加载索引。

---

## 设计理念

- **Skill 系统**：7 个 Skill 按需加载，节省 token，加新 Skill 只需建目录放 SKILL.md
- **画像驱动**：所有功能都基于用户画像，上传简历一次，后续对话/诊断都能用
- **框架优先**：能用成熟方案不手写 — RAG 用 LlamaIndex 而非自研向量检索
- **解耦设计**：Chroma（向量检索）与 SQL（元数据审计）独立存储，互不依赖

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
| `DASHSCOPE_API_KEY` | ✅ | DashScope API Key |
| `DATABASE_URL` | ❌ | 默认 SQLite（`sqlite+aiosqlite:///career_os.db`） |
| `FRONTEND_URL` | ❌ | CORS 白名单，默认 `http://localhost:5173` |

完整配置见 `.env.example`。

---

## License

[MIT](LICENSE)
