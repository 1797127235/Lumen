# Lumen 记忆层重构计划

## 目标

将现有 `services/` 中散落的记忆相关代码迁移到 `memory/` 目录，建立清晰的分层架构，删除旧文件。

## 原则

1. **不保留旧代码** — 迁移完成后删除原文件
2. **API 兼容** — facade 保持现有方法签名，pydantic_tools.py / routers/memory.py 只改 import 路径
3. **数据不丢** — .md 文件路径不变，SQLite 表结构不变
4. **分层清晰** — stores / projections / search / facade / cognee_admin 各司其职

---

## 新目录结构

```
app/backend/memory/
├── __init__.py                    # 导出 get_memory()
├── facade.py                      # LumenMemory：保持现有 API 签名
│
├── stores/
│   ├── __init__.py
│   ├── relational.py              # Repository 基类 + GrowthEvent CRUD + 去重
│   ├── semantic.py                # Cognee 封装（remember/recall/cognify）
│   └── documents.py               # 文件系统存储（简历等原始文件）
│
├── projections/
│   ├── __init__.py
│   ├── markdown.py                # SQLite → .md（原 md_projector.py）
│   └── snapshot.py                # Agent 系统提示快照
│
├── cognee_admin/
│   ├── __init__.py
│   ├── datasets.py                # dataset 常量定义
│   └── cognify_loop.py            # 后台 cognify 任务（原 cognee_client.py）
│
└── search.py                      # 统一搜索：FTS5 + Cognee + .md 子串
```

---

## 删除的旧文件

| 旧文件 | 迁移目标 | 说明 |
|---|---|---|
| `services/lumen_memory.py` | `memory/facade.py` | 门面层，精简后迁移 |
| `services/md_projector.py` | `memory/projections/markdown.py` | .md 投影器 |
| `services/cognee_projector.py` | 拆分 | 逻辑并入 facade + projections |
| `services/cognee_service.py` | `memory/stores/semantic.py` | Cognee 封装 |
| `services/growth_event_service.py` | `memory/stores/relational.py` | GrowthEvent 去重写入 |
| `services/memory_service.py` | `memory/search.py` + `memory/projections/snapshot.py` | .md 读写 + 子串搜索 |
| `services/memory_limits.py` | `memory/projections/markdown.py` 内 | 字符限制常量 |
| `services/memory_templates.py` | `memory/projections/markdown.py` 内 | 默认模板 |
| `agent/cognee_client.py` | `memory/cognee_admin/` | Cognee 初始化 + cognify loop |

---

## Facade API（保持兼容）

```python
class LumenMemory:
    # 写入
    async def remember(self, user_id, event_type, entity_type, entity_id, payload, source, *, db) -> GrowthEvent | None
    async def remember_batch(self, user_id, events, *, db) -> list[GrowthEvent]
    
    # 投影触发
    async def flush_projections(self, user_id, event_ids) -> None
    async def sync_projections(self, user_id, event_ids) -> None
    
    # 重建 / 补偿
    async def rebuild(self, user_id) -> dict
    async def compensate_cognee(self, user_id, limit) -> int
    
    # 读取
    async def recall(self, user_id, query, limit) -> list[MemoryItem]
    async def build_context(self, user_id, user_input) -> str
```

---

## 存储层接口

### relational.py

```python
class BaseRepository:
    def __init__(self, db: AsyncSession)
    async def create(self, **kwargs) -> Model
    async def get_by_id(self, id: int) -> Model | None
    async def list_by_user(self, user_id: str, **filters) -> list[Model]
    async def delete(self, id: int) -> bool

class GrowthEventRepository(BaseRepository):
    model = GrowthEvent
    async def create_with_dedup(self, user_id, event_type, entity_type, entity_id, payload, source) -> GrowthEvent | None
    async def get_pending_cognee_projection(self, user_id, limit) -> list[GrowthEvent]
    async def mark_projected(self, event_ids, projection_type: "md" | "cognee") -> None
```

### semantic.py

```python
class SemanticStore:
    async def ingest(self, content: str, doc_id: str, dataset: str) -> bool
    async def search(self, query: str, datasets: list[str], top_k: int) -> list[str]
    async def clear_index(self) -> bool
```

### documents.py

```python
class DocumentStore:
    def save(self, user_id: str, doc_type: str, filename: str, content: bytes) -> str
    def read(self, rel_path: str) -> bytes
    def delete(self, rel_path: str) -> bool
```

---

## 投影层接口

### projections/markdown.py

```python
async def sync_user_md_projection(user_id: str) -> bool
async def project_user_to_md(db: AsyncSession, user_id: str) -> bool
```

### projections/snapshot.py

```python
async def build_user_snapshot(user_id: str) -> str
# 读取 memory.md + skills.md + experiences.md，组装成 Agent 系统提示
```

---

## 搜索层接口

### search.py

```python
async def search_all(user_id: str, query: str, limit: int = 10) -> list[MemoryItem]
# 内部按优先级：Cognee 语义 → FTS5 全文 → .md 子串
```

---

## Cognee 运维

### cognee_admin/datasets.py

```python
DATASET_PROFILE = "lumen_profile"
DATASET_REFERENCE = "lumen_reference"
DATASET_REFLECTION = "lumen_reflection"
DATASET_CHAT = "lumen_chat"
ALL_DATASETS = [...]
```

### cognee_admin/cognify_loop.py

```python
def init_cognee() -> str
async def cognify_loop()
def get_cognee_status() -> str
def mark_needs_cognify()
```

---

## 数据流（重构后）

### 写入路径

```
Agent 工具 / Router
    ↓
facade.remember()
    ↓
stores/relational.py → GrowthEvent（去重写入 SQLite）
    ↓
facade.flush_projections()
    ├─ projections/markdown.py → .md 文件
    └─ stores/semantic.py → Cognee（后台异步）
```

### 读取路径

```
Agent 工具 / Router
    ↓
facade.recall() / facade.build_context()
    ↓
search.py
    ├─ stores/semantic.py → Cognee 语义搜索
    ├─ relational.py → FTS5 全文搜索
    └─ projections/snapshot.py → .md 文件读取
```

---

## 迁移步骤

### Step 1: 新建目录 + 基础设施
- [ ] `memory/cognee_admin/datasets.py` — dataset 常量
- [ ] `memory/cognee_admin/cognify_loop.py` — 从 `agent/cognee_client.py` 迁移
- [ ] `memory/stores/documents.py` — 文件存储

### Step 2: 存储层
- [ ] `memory/stores/relational.py` — Repository + 去重逻辑（从 `growth_event_service.py` 迁移）
- [ ] `memory/stores/semantic.py` — Cognee 封装（从 `cognee_service.py` 迁移）

### Step 3: 投影层
- [ ] `memory/projections/markdown.py` — 从 `md_projector.py` 迁移
- [ ] `memory/projections/snapshot.py` — 从 `lumen_memory.build_context()` 中提取静态部分

### Step 4: 搜索层
- [ ] `memory/search.py` — 统一搜索（从 `lumen_memory.recall()` 提取）

### Step 5: 门面层
- [ ] `memory/facade.py` — 组合所有层，保持 API 兼容

### Step 6: 交互层改 import
- [ ] `agent/pydantic_tools.py` — `from app.backend.memory import get_memory`
- [ ] `agent/pydantic_agent.py` — `from app.backend.memory import get_memory`
- [ ] `routers/memory.py` — `from app.backend.memory import get_memory`
- [ ] `main.py` — `from app.backend.memory.cognee_admin import init_cognee, cognify_loop`

### Step 7: 删除旧文件
- [ ] `services/lumen_memory.py`
- [ ] `services/md_projector.py`
- [ ] `services/cognee_projector.py`
- [ ] `services/cognee_service.py`
- [ ] `services/growth_event_service.py`
- [ ] `services/memory_service.py`
- [ ] `services/memory_limits.py`
- [ ] `services/memory_templates.py`
- [ ] `agent/cognee_client.py`

### Step 8: 验证
- [ ] ruff check 通过
- [ ] 启动服务正常
- [ ] Agent 对话正常
- [ ] 记忆搜索正常

---

## 注意事项

1. **FTS5 触发器重建** — routers/memory.py DELETE 端点的 FTS5 重建逻辑保留，移到 facade 或 router 中
2. **Frozen Snapshot 缓存** — facade 中的 `_static_cache` 保留，移到 projections/snapshot.py
3. **后台任务生命周期** — `_background_tasks` 集合保留，由 facade 管理
4. **去重 key 生成** — `growth_event_service.py` 中的 `_make_payload_hash` + `_make_dedupe_key` 原样保留
5. **legacy 格式兼容** — md_projector 中的 legacy payload 处理（field/content、memory_md blob）保留

---

## 时间预估

| 批次 | 内容 | 预估 |
|---|---|---|
| 1 | cognee_admin + stores/documents | 10 min |
| 2 | stores/relational + stores/semantic | 20 min |
| 3 | projections/markdown + projections/snapshot | 20 min |
| 4 | search.py | 15 min |
| 5 | facade.py | 20 min |
| 6 | 交互层改 import | 10 min |
| 7 | 删除旧文件 + 验证 | 15 min |
| **总计** | | **约 2 小时** |
