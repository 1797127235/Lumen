# Telegram Bot 接入设计文档

**日期**: 2026-05-24  
**主题**: Lumen Telegram Bot 接入与通信层解耦  
**状态**: 待评审

---

## 1. 项目背景

Lumen 当前仅支持 Web 前端（SSE 流式对话）。为了在其他平台上也能使用 Lumen（Telegram、未来可能的 QQ/Discord），需要：

1. **解耦通信层**：现有 `stream_chat()` 直接产出 SSE 事件，与 Web 深度耦合
2. **接入 Telegram Bot**：用户可以在 Telegram 上与 Lumen 对话
3. **保证工具可用**：用户在 Telegram 上也能使用 `memory_search`、`shell`、`web_search` 等工具
4. **记忆层兼容**：多平台共享记忆，不冲突、不丢失

---

## 2. 设计决策

### 2.1 用户身份

**决策**: 单用户模式，所有平台共享同一个 `demo_user`

- Lumen 当前是单人使用，没有多用户认证
- `demo_user` 在 Web 上使用，Telegram 上也是同一个用户
- 不需要用户映射表，不需要平台间用户关联
- 所有平台的记忆全部共享，不隔离

### 2.2 记忆层策略

**决策**: 共享记忆，仅标记来源

| 维度 | 策略 | 原因 |
|------|------|------|
| **Profile（画像）** | 跨平台共享 | 兴趣、价值观不因平台改变 |
| **Narrative（对话）** | 跨平台共享 | 单人使用，隔离无意义 |
| **去重** | 保持现有逻辑 | `dedupe_key` 不含平台信息 |
| **source_platform** | 新增字段，仅用于日志 | 方便追溯记忆来源 |

### 2.3 通信层模式

**决策**: 使用 Akashic-Agent 参考模式，但简化实现

Akashic-Agent 使用 MessageBus + Channel 抽象，但 Lumen 是单进程异步应用，不需要 MessageBus。改为 **Orchestrator + Channel Adapter** 两层架构：

```
┌──────────────────────────────────────────────┐
│  Channel 层（平台适配）                        │
│  ├─ WebChannel     ← SSE 流式输出             │
│  └─ TelegramChannel ← Polling + 主动发送       │
│       ↓ 调用                                   │
├──────────────────────────────────────────────┤
│  编排层（lib/chat/orchestrator.py）            │
│  run_chat_turn() → ChatResult                 │
│  · 不假设输出格式                              │
│  · 不假设通信协议                              │
│  · 返回结构化结果                              │
│       ↓ 返回                                   │
├──────────────────────────────────────────────┤
│  核心层（复用现有）                             │
│  ├─ core/agent.py        ← Agent 实例        │
│  ├─ lib/memory/          ← 记忆系统          │
│  ├─ lib/tools/           ← 工具系统          │
│  └─ lib/chat/persistence.py ← 持久化         │
└──────────────────────────────────────────────┘
```

### 2.4 Telegram 交互模式

**决策**: Polling 模式

| 模式 | 优点 | 缺点 | 选择 |
|------|------|------|------|
| **Webhook** | 实时、低延迟 | 需要公网地址或内网穿透 | ❌ |
| **Polling** | 本地开发方便、零配置 | 1-3 秒延迟 | ✅ |

Lumen 当前是本地开发环境（`localhost:5173`），Polling 更适合。

### 2.5 SDK 选择

**决策**: `python-telegram-bot`

- Akashic-Agent 使用此 SDK，稳定可靠
- 原生支持 asyncio
- 提供 `Application.builder().token().build()` 快速启动

---

## 3. 架构设计

### 3.1 核心改造：Orchestrator 层

新建 `lib/chat/orchestrator.py`，提取 `stream_chat()` 的业务逻辑：

```python
@dataclass
class ChatResult:
    conversation_id: str
    full_text: str           # 完整回复文本
    thinking: str            # 思考过程
    tools_used: list[dict]   # 工具调用记录
    usage: dict | None       # token 用量
    cancelled: bool          # 是否被中断

async def run_chat_turn(
    db: AsyncSession,
    user_id: str,
    user_input: str,
    conversation_id: str | None = None,
    attachments: list | None = None,
    cancel_event: asyncio.Event | None = None,
    source_platform: str = "web",  # 新增：平台标识
) -> ChatResult:
    """纯业务逻辑，返回结构化结果，不处理输出格式。"""
```

### 3.2 Web 层兼容改造

改造 `lib/chat/service.py` 的 `stream_chat()`：

```python
async def stream_chat(db, user_id, user_input, ...):
    """保持现有 SSE API 不变，内部调用 orchestrator。"""
    result = await run_chat_turn(...)
    
    # 模拟流式输出
    for chunk in _split_text(result.full_text):
        yield {"type": "token", "content": chunk}
    
    yield {"type": "done", "conversation_id": result.conversation_id}
```

### 3.3 Telegram Channel 实现

新建 `lib/channels/telegram.py`（或 `server/channels/telegram.py`）：

```python
class TelegramChannel:
    def __init__(self, token: str, orchestrator: Orchestrator):
        self._token = token
        self._orchestrator = orchestrator
        self._app = Application.builder().token(token).build()
        
    async def start(self):
        # 注册消息处理器
        self._app.add_handler(MessageHandler(filters.TEXT, self._on_message))
        await self._app.updater.start_polling()
        
    async def _on_message(self, update: Update, context):
        chat_id = update.effective_chat.id
        text = update.effective_message.text
        
        # 调用 orchestrator
        result = await self._orchestrator.run_chat_turn(
            user_id="demo_user",  # 单用户，固定
            user_input=text,
            source_platform="telegram",
        )
        
        # 发送回复
        await context.bot.send_message(chat_id=chat_id, text=result.full_text)
```

### 3.4 数据库改造

**新增字段**：`GrowthEvent.source_platform`

```python
# lib/memory/models.py
class GrowthEvent(Base):
    # ... 现有字段 ...
    source: Mapped[str] = mapped_column(String(16), default="用户主动")
    source_platform: Mapped[str] = mapped_column(String(16), default="web")  # 新增
```

**影响**：
- 现有数据自动标记为 `"web"`
- Telegram 新数据标记为 `"telegram"`
- 仅用于日志追溯，不参与去重/隔离逻辑

---

## 4. 数据流

### 4.1 Web 场景

```
用户输入 → POST /api/chat → stream_chat() → orchestrator → Agent Loop → SSE 流式输出
                                        ↓
                                   Memory.save(platform="web")
```

### 4.2 Telegram 场景

```
用户输入 → Telegram Polling → TelegramChannel → orchestrator → Agent Loop → 完整文本
                                              ↓
                                         Memory.save(platform="telegram")
                                              ↓
                                         bot.send_message()
```

### 4.3 工具调用

无论 Web 还是 Telegram，工具调用链路完全一致：

```
Agent Loop → ToolExecutor → Memory/Shell/WebSearch/...
            ↓
        结果返回 Agent
            ↓
        orchestrator 收集完整回复
            ↓
        按平台格式输出（SSE 流 / Telegram 文本）
```

---

## 5. 文件变更清单

### 5.1 新增文件

| 文件 | 说明 |
|------|------|
| `lib/chat/orchestrator.py` | 核心编排层，平台无关的业务逻辑 |
| `lib/channels/telegram.py` | Telegram 平台适配器 |
| `lib/channels/base.py` | Channel 抽象基类（预留） |
| `migrations/add_source_platform.py` | 数据库迁移脚本 |

### 5.2 修改文件

| 文件 | 改动 |
|------|------|
| `lib/memory/models.py` | GrowthEvent 加 `source_platform` 字段 |
| `lib/memory/relational_store.py` | `create_with_dedup()` 支持 `source_platform` |
| `lib/memory/facade.py` | `remember()` 透传 `source_platform` |
| `lib/chat/service.py` | `stream_chat()` 内部调用 orchestrator |
| `core/startup.py` | 启动时初始化 Telegram Channel |
| `pyproject.toml` | 新增 `python-telegram-bot` 依赖 |

### 5.3 不改的文件

| 文件 | 原因 |
|------|------|
| `core/agent.py` | Agent 核心逻辑无感知 |
| `lib/tools/` | 工具系统已解耦 |
| `lib/memory/writer.py` | 去重逻辑不变 |
| `lib/memory/projection.py` | 投影逻辑不变 |
| `lib/memory/searcher.py` | 搜索逻辑不变（不隔离平台） |

---

## 6. 工具可用性

### 6.1 完全可用的工具

| 工具 | Web | Telegram | 说明 |
|------|-----|----------|------|
| `memory_save` | ✅ | ✅ | 纯数据库操作 |
| `memory_search` | ✅ | ✅ | 纯数据库操作 |
| `update_profile` | ✅ | ✅ | 纯数据库操作 |
| `notes` | ✅ | ✅ | 纯数据库操作 |
| `shell` | ✅ | ✅ | 执行系统命令 |
| `web_search` | ✅ | ✅ | 调搜索引擎 |
| `skill_load` | ✅ | ✅ | 读取本地 markdown |
| `MCP 工具` | ✅ | ✅ | 纯后端桥接 |

### 6.2 需要适配的工具

| 工具 | Web | Telegram | 适配方案 |
|------|-----|----------|----------|
| `file_read` | ✅ | ⚠️ | Telegram 文件需先下载到本地 |
| `file_write` | ✅ | ⚠️ | 生成文件后通过 Telegram 发送 |

---

## 7. 未来扩展

### 7.1 接入 QQ

QQ 需要独立的 Channel（类似 TelegramChannel）：

```python
class QQChannel(BaseChannel):
    def __init__(self, ...):
        # 使用 NcatBot SDK
        # 处理 CQ 码
        # 群聊过滤
        pass
```

**不共用 Telegram 的逻辑**，因为：
- SDK 不同（NcatBot vs python-telegram-bot）
- 协议不同（WebSocket vs HTTP Polling）
- 消息格式不同（CQ 码 vs Markdown）

### 7.2 接入 Discord

```python
class DiscordChannel(BaseChannel):
    def __init__(self, ...):
        # 使用 discord.py
        pass
```

### 7.3 如果未来需要多用户

当前设计预留了扩展空间：

```python
# 现在：固定 demo_user
user_id = "demo_user"

# 未来：从映射表查询
mapping = await db.get(UserPlatformMapping, platform_id=chat_id)
user_id = mapping.user_id if mapping else create_new_user()
```

但当前不实现，避免过度设计。

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解方案 |
|------|------|----------|
| **Telegram Polling 与 Web 冲突** | 资源竞争 | 使用 asyncio.create_task() 并行运行 |
| **Telegram 文件下载失败** | 图片/文档无法处理 | 异常捕获 + 降级为文本提示 |
| **长回复超过 Telegram 限制** | 4096 字符截断 | 分段发送或多条消息 |
| **Bot Token 泄露** | 安全风险 | Token 存环境变量，不提交代码 |
| **SQLite 并发写入** | 事务冲突 | SQLite 默认 SERIALIZABLE，已天然安全 |

---

## 9. 验收标准

- [ ] Telegram 上发送消息，Lumen 能回复
- [ ] Telegram 上能触发 `memory_save`，Web 上能看到该记忆
- [ ] Web 上触发 `memory_save`，Telegram 上对话能用到该记忆
- [ ] `shell`、`web_search` 等工具在 Telegram 上可用
- [ ] Web SSE 流式输出不受影响
- [ ] 代码无类型错误（`pyright`/`ruff` 通过）

---

## 10. 实施顺序

**Phase 1：核心解耦**
1. 创建 `lib/chat/orchestrator.py`
2. 改造 `lib/chat/service.py` 兼容 SSE
3. 添加 `source_platform` 字段到 `GrowthEvent`

**Phase 2：Telegram 接入**
4. 安装 `python-telegram-bot`
5. 创建 `lib/channels/telegram.py`
6. 在 `core/startup.py` 中启动 Telegram Channel

**Phase 3：验证**
7. 本地测试 Telegram Bot
8. 验证 Web 不受影响
9. 验证工具跨平台可用

---

## 11. 部署方案

### 11.1 核心原则

**单进程，多协程，配置决定启哪些通道**

所有 Channel 跑在同一个 Python 进程里，通过 asyncio 协程调度：
- Web：FastAPI 监听 `0.0.0.0:8000`
- Telegram：协程轮询 `api.telegram.org:443`
- CLI：协程读取 `sys.stdin`

不需要额外进程，不需要额外端口（Telegram Polling 是出站连接）。

### 11.2 配置方式

通过 `.env` 环境变量控制：

```bash
# .env

# === 通道开关 ===
ENABLE_WEB=true              # Web 前端（始终可用）
ENABLE_TELEGRAM=true         # Telegram Bot
ENABLE_CLI=false             # 命令行模式（调试用）
ENABLE_QQ=false              # QQ Bot（未来）

# === Telegram 配置 ===
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_ALLOW_FROM=         # 可选：白名单用户 ID，逗号分隔

# === QQ 配置（未来）===
QQ_BOT_UIN=123456789
QQ_ALLOW_FROM=
```

### 11.3 启动流程

```python
# core/startup.py
async def lifespan(app: FastAPI):
    # 1. 初始化基础设施（数据库、日志等）
    await _init_db()
    
    # 2. 创建 Bus
    bus = MessageBus()
    event_bus = EventBus()
    
    # 3. 根据配置启动 Channels
    channels = []
    
    if settings.enable_web:
        web_channel = WebChannel(bus, event_bus)
        await web_channel.start()
        channels.append(web_channel)
        app.state.web_channel = web_channel
    
    if settings.enable_telegram and settings.telegram_token:
        tg_channel = TelegramChannel(settings.telegram_token, bus, event_bus)
        await tg_channel.start()
        channels.append(tg_channel)
    
    if settings.enable_cli:
        cli_channel = CLIChannel(bus, event_bus)
        await cli_channel.start()
        channels.append(cli_channel)
    
    # 4. 启动 AgentRunner
    runner = AgentRunner(bus, event_bus)
    runner.start()
    
    # 5. 启动出站消息分发
    dispatch_task = asyncio.create_task(bus.dispatch_outbound())
    
    yield
    
    # 6. 清理
    runner.stop()
    dispatch_task.cancel()
    for channel in channels:
        await channel.stop()
```

### 11.4 部署场景

#### 场景 1：本地开发（Web + Telegram）

```bash
# .env
ENABLE_WEB=true
ENABLE_TELEGRAM=true
TELEGRAM_BOT_TOKEN=your_token

# 启动
python main.py

# 访问 http://localhost:5173 使用 Web
# Telegram 上 @你的Bot 也能对话
```

**进程**：1 个 Python 进程
**端口**：8000（Web）
**网络**：出站到 Telegram API

---

#### 场景 2：桌面模式（Web + Telegram）

```bash
# .env（同上）

# 启动桌面应用
cargo tauri dev
```

**进程**：1 个 Rust 进程 + 1 个 Python sidecar 子进程
**说明**：Telegram Polling 跑在 Python sidecar 里，和 Web 后端同一个进程

---

#### 场景 3：服务器部署（Telegram Only）

```bash
# .env
ENABLE_WEB=false
ENABLE_TELEGRAM=true
TELEGRAM_BOT_TOKEN=your_token

# 启动
python main.py

# 或 Docker
```

**特点**：
- 不监听 HTTP 端口（ENABLE_WEB=false）
- 只有 Telegram Bot 在工作
- 适合纯 Bot 场景，不跑 Web 前端

---

#### 场景 4：服务器部署（Web Only）

```bash
# .env
ENABLE_WEB=true
ENABLE_TELEGRAM=false

# 启动
gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:app
```

**特点**：
- 传统 Web 部署
- 和当前 Lumen 部署方式一致

---

### 11.5 Docker 部署

#### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 暴露 Web 端口（如果启用）
EXPOSE 8000

CMD ["python", "main.py"]
```

#### docker-compose.yml

```yaml
version: '3.8'

services:
  lumen:
    build: .
    ports:
      - "8000:8000"  # Web 端口（ENABLE_WEB=true 时需要）
    environment:
      # 通道配置
      - ENABLE_WEB=true
      - ENABLE_TELEGRAM=true
      - ENABLE_CLI=false
      
      # Telegram Token（从宿主机环境变量传入）
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      
      # 数据库
      - DATABASE_URL=/data/lumen.db
      
      # 调试
      - DEBUG=false
    
    volumes:
      - lumen-data:/data  # 持久化数据库和上传文件
    
    # Telegram Polling 需要出站网络访问
    # 不需要暴露额外端口
    networks:
      - default
    
    restart: unless-stopped

volumes:
  lumen-data:
```

#### 启动命令

```bash
# 1. 设置环境变量
export TELEGRAM_BOT_TOKEN="your_token_here"

# 2. 启动
docker-compose up -d

# 3. 查看日志
docker-compose logs -f lumen
```

---

### 11.6 常见问题

#### Q1: Telegram Polling 和 Web 会不会冲突？

**不会。**

| 通道 | I/O 类型 | 网络方向 |
|------|----------|----------|
| Web | HTTP 监听 | 入站 (0.0.0.0:8000) |
| Telegram | HTTPS 轮询 | 出站 (api.telegram.org) |

两者都是 asyncio 协程，不会阻塞，互不干扰。

#### Q2: 需要开多个进程吗？

**不需要。**

- 当前方案：单进程多协程
- Akashic-Agent 用多进程是因为 QQ 的 NcatBot 跑在独立线程
- Lumen 所有通道都是 asyncio 友好的，不需要多进程

#### Q3: 日志怎么看？

```bash
# 所有通道的日志统一输出
tail -f logs/lumen.log

# 你会看到
[web] 收到消息: Hello
[telegram] 收到消息: Hi
[agent] 处理完成
[web] 发送回复: ...
[telegram] 发送回复: ...
```

#### Q4: 以后加 QQ 需要改部署吗？

**不需要改部署流程。**

只需要：
1. 安装 NcatBot 依赖
2. 配置 QQ 参数
3. 重启

```bash
# .env
ENABLE_QQ=true
QQ_BOT_UIN=123456789

# 同样的启动命令
python main.py
```

内部会自动处理 QQ 的线程隔离。

#### Q5: 没有公网 IP 怎么跑 Telegram Bot？

**Polling 模式不需要公网 IP！**

- Webhook 需要：Telegram → 你的服务器（需要公网）
- Polling 不需要：你的服务器 → Telegram API（出站连接，任何网络都可以）

适合：
- 本地开发
- 家庭服务器
- Docker 内网部署

---

### 11.7 配置变更对照表

| 场景 | ENABLE_WEB | ENABLE_TELEGRAM | ENABLE_CLI | 启动命令 |
|------|-----------|----------------|------------|---------|
| 本地开发 | true | true | false | `python main.py` |
| 桌面模式 | true | true | false | `cargo tauri dev` |
| 服务器 Web | true | false | false | `gunicorn main:app` |
| 服务器 Bot | false | true | false | `python main.py` |
| 调试 CLI | false | false | true | `CLI_MODE=true python main.py` |
| Docker 全功能 | true | true | false | `docker-compose up` |

---

---

## 12. CLI 交互设计

### 12.1 定位

Lumen CLI 不是编码助手（不像 Claude Code 操作文件/代码），而是**终端里的 AI 伙伴**：

- 纯文本对话，无 GUI
- 共享 Web/Telegram 的完整记忆
- 支持所有工具调用（memory_save、shell、web_search）
- 适合远程服务器、无显示器设备、快速调试

### 12.2 启动方式

```bash
# 方式 1：环境变量
ENABLE_CLI=true python main.py

# 方式 2：命令行参数
python main.py --cli

# 方式 3：独立入口（未来）
python -m lumen.cli
```

### 12.3 交互界面

```bash
$ ENABLE_CLI=true python main.py

🌙 Lumen CLI Mode
Type 'exit' or '/quit' to quit, '/help' for commands.
─────────────────────────────────

You: 你好
AI: 你好！很高兴在终端里见到你。今天想聊点什么？

You: 我喜欢猫
AI: 好的，我记住了 🐱

You: /memory
AI: 【记忆摘要】
  • 兴趣：喜欢猫
  • 性格：友善、好奇
  • 近期：刚刚在 CLI 模式下对话

You: web_search 查询 Python asyncio 最佳实践
AI: 稍等，我搜索一下...
AI: 【搜索结果】
  1. asyncio 官方文档推荐...
  2. 实际项目中建议...

You: /shell ls -la
AI: 【执行命令】
  total 128
  drwxr-xr-x  12 user  staff   384 Jan 15 10:30 .
  drwxr-xr-x   5 user  staff   160 Jan 15 10:28 ..
  ...

You: exit
👋 再见！随时可以回来找我聊天。
```

### 12.4 内置命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/help` | 显示帮助信息 | `/help` |
| `/quit`, `/exit` | 退出 CLI | `/quit` |
| `/memory` | 查看当前记忆摘要 | `/memory` |
| `/history` | 查看对话历史 | `/history 10` |
| `/clear` | 清空当前对话上下文 | `/clear` |
| `/shell <cmd>` | 执行系统命令 | `/shell ls -la` |
| `/web <query>` | 快速网页搜索 | `/web Python asyncio` |
| `/save <text>` | 手动保存记忆 | `/save 明天要开会` |
| `/status` | 显示系统状态 | `/status` |
| `/mode` | 切换模式 | `/mode verbose` |

### 12.5 特殊功能

#### 1. 多行输入

```bash
You: <<EOF
我想写一段关于猫的诗歌，
要温柔一点的风格，
大概 4 句话。
EOF
AI: 好的，我来写：
  月光洒在窗台，
  猫咪蜷缩成球，
  呼噜声像远方海浪，
  在梦里追逐蝴蝶...
```

#### 2. 文件拖拽/粘贴

```bash
You: [文件: /path/to/note.txt]
AI: 我读取了这个文件：
  "今天的想法..."
  需要我帮你总结或保存到记忆吗？
```

#### 3. 静默模式（脚本友好）

```bash
# 管道输入，纯输出
$ echo "总结今天的记忆" | python main.py --cli --silent
【记忆摘要】
• 兴趣：喜欢猫
• 今日事件：CLI 模式测试
```

#### 4. 会话恢复

```bash
$ ENABLE_CLI=true python main.py
🌙 Lumen CLI Mode
✨ 恢复了上次的对话（3 小时前）

You: 接着聊
AI: 好的，上次我们聊到你喜欢猫...
```

### 12.6 实现要点

```python
# lib/channels/cli.py
class CLIChannel(BaseChannel):
    def __init__(self, bus: MessageBus, event_bus: EventBus):
        self._bus = bus
        self._event_bus = event_bus
        self._session_history = []  # 当前会话历史
        self._mode = "normal"  # normal | verbose | silent
    
    async def start(self) -> None:
        print("🌙 Lumen CLI Mode")
        print("Type '/help' for commands, 'exit' to quit.")
        print("─" * 40)
        
        self._bus.subscribe_outbound("cli", self._on_response)
        asyncio.create_task(self._read_stdin())
    
    async def _read_stdin(self) -> None:
        import aioconsole
        
        while True:
            try:
                line = await aioconsole.ainput("You: ")
                
                # 处理内置命令
                if await self._handle_command(line):
                    continue
                
                # 发送到 Bus
                await self._bus.publish_inbound(InboundMessage(
                    channel="cli",
                    sender="user",
                    chat_id="cli",
                    content=line,
                ))
                
            except EOFError:
                break
            except KeyboardInterrupt:
                print("\n👋 再见！")
                break
    
    async def _handle_command(self, line: str) -> bool:
        """处理内置命令，返回 True 表示已处理"""
        if not line.startswith("/"):
            return False
        
        parts = line.split()
        cmd = parts[0].lower()
        
        if cmd in ["/quit", "/exit"]:
            print("👋 再见！")
            asyncio.get_event_loop().stop()
            return True
        
        if cmd == "/help":
            print("【命令列表】")
            print("  /help     - 显示帮助")
            print("  /quit     - 退出")
            print("  /memory   - 查看记忆")
            print("  /history  - 查看历史")
            print("  /clear    - 清空上下文")
            print("  /shell    - 执行命令")
            print("  /web      - 网页搜索")
            return True
        
        if cmd == "/memory":
            # 调用 memory_search 工具
            await self._bus.publish_inbound(InboundMessage(
                channel="cli",
                sender="user",
                chat_id="cli",
                content="请总结我的记忆",
            ))
            return True
        
        if cmd == "/shell" and len(parts) > 1:
            # 直接执行 shell 命令并显示结果
            import subprocess
            result = subprocess.run(parts[1:], capture_output=True, text=True)
            print(f"【执行结果】\n{result.stdout}")
            if result.stderr:
                print(f"【错误】\n{result.stderr}")
            return True
        
        return False
    
    async def _on_response(self, msg: OutboundMessage) -> None:
        if self._mode == "silent":
            print(msg.content)
        else:
            print(f"AI: {msg.content}\n")
```

### 12.7 使用场景

| 场景 | 命令 | 说明 |
|------|------|------|
| **快速调试** | `ENABLE_CLI=true python main.py` | 测试 Agent 回复，不用开浏览器 |
| **远程服务器** | SSH 后启动 CLI | 无浏览器环境，纯终端操作 |
| **自动化脚本** | `echo "query" \| python main.py --cli --silent` | 管道输入，脚本集成 |
| **树莓派/NAS** | 后台运行 CLI | 低资源设备，无 GUI |
| **开发测试** | 测试记忆、工具调用 | 快速验证功能 |

### 12.8 与其他通道的对比

| 功能 | Web | Telegram | CLI |
|------|-----|----------|-----|
| **界面** | 富文本、图片、SSE 流式 | Markdown、实时编辑 | 纯文本、即时打印 |
| **附件** | 上传文件、图片 | 发送文件、照片 | 拖拽路径、粘贴文本 |
| **工具显示** | 流式显示工具调用状态 | 实时编辑消息显示状态 | 文本列表显示 |
| **记忆共享** | ✅ | ✅ | ✅ |
| **shell 工具** | ✅ | ✅ | ✅ |
| **适用场景** | 日常使用 | 移动端、外出 | 服务器、调试、自动化 |
| **启动依赖** | 浏览器 | Telegram App | 终端 |

---

*设计完成，等待评审。*
