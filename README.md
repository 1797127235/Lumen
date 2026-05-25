# Lumen

<p align="center">
  <b>你的长期个人 AI 伙伴</b><br>
  <i>记住你 · 理解你 · 接着上次继续聊</i>
</p>

---

## 这是什么

Lumen 是一个长期个人 AI 伙伴。它不只是聊天工具，而是一个会记住你、理解你、持续和你互动的 AI。

你可以把它理解为一个更长期的个人 AI：

- **会记住你**：保留你的偏好、经历、关系线索和重要时刻
- **会持续理解你**：不是每次都从零开始，而是能接着之前的话题继续
- **会长期陪你**：从日常聊天到阶段性变化，逐步形成更稳定的互动关系

当前项目同时支持 **本地 Web 开发模式** 和 **Tauri 桌面模式**。默认定位仍然是单用户、长期互动、隐私优先的个人 AI 产品。

**技术栈**：Tauri v2 (Rust) + FastAPI (Python sidecar) + React 19 + Vite + Tailwind CSS 4 + SQLite + PydanticAI

---

## 快速开始

### 前提

- **Rust**（1.80+）：`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- **Python**（3.11+）
- **Node.js**（20+）

### 启动

```bash
# 安装前端依赖
npm install

# 安装 Python 依赖
pip install -r requirements.txt

# 配置 API Key（二选一）
# 方式一：编辑 .env
#   LLM_PROVIDER=deepseek
#   LLM_API_KEY=your-key
# 方式二：启动后在设置页填写

# 方式一：Web 本地开发
# Windows: 双击 start.bat
# PowerShell: .\start.ps1
# 打开 http://localhost:5173

# 方式二：启动桌面应用（自动拉起 Python 后端 + Vite 前端 + Tauri 窗口）
cargo tauri dev
```

桌面模式下，Python 后端作为 sidecar 子进程由 Rust 管理生命周期，关闭窗口自动结束。


## 架构

```
┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐
│  React 前端       │  │  Telegram Bot    │  │  CLI         │
│  (Vite / Tauri)  │  │  (Polling)       │  │  (stdin)     │
└────────┬─────────┘  └────────┬─────────┘  └──────┬───────┘
         │ SSE / HTTP          │ Bot API            │ stdio
         └─────────────────────┼────────────────────┘
                                ↓
              ┌──────────────────────────────────────┐
              │  Python FastAPI  (127.0.0.1:8000)     │
              │                                       │
              │  WebChannel / TelegramChannel / CLI   │
              │  Channel              ↓               │
              │              MessageBus + EventBus    │
              │                       ↓               │
              │              AgentRunner (PydanticAI) │
              │                       ↓               │
              │  记忆系统 (memory.md 文件优先)         │
              │  SQLite  (~/.lumen/lumen.db)           │
              └──────────────────────────────────────┘
                         ↑ sidecar subprocess
              ┌──────────────────────────────────────┐
              │  Rust lib.rs（Tauri 桌面模式）         │
              │  start_backend() / Job Object 绑定    │
              └──────────────────────────────────────┘
```

桌面模式（`cargo tauri dev`）：Rust 管理 Python 子进程生命周期。  
Web 开发模式（`.\start.ps1`）：直接运行 Python + Vite，无 Tauri。  
Telegram 模式：配置 `telegram_bot_token` 后同一 Python 进程启动 TelegramChannel。

---

## 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 桌面壳 | Tauri v2 (Rust) | 体积小（~5MB），原生系统 API，不与 Node 绑定 |
| 后端 | FastAPI (Python sidecar) | 保持现有 Python AI 生态不变 |
| 通信 | HTTP localhost:8000 | 前后端解耦，Vite proxy 同端口无需 CORS |
| Agent | PydanticAI + ReAct Loop | 流式推理，4 个工具，可观测 |
| 数据库 | SQLite (aiosqlite) | 单文件零运维，FTS5 全文搜索 |
| 前端 | React 19 + Tailwind CSS 4 | 现代 UI，OKLCH 配色 |
| 记忆 | memory.md 文件优先 + MemoryProvider 插件 | 长期记忆存为可编辑 Markdown（唯一真相源）；外部语义召回通过 provider 插件按需接入 |

---

## 项目结构

```
core/             基础设施与运行时（配置、数据库、启动、Agent 装配）
lib/              业务模块（Chat、Memory、Tools、Profile、Data Sources）
server/           FastAPI 路由层
src/              React 19 前端（Vite + Tailwind CSS 4）
src-tauri/        Tauri v2 桌面壳（Rust）
tests/            pytest 测试用例
docs/             设计文档与 Story 记录
```

详见 [`AGENTS.md`](AGENTS.md) 了解完整的文件树、API 列表和架构决策。

## 开发

```bash
# 仅后端
python -m uvicorn main:app --reload

# 仅前端
npm run dev

# 完整桌面（推荐）
cargo tauri dev

# 测试
pytest

# 打包
cargo tauri build
```

---

## License

[MIT](LICENSE)
