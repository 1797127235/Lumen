# 记忆双管线架构

> 最后更新：2026-05-11

## 总览

Lumen 记忆系统采用**双管线架构**，将 GrowthEvent 按语义分为两条独立管线，从架构层面消除 L0↔L2 语义重复。

```
GrowthEvent
    ↓ classifier.py（单一真相源）
    ├── Profile 管线 → .md 投影 → L0 固定注入（不进搜索索引）
    └── Narrative 管线 → FTS5 索引 → L2 按需召回
```

## 事件分类

| 管线 | 事件类型 | 语义 | 注入方式 |
|------|---------|------|----------|
| **Profile** | `profile_updated`, `skill_added`, `skill_level_changed`, `goal_updated`, `preference_learned`, `status_changed` | 用户「是谁」— 身份/技能/目标/偏好/状态 | L0 永远注入 system prompt |
| **Narrative** | `experience_added`, `decision_made`, `document_uploaded` | 用户「经历了什么」— 经历/决策/文件 | L2 keyword 搜索 / grep 时间过滤 |

单文件定义：`backend/memory/classifier.py`

## Phase 1（已实现）— FTS5 内部搜索

### 写入路径

```
remember() / remember_batch()
    → GrowthEventRepository（SQLite + FTS5 触发器自动索引）
    → flush_projections()
        → sync_user_md_projection()  → memory.md / about_you.md
        → _update_understanding()     → AI 综合画像（异步）
```

- Profile 事件 → .md 投影 + L0 固定注入
- Narrative 事件 → FTS5 自动索引（AFTER INSERT 触发器）
- Cognee 不再接收 GrowthEvent 数据

### 读取路径

```
build_context()
    ├── L0: build_snapshot() → about_you.md（5 分钟 TTL 缓存）
    ├── L1: _build_context_block() → 最近对话摘要
    └── L2: recall()
            ├── keyword 模式 → search_all() → FTS5 全文匹配
            └── grep 模式 → list_events_by_time_range() → SQL 时间过滤
```

### 搜索模式

| 模式 | 触发方式 | 典型场景 |
|------|---------|---------|
| `keyword` | `memory_search(query="Python", search_mode="keyword")` | 「我会 Python 吗」「上次的实习项目」 |
| `grep` | `memory_search(query="最近", search_mode="grep", time_filter="recent_7d")` | 「最近做了什么」「这周怎么样」 |

time_filter 选项：`today`, `yesterday`, `recent_3d`, `recent_7d`, `recent_30d`, `YYYY-MM-DD~YYYY-MM-DD`

### 关键保证

- Profile 事件**不进** FTS5（`classifier.is_indexable()` 门控 + search.py `event_type IN` 过滤）
- Narrative 事件**不参与** L0 画像聚合（snapshot 只读 about_you.md）
- L0 ↔ L2 语义重复问题**架构层面消除**，不依赖运行时过滤

---

## Phase 2（规划中）— 外部数据接入 Cognee

### 目标

接入用户外部数据源（笔记、代码仓库、文档等），利用 Cognee 知识图谱进行跨源实体链接和语义搜索。

### 架构

```
Phase 2 新增管线：

外部数据（笔记/代码仓库/文档）
    → Ingestion Pipeline（独立于 GrowthEvent）
        → 文本提取 + 分块
        → Cognee.add() + cognify
            → KuzuDB 图数据库（实体关系、跨源链接）
            → LanceDB 向量库（语义搜索）
    → L2 召回（按 NodeSets 分区搜索）
```

### Cognee 在 Phase 2 的角色

- **知识图谱**：自动实体抽取 + 跨文档关系链接（文件 A 的函数调用了文件 B 的类）
- **语义搜索**：自然语言查询跨源匹配（「上次笔记里提过的设计模式」）
- **NodeSets 分区**：外部数据与内部数据隔离，scoped recall

### 与内部管线的隔离

```
                    ┌─────────────────┐
                    │   L2 召回入口    │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
         keyword 模式    grep 模式     semantic 模式
              │              │         (Phase 2)
              ▼              ▼              ▼
         FTS5(Narrative)  SQL(时间)    Cognee(外部数据)
```

内部事件走 FTS5，外部数据走 Cognee。两条 L2 子管线物理隔离，NodeSets 分区保证互不污染。

### Cognee 当前状态（Phase 1 结束时）

- 初始化逻辑保留（`cognify_loop.py`, `init_cognee`）
- `semantic_store.py` 保留（`ingest`/`search`/`clear_index`）
- `cognee_status()` 可用
- 无 GrowthEvent 数据喂入（`build_event_content` 只对 Narrative 事件产出内容，但 facade 已不调用 `_sync_cognee`）
- `search_all(include_cognee=False)` 默认不搜索 Cognee

### Phase 2 实施要点

1. 新建 `backend/memory/external_ingestion.py` — 外部数据接入管道（文本提取、分块、去重）
2. 利用 Cognee NodeSets 分区：`dataset="lumen_external"`，与 `lumen_profile` 隔离
3. `search_all(include_cognee=True)` 打开时，scope 参数路由到对应 dataset
4. 前端上传入口：笔记/Markdown/代码文件 → backend API → ingestion pipeline → Cognee

---

## 文件清单

| 文件 | Phase 1 改动 | 说明 |
|------|-------------|------|
| `classifier.py` | **新建** | 事件分类单一真相源 |
| `facade.py` | **重构** | 删 Cognee 桥接 + grep 模式 + 写入路由 |
| `search.py` | 修改 | FTS5 主路径 + Narrative only 过滤 |
| `snapshot.py` | 修改 | L0 读 about_you.md（不查询 GrowthEvent） |
| `semantic_store.py` | 修改 | `build_event_content` 门控 |
| `tool_memory_search.py` | 修改 | 加 search_mode / time_filter |
| `pydantic_agent.py` | 修改 | system prompt 引导模式选择 |
| `events_merger.py` | 未改 | markdown.py 投影用 |
| `markdown.py` | 未改 | .md 文件投影 |
| `relational_store.py` | 未改 | FTS5 表 + 触发器 |
| `cognify_loop.py` | 未改 | Phase 2 用 |
