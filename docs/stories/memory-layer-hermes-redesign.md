# 记忆层重构：GrowthEvent → Hermes-Pure 设计

> 状态：**已完成**（2026-06-12 同步代码）
> 原始日期：2026-05-25

---

## 背景与动机

当前记忆层围绕 `GrowthEvent` 事件数据库构建，每次写入经过三层：

```
memory_save → GrowthEvent（SQLite）→ 投影 → memory.md → LLM 合成 → about_you.md
```

`memory.md` 和 `about_you.md` 才是 Agent 实际使用的文件，GrowthEvent 是中间层。这条管线提供了去重、审核、历史溯源、重建能力，但这些能力在当前单用户场景下没有被实际使用，换来的代价是 13 个模块、FTS5 索引、投影补偿循环等基础设施。

参考 hermes-agent 的设计：MEMORY.md + USER.md 平面文件直接读写，外部语义搜索通过可插拔 MemoryProvider 接入。Lumen 已经有等价文件（`memory.md` + `about_you.md`），可以直接采用同样的设计。

---

## 目标状态（已实现）

```
memory_save → 直接写 memory.md
memory.md  → LLM 合成（异步）→ about_you.md
外部语义搜索 → MemoryProvider 插件（可选，lib/memory/builtins/<name>/ 或 ~/.lumen/plugins/memory/<name>/）
```

L0 / L1 注入保留，L2 由 `MemoryManager.prefetch_all()`  fan-out 到所有已启用外部 provider（无插件时降级为空）。

---

## 实现结果与关键偏差

### 已落地的模块

| 模块 | 职责 |
|---|---|
| `lib/memory/provider.py` | `MemoryProvider` 抽象接口 + `NoOpMemoryProvider` |
| `lib/memory/manager.py` | `MemoryManager` 进程级单例；内置文件记忆 + 外部 provider fan-out |
| `lib/memory/builtin_provider.py` | `BuiltinMemoryProvider`：文件 backed，L0 冻结快照 |
| `lib/memory/loader.py` | 扫描 `lib/memory/builtins/` 与 `~/.lumen/plugins/memory/<name>/` 发现 provider |
| `lib/memory/config_store.py` | 读写 `~/.lumen/config.json["memory_providers"]` |
| `lib/memory/markdown.py` | `AsyncMarkdownStore`：MEMORY.md / USER.md 原子读写 |
| `lib/memory/context_fence.py` | `<memory-context>` 围栏构建 + 注入内容清洗 |
| `lib/memory/understanding.py` | 从 MEMORY.md 生成 USER.md（5 分钟防抖） |
| `lib/memory/snapshot.py` | 薄兼容层，委托 `MemoryManager` 构建 L0 + L1 |
| `lib/memory/builtins/honcho/` | Honcho 内置插件（语义召回 + 轮次同步） |
| `lib/memory/builtins/akasha/` | Akasha 图记忆引擎内置插件 |
| `server/routes/memory_providers.py` | Memory Provider CRUD / test / reload API |
| `server/routes/akasha.py` | Akasha Dashboard API |

### 与原设计的关键差异

1. **多外部 provider 共存**
   - 原设计：同一时间只激活**一个**外部 provider。
   - 实现：`MemoryManager.add_provider(provider, instance_name=...)` 用配置实例名作为 key，支持多个同类型 provider 并存（如 `honcho-prod` + `honcho-dev`）。`prefetch_all()` 会 fan-out 到所有外部 provider。

2. **内置插件目录**
   - 原设计：插件仅放在 `~/.lumen/plugins/memory/<name>/`。
   - 实现：增加 `lib/memory/builtins/<name>/`，Honcho 与 Akasha 都作为内置插件维护；用户目录同名插件可覆盖内置插件。

3. **配置驱动加载**
   - 原设计：通过 `config.yaml` 中 `memory.provider` 指定单个 provider。
   - 实现：通过 `~/.lumen/config.json["memory_providers"]` 列表配置多个实例；旧 `honcho_enabled=true` / `HONCHO_API_KEY` 在首次启动时自动迁移为新列表。

4. **Akasha 迁移**
   - 原设计未包含 Akasha。
   - 实现：将 `lib/memory/akasha/` 迁移为 `lib/memory/builtins/akasha/`，移除对 `agent.*` / `bus.*` / `core.memory.*` 的旧依赖，接入 `MemoryProvider` 接口；新增 `/api/akasha/*` 路由提供检索概览。

5. **写入镜像**
   - 实现：`memory_save` / `update_profile` 写入 MEMORY.md 后，调用 `MemoryManager.on_memory_write()` 将事件镜像到所有外部 provider。

6. **Embedding 客户端**
   - 实现：新增 `lib/llm/embeddings.py` 通用异步 embedding 客户端，Akasha 等插件复用 Lumen 的 embedding 配置。

---

## 规模对比

| | 重构前 | 重构后 |
|---|---|---|
| `lib/memory/` 模块数 | 13 | provider / manager / builtin_provider / loader / config_store / markdown / context_fence / understanding / snapshot / review_service / housekeeping / models |
| SQLite 记忆相关表 | growth_events + FTS5 虚拟表 | 无 |
| 记忆写入路径 | memory_save → GrowthEvent → 投影 → memory.md | memory_save → memory.md |
| L2 搜索 | FTS5（关键词）+ NullProvider | MemoryProvider.prefetch() fan-out（插件可选） |
| 外部记忆服务 | 不支持 | 支持多实例（Honcho / Akasha / 自定义插件） |

---

## 相关测试

- `tests/memory/test_provider_loader.py`
- `tests/memory/test_memory_manager.py`
- `tests/memory/test_akasha_provider.py`

运行：`pytest tests/memory/ -v`
