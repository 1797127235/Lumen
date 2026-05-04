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

**技术栈**：FastAPI + SQLAlchemy + LiteLLM（多 Provider 路由）+ LlamaIndex + Chroma + React + Vite

---

## 功能特性

| 功能 | 状态 | 说明 |
|------|------|------|
| 智能对话 | ✅ | SSE 流式输出，7 大意图分类 |
| 记忆检索 | ⚠️ | 用户数据已索引（LlamaIndex + Chroma），对话中尚未调用检索 |
| 用户画像 | ✅ | 上传简历 → LLM 自动提取 → 手动修正 |
| JD 诊断 | ✅ | 画像 vs 岗位要求 → 匹配评分 + 缺口分析 |
| 岗位追踪 | ✅ | 拖拽看板管理求职进度 |
| 技能记录 | ✅ | 表单管理 + 简历自动同步 |
| 项目经历 | ✅ | 表单管理 + AI 从对话中提取 |
| 学习路径 | 🚧 | 对话意图可用，暂无独立页面 |

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
cd frontend && npm install && cd ..

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
│   ├── agent/           # AI 核心（RAG 记忆检索、意图分类、LLM 路由、Skill 系统）
│   ├── routers/         # API 路由
│   ├── services/        # 业务逻辑
│   ├── models/          # ORM 模型（User, UserProfile, Conversation, Message, JDDiagnosis, JobTarget, Document, Chunk）
│   ├── schemas/         # Pydantic 模型
│   └── skills/          # Skill 定义（SKILL.md）
├── app/frontend/
│   └── src/pages/       # 对话、画像、JD 诊断、岗位看板、设置
├── tests/               # pytest 测试用例
├── docs/                # 设计文档
├── Dockerfile           # 多阶段构建（node + python）
├── docker-compose.yml   # 单服务 + 持久化 volume
├── .github/workflows/   # CI/CD（lint + test + build）
├── pyproject.toml       # ruff + pytest 配置
└── run.ps1 / run.bat    # 本地开发启动脚本
```

---

## 数据架构

CareerOS 的"知识库"不是预置的静态数据，而是**用户自己的个人数据**：

```
用户画像（我是谁）     → 学校、年级、目标方向、技能
对话历史（我聊过什么） → 和 AI 的所有对话记录
技能记录（我会什么）   → 从对话中识别，用户确认后入库
项目经历（我做过什么） → 项目描述、技术栈、角色
JD/岗位（我投过什么） → JD 诊断结果、岗位看板
```

这些数据通过 LlamaIndex + Chroma 向量化存储，AI 在对话时自动检索相关上下文——越用越懂你。

所有数据存储在 `~/.careeros/`：
```
~/.careeros/
├── career_os.db      # SQLite（对话、画像、岗位等）
├── chroma_db/        # Chroma 向量索引
└── config.json       # 用户设置（API Key 等）
```

---

## 技术选型

| 层级 | 选型 | 说明 |
|------|------|------|
| 后端 | FastAPI + SQLAlchemy 2.0 | async，类型安全 |
| 数据库 | SQLite（单文件） | 自托管首选，零运维 |
| LLM | LiteLLM（DashScope / OpenAI / DeepSeek / Anthropic / Gemini / Ollama / OpenRouter）| 多 Provider 统一路由，用户自选 |
| Agent | 自发现 Skill + 纯函数编排 | 扫描 skills/ 目录自动生成意图分类 |
| 记忆层 | LlamaIndex + Chroma | 向量检索用户个人数据，Provider 原生 Embedding |
| 前端 | React + Vite + Tailwind | shadcn/ui 组件，OKLCH 配色 |
| 部署 | Docker Compose | 单容器，单端口，持久化 volume |

---

## 设计理念

- **自托管优先**：数据在本地，不依赖外部服务，用户自己掌控
- **记忆驱动**：RAG 索引的是用户自己的数据，不是预置知识库
- **表单为主、对话为辅**：基础数据表单填写，AI 在对话中识别新信息并确认后入库
- **框架优先**：能用成熟方案不手写 — RAG 用 LlamaIndex，向量库用 Chroma
- **解耦设计**：Chroma（向量检索）与 SQL（元数据）独立存储，互不依赖

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
