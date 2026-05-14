# Lumen

<p align="center">
  <b>一个真正认识你的 AI 伴侣</b><br>
  <i>本地运行 · 原生桌面 · 隐私优先</i>
</p>

---

## 这是什么

单用户 AI 伴侣桌面应用（Tauri v2）。所有数据存本地，不依赖云端，越用越懂你。

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
┌──────────────────────────────────────────┐
│  Tauri v2 窗口（React 前端）              │
│  http://localhost:5173 (Vite dev)        │
│           │                              │
│  Vite proxy: /api → 127.0.0.1:8000      │
└───────────┼──────────────────────────────┘
            │ HTTP
┌───────────┼──────────────────────────────┐
│  Rust lib.rs                             │
│  ├─ start_backend()  ← Command::new()    │
│  ├─ stop_backend()   ← kill + wait      │
│  ├─ Tauri commands   ← IPC              │
│  └─ 文件夹监听       ← notify crate      │
└───────────┼──────────────────────────────┘
            │ spawn subprocess
┌───────────┼──────────────────────────────┐
│  Python FastAPI (127.0.0.1:8000)         │
│  ├─ SSE 流式对话 (PydanticAI Agent)      │
│  ├─ 记忆系统 (growth_events → .md → Cognee) │
│  └─ SQLite (单文件，~/.lumen/lumen.db)   │
└──────────────────────────────────────────┘
```

---

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
| 记忆 | growth_events → .md + Cognee | 事件溯源 + 语义索引 |

---

## 项目结构

```
backend/          FastAPI 后端（Agent、API、Application、Domain、Memory、Ingestion）
src/              React 19 前端（Vite + Tailwind CSS 4）
src-tauri/        Tauri v2 桌面壳（Rust）
tests/            pytest 测试用例
docs/             设计文档与 Story 记录
```

详见 [`AGENTS.md`](AGENTS.md) 了解完整的文件树、API 列表和架构决策。

## 开发

```bash
# 仅后端
python -m uvicorn backend.main:app --reload

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
