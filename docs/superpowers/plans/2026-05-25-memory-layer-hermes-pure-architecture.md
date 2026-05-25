# 记忆层 Hermes-Pure 重构 — 目标架构图

> 日期：2026-05-25  
> 对应计划：`docs/superpowers/plans/2026-05-25-memory-layer-hermes-pure.md`

---

## 一、系统全景架构

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                              接入层（多通道）                                 │
│                                                                             │
│  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐           │
│  │   WebChannel    │  │ TelegramChannel  │  │    CLIChannel    │           │
│  │   (SSE/HTTP)    │  │   (Bot API)      │  │    (stdin/out)   │           │
│  │                 │  │                  │  │                  │           │
│  │ · Streaming     │  │ · 整段发送       │  │ · 整行读取       │           │
│  │   Context       │  │ · 无需流式清洗   │  │ · 无需流式清洗   │           │
│  │   Scrubber      │  │                  │  │                  │           │
│  └────────┬────────┘  └────────┬─────────┘  └────────┬─────────┘           │
│           │                    │                     │                       │
│           └────────────────────┼─────────────────────┘                       │
│                                ▼                                             │
│                         ┌──────────────┐                                    │
│                         │  MessageBus  │                                    │
│                         │  (inbound)   │                                    │
│                         └──────┬───────┘                                    │
│                                │                                            │
└────────────────────────────────┼────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              核心层（AgentRunner）                            │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        PydanticAI Agent                             │   │
│  │                                                                     │   │
│  │  System Prompt 组装顺序（不可变部分在前，动态部分在后）：               │   │
│  │                                                                     │   │
│  │  ┌───────────────────────────────────────────────────────────────┐  │   │
│  │  │ 1. 基础角色定义 + 工具说明                                     │  │   │
│  │  │ 2. memory_manager.build_system_prompt()  ← provider 自我介绍   │  │   │
│  │  │ 3. memory_manager.build_context()        ← <memory-context>   │  │   │
│  │  │    · L0: about_you.md / memory.md (Frozen Snapshot)          │  │   │
│  │  │    · L1: recent conversations                                │  │   │
│  │  │    · L2: external_provider.prefetch(query)                   │  │   │
│  │  └───────────────────────────────────────────────────────────────┘  │   │
│  │                                                                     │   │
│  │  Turn End ──→ memory_manager.sync_all(user_msg, assistant_msg)     │   │
│  │  Compress ──→ memory_manager.on_pre_compress(messages)             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                │                                            │
│                                ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        Memory Subsystem                             │   │
│  │                                                                     │   │
│  │   ┌─────────────────────────────────────────────────────────────┐   │   │
│  │   │                    MemoryManager (fan-out)                   │   │   │
│  │   │                      进程级单例                               │   │   │
│  │   │                                                              │   │   │
│  │   │   add_provider()              build_system_prompt()          │   │   │
│  │   │   build_context()    ───┐     prefetch_all()                 │   │   │
│  │   │   sync_all()            │     sync_all()                     │   │   │
│  │   │   on_pre_compress()     │     get_all_tool_schemas()         │   │   │
│  │   │   handle_tool_call()    │     has_tool()                     │   │   │
│  │   │                         │                                    │   │   │
│  │   │   ┌─────────────────────┴─────────────────────┐             │   │   │
│  │   │   │                                         │             │   │   │
│  │   │   ▼                                         ▼             │   │   │
│  │   │ ┌──────────────────┐              ┌──────────────────────┐ │   │   │
│  │   │ │ Builtin Provider │              │ External Provider    │ │   │   │
│  │   │ │   ("builtin")    │              │   (0 或 1 个)        │ │   │   │
│  │   │ │                  │              │                      │ │   │   │
│  │   │ │ · prefetch:      │              │ · prefetch(query)    │ │   │   │
│  │   │ │   文本匹配       │              │ · sync_turn(u,a)     │ │   │   │
│  │   │ │   memory.md      │              │ · custom tools       │ │   │   │
│  │   │ │                  │              │                      │ │   │   │
│  │   │ │ · sync_turn:     │              │ · queue_prefetch     │ │   │   │
│  │   │ │   pass（不自动） │              │ · on_session_end     │ │   │   │
│  │   │ │                  │              │                      │ │   │   │
│  │   │ │ · get_tool_      │              │ · circuit breaker    │ │   │   │
│  │   │ │   schemas: []    │              │   (Mem0 等)          │ │   │   │
│  │   │ └────────┬─────────┘              └──────────┬───────────┘ │   │   │
│  │   └──────────┼───────────────────────────────────┼─────────────┘   │   │
│  │              │                                   │                 │   │
│  │              ▼                                   ▼                 │   │
│  │   ┌──────────────────┐              ┌──────────────────────┐      │   │
│  │   │ AsyncMarkdownStore│             │ 插件加载器 (loader)   │      │   │
│  │   │                  │              │                      │      │   │
│  │   │ § memory.md      │              │ ~/.lumen/plugins/    │      │   │
│  │   │   长期记忆文件    │              │   memory/<name>/     │      │   │
│  │   │   2,200 char     │              │   ├ plugin.yaml      │      │   │
│  │   │   大小限制       │              │   └ __init__.py      │      │   │
│  │   │                  │              │                      │      │   │
│  │   │ § about_you.md   │              │ config.yaml 中指定    │      │   │
│  │   │   AI 画像文件    │              │ memory.provider       │      │   │
│  │   │   1,375 char     │              │ = "honcho" / null    │      │   │
│  │   │   大小限制       │              │                      │      │   │
│  │   │ 特性：            │              │ 未配置 = NoOpProvider │      │   │
│  │   │ · § 分隔符       │              └──────────────────────┘      │   │
│  │   │ · 原子写入       │                                            │   │
│  │   │ · 安全扫描       │                                            │   │
│  │   │ · 文件锁         │                                            │   │
│  │   │ · Frozen Snapshot│                                            │   │
│  │   └──────────────────┘                                            │   │
│  │                                                                     │   │
│  │   ┌─────────────────────────────────────────────────────────────┐   │   │
│  │   │                    Context Fence 层                          │   │   │
│  │   │                                                              │   │   │
│  │   │  build_memory_context_block()                                │   │   │
│  │   │    → <memory-context> ... </memory-context>                  │   │   │
│  │   │                                                              │   │   │
│  │   │  StreamingContextScrubber (仅 WebChannel/SSE)                │   │   │
│  │   │    → 跨 chunk 状态机清洗，防止泄漏到用户 UI                   │   │   │
│  │   │                                                              │   │   │
│  │   │  sanitize_context() (Telegram/CLI)                           │   │   │
│  │   │    → 整段清洗，无需状态机                                    │   │   │
│  │   └─────────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         工具层                                      │   │
│  │                                                                     │   │
│  │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │   │
│  │   │memory_save│  │memory_search│ │get_profile│ │update_profile   │   │   │
│  │   └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘   │   │
│  │        │             │             │                  │             │   │
│  │        └─────────────┴─────────────┴──────────────────┘             │   │
│  │                          │                                          │   │
│  │              AsyncMarkdownStore.write_memory()                      │   │
│  │              （追加 § 条目，触发 about_you.md 刷新）                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              持久化层                                        │
│                                                                             │
│   ┌────────────────────┐        ┌────────────────────┐                      │
│   │   SQLite (保留)     │        │   文件系统 (新增)    │                      │
│   │                    │        │                    │                      │
│   │ · users            │        │ ~/.lumen/memory/   │                      │
│   │ · conversations    │        │   {user_id}/       │                      │
│   │ · messages         │        │   ├ memory.md      │                      │
│   │                    │        │   └ about_you.md   │                      │
│   │ (无 growth_events) │        │                    │                      │
│   │ (无 growth_events_fts)│      │ ~/.lumen/plugins/  │                      │
│   │ (无 notes)         │        │   memory/          │                      │
│   └────────────────────┘        └────────────────────┘                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、关键数据流（一次对话回合）

```
用户输入 ──→ Web/Telegram/CLI
              │
              ▼
         MessageBus
              │
              ▼
    ┌─────────────────┐
    │   AgentRunner   │
    │                 │
    │ 1. 初始化阶段   │
    │    memory_manager.initialize_all(session_id)
    │    builtin_provider.load_frozen_snapshot()
    │         │
    │         ▼
    │    ┌──────────────┐
    │    │ memory.md    │ ← 读取初始快照（Frozen Snapshot）
    │    │ about_you.md │   整个会话复用，不更新
    │    └──────────────┘
    │                 │
    │ 2. 回合开始     │
    │    context = await memory_manager.build_context(user_id, user_input)
    │         │
    │         ├── L0: about_you.md (或 memory.md 回退)
    │         ├── L1: 近期对话历史
    │         └── L2: await external_provider.prefetch(user_input)
    │                 │
    │                 ▼
    │            <memory-context> 围栏包装
    │                 │
    │    system_prompt = base_prompt + build_system_prompt() + build_context()
    │                 │
    │ 3. LLM 生成     │
    │    （可能调用 memory_save / memory_search）
    │                 │
    │    memory_save("React 19", "tech")
    │         │
    │         ▼
    │    AsyncMarkdownStore.write_memory()
    │         │
    │         ├── 追加 § 条目到 memory.md（磁盘持久）
    │         ├── 不更新 Frozen Snapshot（保护 prefix cache）
    │         └── 触发 about_you.md 刷新（5分钟防抖）
    │                 │
    │ 4. 回合结束     │
    │    await memory_manager.sync_all(user_msg, assistant_msg)
    │         │
    │         ├── builtin.sync_turn() → pass
    │         └── external.sync_turn() → 发送给外部 API
    │                 │
    │ 5. 输出         │
    │    Web: StreamingContextScrubber.feed(chunk) 逐块清洗
    │    Telegram/CLI: sanitize_context() 整段清洗
    │         │
    │         ▼
    │    用户看到纯文本（无 <memory-context> 标签）
    └─────────────────┘
```

---

## 三、组件关系图（简化版）

```
                    ┌──────────────────┐
                    │   MemoryManager   │
                    │   (fan-out 编排)  │
                    └────────┬─────────┘
                             │
           ┌─────────────────┼─────────────────┐
           │                 │                 │
           ▼                 ▼                 ▼
    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
    │   Builtin   │  │   External  │  │    NoOp     │
    │  Provider   │  │  Provider   │  │  Provider   │
    │  (always)   │  │  (optional) │  │  (default)  │
    └──────┬──────┘  └──────┬──────┘  └─────────────┘
           │                │
           ▼                ▼
    ┌─────────────┐  ┌─────────────┐
    │ memory.md   │  │ Honcho API  │
    │ about_you.md│  │ Mem0 API    │
    │ § 分隔      │  │ ...         │
    │ 文件锁      │  │             │
    │ 安全扫描    │  │             │
    │ Frozen Snap │  │             │
    └─────────────┘  └─────────────┘
```

---

## 四、System Prompt 组装顺序

```
┌─────────────────────────────────────────────────────────────┐
│                     System Prompt                           │
├─────────────────────────────────────────────────────────────┤
│ 1. 基础角色定义                                              │
│    "你是 Lumen，一个长期个人 AI 伙伴..."                     │
├─────────────────────────────────────────────────────────────┤
│ 2. Provider 自我介绍 (build_system_prompt)                   │
│    "[Honcho] 本对话使用 Honcho 记忆服务..."                  │
│    （无外部 provider 时为空）                                 │
├─────────────────────────────────────────────────────────────┤
│ 3. 动态记忆上下文 (build_context)                            │
│    <memory-context>                                         │
│    [System note: 以下是被回忆的记忆上下文...]                 │
│    · L0: about_you.md                                       │
│      "用户是经验丰富的全栈开发者，偏好直接回答..."            │
│    · L1: 近期对话                                            │
│      "用户：上次说的 Vue 3.4 性能优化..."                     │
│    · L2: external.prefetch("React 19")                      │
│      "用户最近在研究 React 19 的 Server Components..."       │
│    </memory-context>                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 五、与旧架构对比

| 维度 | 旧架构 (GrowthEvent) | 新架构 (Hermes-Pure) |
|------|----------------------|----------------------|
| **Truth Source** | SQLite `growth_events` 表 | `memory.md` 文件 |
| **用户画像** | 从事件推导 | `about_you.md` 直接生成 |
| **外部记忆** | 无 | 插件化 Provider (Honcho/Mem0) |
| **搜索** | FTS5 全文检索 | Provider prefetch + 文本匹配 |
| **并发** | DB 事务 | 文件锁 + async lock |
| **安全** | 无 | Prompt injection 扫描 |
| **用户编辑** | 逐条事件审核/删除 | 全文编辑器 |
| **Notes** | `GrowthEvent` 存储 | **删除** |
| **Snapshot** | 无 | Frozen Snapshot (prefix cache 保护) |
| **多通道** | Web-only 思维 | Web/Telegram/CLI 统一 |

---

## 六、关键设计原则

1. **AgentRunner 是唯一调用方**：MemoryManager 不直接暴露给 UI 或路由，只被 AgentRunner 调用
2. **Fan-out 编排**：Manager 广播到所有 provider，一个失败不影响其他
3. **文件是 truth source**：`memory.md` 是唯一长期记忆来源，`about_you.md` 是派生文件
4. **插件即插即用**：外部 provider 通过 `~/.lumen/plugins/memory/` 目录发现，无需改核心代码
5. **Frozen Snapshot**：会话启动时取一次快照，mid-session 写入不更新，保护 prefix cache
6. **Context Fencing**：`<memory-context>` 标签 + 系统说明，防止模型混淆记忆与用户输入
7. **渠道差异**：Web 用 StreamingContextScrubber（状态机跨 chunk），Telegram/CLI 用 sanitize_context（整段）

---

*本文档对应实施计划，描述重构完成后的目标状态。*
