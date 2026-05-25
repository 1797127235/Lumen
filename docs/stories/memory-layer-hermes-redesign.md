# 记忆层重构：GrowthEvent → hermes 设计

> 状态：待实现
> 日期：2026-05-25

---

## 背景与动机

当前记忆层围绕 `GrowthEvent` 事件数据库构建，每次写入经过三层：

```
memory_save → GrowthEvent（SQLite）→ 投影 → memory.md → LLM 合成 → about_you.md
```

`memory.md` 和 `about_you.md` 才是 Agent 实际使用的文件，GrowthEvent 是中间层。这条管线提供了去重、审核、历史溯源、重建能力，但这些能力在当前单用户场景下没有被实际使用，换来的代价是 13 个模块、FTS5 索引、投影补偿循环等基础设施。

参考 hermes-agent 的设计：MEMORY.md + USER.md 平面文件直接读写，外部语义搜索通过可插拔 MemoryProvider 接入。Lumen 已经有等价文件（`memory.md` + `about_you.md`），可以直接采用同样的设计。

---

## 目标状态

```
memory_save → 直接写 memory.md
memory.md  → LLM 合成（异步）→ about_you.md
外部语义搜索 → MemoryProvider 插件（可选，~/.lumen/plugins/memory/<name>/）
```

L0 / L1 注入保留，L2 由 MemoryProvider.prefetch() 承担（无插件时降级为空）。

---

## 实现步骤

### Step 1 — 新建 MemoryProvider 层

**新建 `lib/memory/provider.py`**

```python
class MemoryProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    def is_available(self) -> bool:
        return False

    def initialize(self, session_id: str, **kwargs) -> None: ...
    def system_prompt_block(self) -> str:
        return ""
    async def prefetch(self, query: str) -> str:
        return ""
    def sync_turn(self, user: str, assistant: str) -> None: ...
    def on_pre_compress(self, messages: list) -> str:
        return ""
    def on_session_end(self, messages: list) -> None: ...
    def shutdown(self) -> None: ...
```

**新建 `lib/memory/manager.py`**

单个外部 provider + 内置 MD 文件注入。核心方法：

- `prefetch_all(query)` — 调外部 provider.prefetch()，返回上下文字符串
- `sync_all(user, assistant)` — 调 provider.sync_turn()
- `on_pre_compress(messages)` — 调 provider.on_pre_compress()，返回结果追加进压缩摘要提示词
- `shutdown_all()` — 进程退出时调用

**新�� `lib/memory/loader.py`**

扫描 `~/.lumen/plugins/memory/<name>/`，发现 `register(ctx)` 入口，激活 `is_available()` 为 True 的 provider。复用 SkillsLoader 文件系统发现模式。

```
~/.lumen/plugins/memory/<name>/
  __init__.py    # MemoryProvider 子类 + register(ctx)
  plugin.yaml    # name / description / requires
```

**验证**：`MemoryManager` 无插件时正常初始化，`prefetch_all()` 返回空字符串不报错���

---

### Step 2 — 改写 memory ���具

**`lib/tools/memory.py`** 重写，去掉 GrowthEvent 依赖：

**`memory_save`**：
1. 读 `~/.lumen/memory.md`
2. 将新内容合并进对应段落（按 entity_type 路由到对应 section）
3. 原子写回
4. 异步触发 `update_ai_understanding()`（已有，5 分钟防抖）

**`memory_search`**：
1. 调 `MemoryManager.prefetch_all(query)`
2. 无 provider 时降级：读 `memory.md` 做简单文本匹配返回相关段落

**验��**：对话中调 `memory_save`，`memory.md` 内���变化；重启后内��仍在；`memory_search` 返回相关内容。

---

### Step 3 — 删除 GrowthEvent 管线

**删除的文件：**

```
lib/memory/facade.py
lib/memory/models.py
lib/memory/writer.py
lib/memory/classifier.py
lib/memory/events_merger.py
lib/memory/relational_store.py
lib/memory/projection.py
lib/memory/observations.py
lib/memory/searcher.py
lib/memory/search.py
```

**保���并适配：**

| 文件 | 操作 |
|---|---|
| `lib/memory/markdown.py` | 保留，MD 文件原子读写 |
| `lib/memory/understanding.py` | 保留，LLM 合成 about_you.md |
| `lib/memory/snapshot.py` | 适配：删 L2 FTS5 部分，L2 改为 `MemoryManager.prefetch_all()` |

**清理基础设施：**

| 文件 | ��作 |
|---|---|
| `core/migrations.py` | 删 FTS5 表、��发器、growth_events 相关 migration |
| `core/vector_store.py` | 删除（职责移到 MemoryProvider） |
| `core/startup.py` | 删 Provider 补偿循环 |

**验证**：`pytest` 全通，后端启动不报错，无 import 报错。

---

### Step 4 — 接入 Agent 流

**`lib/chat/persistence.py`**：

```python
# persist_turn() 末尾
await memory_manager.sync_all(user_msg, assistant_response)
```

**`lib/chat/session.py`**：

```python
# 上下文压缩前（填当前缺口��
extra = memory_manager.on_pre_compress(messages_about_to_compress)
# 将 extra 追加进���缩摘要 prompt
```

**`core/agent.py`**：`LumenDeps` 加 `memory_manager: MemoryManager`

**`core/startup.py`**：lifespan 中初始化 `MemoryManager`，加载插件，进程退出调 `shutdown_all()`

**验证**：轮次结束后 `memory.md` 更新；压缩前内容不丢��。

---

### Step 5 — 清理 API 路由

`server/routes/memory.py` 中依赖 GrowthEvent 的接口：

| 接口 | 操作 |
|---|---|
| `GET /api/memory/list` | 改为读 `memory.md` 段落列表 |
| `GET /api/memory/stats` | 简化或删除 |
| `POST /api/memory/rebuild` | 改为触发 `update_ai_understanding()` |
| `DELETE /api/memory/{event_id}` | 删除（无事件概念） |
| `PATCH /api/memory/{event_id}` | 删除 |
| `POST /api/memory/{event_id}/review` | 删除 |
| `GET /api/memory/observations` | 删除 |
| `POST /api/memory/tell` | 改为直接调 `memory_save` 工具逻辑 |
| `GET /api/memory/me` | 保留，读 `memory.md` |
| `GET /api/memory/understanding` | 保留，读 `about_you.md` |
| `POST /api/memory/understanding/refresh` | 保留，触发 LLM 重新��成 |
| `POST /api/memory/understanding/correct` | 保留 |
| `POST /api/memory/reset` | 改为清空 `memory.md` + `about_you.md` |

**验证**：前端记忆相关页面（Memories、Profile）正常加载，无 500 错误。

---

## 规模对比

| | 重构前 | 重构后 |
|---|---|---|
| `lib/memory/` 模块数 | 13 | 6（provider / manager / loader / markdown / understanding / snapshot） |
| SQLite 记忆相关表 | growth_events + FTS5 虚拟表 | 无 |
| 记忆写入路径 | memory_save → GrowthEvent → 投�� → memory.md | memory_save → memory.md |
| L2 搜索 | FTS5（关键词）+ NullProvider | MemoryProvider.prefetch()（插件可选） |
| 外部记忆服务 | 不支持 | 支持（Honcho / Mem0 / Hindsight 等） |
