# Lumen

一个真正认识你的 AI 伙伴。

## 这是什么

Lumen 是一个长期个人 AI 伙伴。它不只是聊天工具，而是一个会记住你、理解你、持续和你互动的 AI。

- **会记住你**：保留你的偏好、经历、关系线索和重要时刻
- **会持续理解你**：不是每次都从零开始，而是能接着之前的话题继续
- **会长期陪你**：从日常聊天到阶段性变化，逐步形成更稳定的互动关系

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + SQLAlchemy + SQLite |
| 前端 | React 19 + Vite + Tailwind CSS 4 |
| 桌面 | Tauri v2 (Rust) |
| 渠道 | Web (SSE) / Telegram Bot / CLI TUI |
| Agent | 自研 ReAct Loop + MCP 工具协议 |
| 记忆 | MEMORY.md 文件优先 + MemoryProvider 插件 |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt
npm install

# 配置 .env
LLM_API_KEY=your-key
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=your-model

# 启动
python lumen.py              # Web 模式 (http://localhost:8000)
python lumen.py --mode cli   # CLI TUI
cargo tauri dev              # 桌面模式
```

## 架构

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  React 前端      │  │  Telegram Bot   │  │  CLI TUI        │
│  (Vite/Tauri)   │  │  (Polling)      │  │  (Bun/SolidJS)  │
└────────┬────────┘  └────────┬────────┘  └────────┬────────┘
         │ SSE                │ Bot API            │ HTTP
         └────────────────────┼────────────────────┘
                              ↓
            ┌─────────────────────────────────────┐
            │  FastAPI (127.0.0.1:8000)            │
            │                                      │
            │  Channel → MessageBus → AgentRunner  │
            │                                      │
            │  记忆系统 (MEMORY.md + 插件)          │
            │  工具系统 (文件/Shell/搜索/MCP)        │
            │  调度系统 (时间触发 + 事件触发)         │
            │  SQLite (~/.lumen/lumen.db)           │
            └─────────────────────────────────────┘
```

## 项目结构

```
core/               基础设施（配置、数据库、启动、Agent）
lib/                业务模块
├── chat/           对话管理（AgentRunner、持久化、摘要）
├── memory/         记忆系统（MEMORY.md + Provider 插件）
├── tools/          Agent 工具（文件/Shell/搜索/MCP/委派）
├── scheduler/      时间触发任务（APScheduler）
├── triggers/       事件触发订阅（MCP 通知）
├── providers/      LLM Provider 管理
├── skills/         可插拔 Skill 指令包
└── session/        会话状态管理
server/             API 路由层
src/                React 前端
channels/           多渠道实现
├── web/            WebChannel (SSE)
├── telegram/       TelegramChannel
├── cli/            CLI TUI (独立 Bun 项目)
└── desktop/        Tauri 桌面壳
apps/               独立应用（MCP 服务）
tests/              测试
docs/               设计文档
```

## 记忆系统

Lumen 采用文件优先的记忆架构：

- `MEMORY.md`：长期记忆唯一真相源
- `USER.md`：由 MEMORY.md 生成的 AI 用户画像
- `PERSONA.md`：人格设定（性格、语气）

写入路径：`memory_save` / `tellAI` / `update_profile` → MEMORY.md → 后台刷新 USER.md

外部语义召回通过 MemoryProvider 插件实现（honcho / akasha），支持多实例并存。

## 工具系统

Agent 内置工具：

| 类别 | 工具 |
|------|------|
| 文件 | file_read / file_write / file_edit / file_list / grep |
| 记忆 | memory_save / memory_search / tellAI |
| 画像 | get_profile / update_profile |
| 搜索 | web_search / web_extract |
| 系统 | shell / delegate / vision |
| 调度 | schedule / subscribe_events |
| 扩展 | skill_load / tool_search / MCP 工具 |

## 开发

```bash
# 后端
python lumen.py --mode web

# 前端
npm run dev

# 桌面
cargo tauri dev

# 测试
pytest

# Lint
ruff check .
```

## API

| Method | Path | 说明 |
|--------|------|------|
| POST | /api/chat | SSE 流式对话 |
| GET | /api/chat/history | 对话历史 |
| GET | /api/memory/me | 读取记忆 |
| PUT | /api/memory/me | 保存记忆 |
| GET | /api/memory/persona | 读取人格 |
| PUT | /api/memory/persona | 保存人格 |
| GET | /api/config | 获取配置 |
| POST | /api/config | 更新配置 |
| GET | /api/health | 健康检查 |

## License

[MIT](LICENSE)
