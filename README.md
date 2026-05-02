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

**技术栈**：FastAPI + SQLAlchemy + DashScope (qwen-plus) + React + Vite

---

## 功能特性

| 功能 | 状态 | 说明 |
|------|------|------|
| 智能对话 | ✅ | SSE 流式输出，7 大意图分类 |
| 用户画像 | ✅ | 上传简历 → LLM 自动提取 → 手动修正 |
| JD 诊断 | ✅ | 画像 vs 岗位要求 → 匹配评分 + 缺口分析 |
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
│   ├── agent/           # AI 核心（意图分类、LLM 路由、Skill 系统）
│   ├── routers/         # API 路由
│   ├── services/        # 业务逻辑
│   ├── models/          # ORM 模型
│   ├── schemas/         # Pydantic 模型
│   └── skills/          # Skill 定义（SKILL.md）
├── app/frontend/
│   └── src/pages/       # 对话、画像、JD 诊断三页
├── docs/                # 设计文档
│   ├── 功能设计/        # 各功能详细设计
│   └── 架构/            # 系统架构
└── run.ps1              # 启动脚本
```

---

## 技术选型

| 层级 | 选型 | 说明 |
|------|------|------|
| 后端 | FastAPI + SQLAlchemy 2.0 | async，类型安全 |
| 数据库 | SQLite → PostgreSQL | 开发用 SQLite，生产切 PG |
| LLM | DashScope (qwen-plus) | 国内访问快，性价比高 |
| Agent | LangGraph | 只做意图分类，不搞复杂编排 |
| 前端 | React + Vite + Tailwind | shadcn/ui 组件，OKLCH 配色 |

---

## 设计理念

- **Skill 系统**：7 个 Skill 按需加载，节省 token，加新 Skill 只需建目录放 SKILL.md
- **画像驱动**：所有功能都基于用户画像，上传简历一次，后续对话/诊断都能用
- **渐进式**：MVP 先跑通核心流程，后续按需加功能

---

## 参与贡献

欢迎提 Issue、建议功能、提交 PR。

```bash
git checkout -b feat/your-feature
git commit -m "feat: add something"
git push origin feat/your-feature
# 然后在 GitHub 上创建 PR
```

### 开发规范

- Python 3.11+，类型提示
- SQLAlchemy 2.0 async 风格
- Pydantic v2
- 中文 commit message

---

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `DASHSCOPE_API_KEY` | ✅ | DashScope API Key |
| `DATABASE_URL` | ❌ | 默认 SQLite |
| `FRONTEND_URL` | ❌ | CORS 白名单 |

完整配置见 `.env.example`。

---

## License

[MIT](LICENSE)
