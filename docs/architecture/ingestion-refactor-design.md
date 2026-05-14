# 外部数据源摄入架构重构设计

## 1. 现状诊断

### 1.1 当前架构

```
DataSource (DB) → create_connector() → FilesystemConnector
                                              │
                                              ▼
                                       scan() / watch
                                              │
                                              ▼
                                       RawDocument (raw text)
                                              │
                                              ▼
                              IngestionPipeline._ingest_with_retry()
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
            IngestionStore               SQLite raw SQL              Cognee
            (JSON file)                  (external_items)            (semantic)
```

### 1.2 粗糙点清单

| 层级 | 问题 | 影响 |
|---|---|---|
| **连接层** | Connector 与 Parser 耦合 | 新增数据源必须重写解析逻辑 |
| | 仅支持 `.md/.txt/.markdown`，硬编码 | PDF、Word、网页等无法接入 |
| | 无 charset 检测，强制 UTF-8 | 中文 GBK 文件乱码 |
| | 无二进制文件检测 | 可能误读图片/压缩包 |
| | `MAX_CONTENT_CHARS` 截断在 Connector | 不同数据源无法自定义限制 |
| **解析层** | **不存在独立的解析层** | 无 frontmatter 提取、无结构解析 |
| | 无文档结构（headings、links） | Agent 无法理解文档大纲 |
| | 无内容分块（chunking）策略 | 长文档超出 LLM context window |
| | 无 metadata 标准化 | 每类文档 metadata 格式不一 |
| **管线层** | Pipeline 与 Persistence 耦合 | 无法单独测试写入逻辑 |
| | 逐文档写入，无 batching | 大量小文件时性能差 |
| | 无背压控制 | 内存可能爆炸 |
| | DB 与 Cognee 写入原子性缺失 | 可能 DB 成功但 Cognee 失败 |
| | `cleanup_deleted` 需两次全量扫描 | 大数据源时慢 |
| **持久层** | 使用原始 SQL 而非 ORM | 类型安全差，难维护 |
| | 内容截断散落在多处 | 50000 限制在 3 个文件出现 |
| | Cognee 索引同步耦合 | Cognee 挂掉导致整批失败 |
| **状态层** | IngestionStore 是独立 JSON 文件 | 与 DB 状态可能不一致 |
| | 线程锁非进程安全 | 多 worker 时状态错乱 |
| | 兼容性代码散落 | `cleanup_deleted` 中手动 pop 旧格式 |
| **监听层** | Watchdog 嵌入 Connector | 无法统一管控多个数据源 |
| | 无变更队列 | 高频修改时重复处理 |

---

## 2. 目标架构

### 2.1 分层设计

```
┌─────────────────────────────────────────────────────────────────────┐
│                    IngestionOrchestrator                             │
│         (生命周期管理：启动、停止、手动触发、定时任务)                   │
└─────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
         ┌──────────────┐   bytes   ┌──────────────────┐
         │  Connector   │──────────▶│  DocumentParser  │
         │  (连接层)     │           │    (解析层)        │
         │ - local_dir  │           │ - frontmatter    │
         │ - web_url    │           │ - markdown AST   │
         │ - github     │           │ - pdf text       │
         │ - s3         │           │ - html2text      │
         └──────────────┘           └──────────────────┘
                                              │ struct doc
                                              ▼
                      ┌──────────────────────────────────────┐
                      │          IngestionPipeline            │
                      │             (管线层)                  │
                      │                                      │
                      │  - batch buffer (100 条)             │
                      │  - backpressure (queue maxsize=1000) │
                      │  - error classification              │
                      │  - async memory queue                │
                      └──────────────────────────────────────┘
                                     │
               ┌─────────────────────┼───────────────────────┐
               ▼                     ▼                       ▼
         ┌──────────────────┐  ┌────────────┐  ┌────────────────────────┐
         │  SQLite (ORM)    │  │   FTS5     │  │  DocumentIndexProvider  │
         │  external_items  │  │ (trigger)  │  │     (注入，可插拔)        │
         │  ingestion_state │  └────────────┘  │                        │
         └──────────────────┘                  │ CogneeProvider         │
                                               │ LanceDBProvider        │
                                               │ HRRProvider            │
                                               │ NullProvider           │
                                               └────────────────────────┘
```

### 2.2 核心原则

1. **关注点分离**：Connector 只负责读取字节，Parser 负责解析结构，Pipeline 负责协调，Store 负责持久化
2. **插件化**：Parser 按 mime-type / extension 注册；DocumentIndexProvider 按后端类型注册
3. **批量处理**：Pipeline 缓冲 100 条或 5 秒后批量写入
4. **最终一致性**：记忆索引通过异步队列消费，不与 DB 事务绑定
5. **状态归一**：所有状态存入 SQLite，与 `external_items` 同事务
6. **错误分类**：可重试（网络超时）、可跳过（格式错误）、致命（配置错误）
7. **记忆层可插拔**：通过 DocumentIndexProvider ABC 隔离具体后端（Cognee、LanceDB、HRR 等），Pipeline 只调 `prefetch()` 和 `sync_document()`，完全不知道背后实现
8. **分块策略内部化**：Provider 自己决定怎么分块，Pipeline 和 Agent 都不感知

---

## 3. 详细设计

### 3.1 数据模型

#### RawBytes (Connector 输出)
```python
@dataclass
class RawBytes:
    data_source_id: str
    external_id: str
    uri: str
    content_bytes: bytes
    mime_type: str | None  # 由 connector 或 magic 检测
    metadata: dict[str, Any]
    last_modified: float
```

#### StructuredDocument (Parser 输出)
```python
@dataclass
class DocumentSection:
    level: int           # 标题层级 (h1=1, h2=2...)
    title: str
    content: str
    start_line: int
    end_line: int

@dataclass
class StructuredDocument:
    data_source_id: str
    external_id: str
    uri: str
    title: str
    content: str                    # 纯文本正文
    sections: list[DocumentSection] # 文档结构大纲
    metadata: dict[str, Any]        # frontmatter + file stat
    links: list[str]                # 内部/外部链接
    content_hash: str
```

### 3.2 Connector 层改造

**职责变更**：只读取原始字节，不做任何解析或截断。

```python
class DataSourceConnector(ABC):
    @abstractmethod
    async def scan(self) -> AsyncIterator[RawBytes]: ...

    @abstractmethod
    def start_watching(self, on_change: Callable[[RawBytes], Coroutine], ...) -> None: ...
```

`FilesystemConnector` 改动：
- 移除 `MAX_CONTENT_CHARS` 截断
- 返回 `RawBytes` 而非 `RawDocument`
- 增加 `python-magic` 做 mime-type 检测
- 增加 `charset-normalizer` 做编码检测
- 增加二进制文件过滤（mime-type 非 text/*）

### 3.3 Parser 层（新增）

```python
class DocumentParser(ABC):
    @abstractmethod
    def supports(self, mime_type: str, extension: str) -> bool: ...

    @abstractmethod
    def parse(self, raw: RawBytes) -> StructuredDocument: ...

# 注册表（Phase 2 实现 MarkdownParser + PlaintextParser；
# Phase 4 再增加 PdfParser、HtmlParser）
_PARSER_REGISTRY: list[DocumentParser] = [
    MarkdownParser(),      # .md / .markdown
    PlaintextParser(),     # .txt
    # PdfParser(),         # Phase 4
    # HtmlParser(),        # Phase 4
]
```

`MarkdownParser` 能力：
- 提取 YAML frontmatter
- 解析 AST：headings、lists、code blocks
- 生成 `DocumentSection` 树
- 收集内部链接 `[[...]]` 和外部链接

### 3.4 DocumentIndexProvider 层（可插拔语义索引后端）

**设计目标**：解耦 Pipeline 与具体向量存储后端，接口极简——Pipeline 只调 `prefetch()` 和 `sync_document()`，完全不知道背后是 Cognee、LanceDB、HRR 还是无存储。

**核心原则**：
- 分块策略完全内部化，外部不感知
- 召回结果拼装成字符串返回，不暴露底层格式
- Provider 可自主暴露工具给 Agent

#### 共享分块工具（module-level，所有 Provider 内部调用）

```python
def _chunk_text(text: str, max_chars: int = 2000, overlap: int = 200) -> list[str]:
    """按段落边界切分，超出 max_chars 时递归切分。
    overlap: 相邻 chunk 重叠字符数，保持上下文连续性。
    """
    ...
```

#### DocumentIndexProvider ABC

```python
from abc import ABC, abstractmethod
from typing import Any

class DocumentIndexProvider(ABC):
    """记忆提供者抽象基类。所有具体后端必须继承此类。
    
    接口极简：主循环只调 prefetch() 和 sync_document()，
    完全不知道背后是 Cognee、LanceDB 还是 HRR。
    分块策略由 Provider 内部决定，外部不感知。
    """
    
    @property
    @abstractmethod
    def name(self) -> str: ...
    
    @abstractmethod
    def is_available(self) -> bool: ...
    
    @abstractmethod
    def initialize(self) -> None: ...
    
    @abstractmethod
    async def prefetch(self, query: str) -> str:
        """召回相关内容，拼装成字符串返回给 LLM。

        返回格式统一为（方便 Agent 解析引用）：
            [来源: {doc_id}]\n{content}\n\n[来源: {doc_id}]\n{content}
        无相关结果时返回空字符串。
        """
        ...
    
    @abstractmethod
    async def sync_document(
        self, 
        content: str, 
        doc_id: str, 
        metadata: dict[str, Any] | None = None
    ) -> None:
        """保存文档内容。分块策略由 Provider 内部决定，外部不感知。"""
        ...
    
    def get_tool_schemas(self) -> list[dict]:
        """Provider 可以暴露自己的工具给 Agent。默认空列表。"""
        return []
    
    async def handle_tool_call(self, name: str, args: dict) -> str:
        """处理 Agent 的工具调用。默认抛异常。"""
        raise NotImplementedError(f"Tool {name} not supported by {self.name}")
```

#### CogneeProvider（默认）

```python
class CogneeProvider(DocumentIndexProvider):
    """Cognee 实现。内部自动分块、向量化、建图。
    
    分块策略：Cognee 内部处理，外部不感知。
    
    真实 API（经官方文档验证）：
    - cognee.add(data, dataset_name="main_dataset", ...)
    - cognee.cognify(datasets="...")  # 处理成知识图谱
    - cognee.search(query, query_type=SearchType, datasets="...", top_k=10)
    - cognee.recall(query)  # search 的别名，自动路由
    - cognee.remember(data)  # 一步完成 add + cognify
    - cognee.forget(dataset="...")  # 删除
    """
    
    @property
    def name(self) -> str:
        return "cognee"
    
    def is_available(self) -> bool:
        try:
            import cognee
            return True
        except ImportError:
            return False
    
    def initialize(self) -> None:
        pass
    
    async def prefetch(self, query: str) -> str:
        """召回并拼装成字符串。
        
        使用 cognee.recall()（search 的别名，自动路由最佳检索策略）。
        返回结果格式：对象列表，可遍历获取文本内容。
        """
        results = await cognee.recall(query, datasets=["default"], top_k=10)
        if not results:
            return ""
        return "\n\n".join([
            f"[来源: {getattr(r, 'doc_id', str(i+1))}]\n{str(r)}"
            for i, r in enumerate(results)
        ])
    
    async def sync_document(self, content: str, doc_id: str, metadata: dict | None = None) -> None:
        """Cognee 内部自动分块，无需外部干预。
        
        使用 cognee.add() 摄入文本，之后需调用 cognify() 处理。
        为简化，可使用 cognee.remember() 一步完成。
        """
        # 方式一：分步（更灵活）
        await cognee.add(
            data=content,
            dataset_name=metadata.get("dataset_name", "default"),
        )
        await cognee.cognify(
            datasets=metadata.get("dataset_name", "default"),
        )
        
        # 方式二：一步完成（推荐）
        # await cognee.remember(
        #     data=content,
        #     dataset_name=metadata.get("dataset_name", "default"),
        # )
    
    def get_tool_schemas(self) -> list[dict]:
        """暴露 data_source_search 工具给 Agent。"""
        return [
            {
                "name": "data_source_search",
                "description": "搜索外部数据源中的相关文档",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"}
                    },
                    "required": ["query"]
                }
            }
        ]
    
    async def handle_tool_call(self, name: str, args: dict) -> str:
        if name == "data_source_search":
            return await self.prefetch(args["query"])
        raise NotImplementedError(f"Tool {name} not supported")
```

#### LanceDBProvider（轻量替代，需 embedding）

```python
class LanceDBProvider(DocumentIndexProvider):
    """LanceDB 实现。内部处理 embedding 和分块。
    
    embedding_model 通过构造函数注入，支持本地模型（sentence-transformers）
    或远程 API（OpenAI embedding）。
    
    真实 API（经官方文档验证）：
    - table.search(query_vector).limit(n).to_list() -> list[dict]
    - 返回字段包含 _distance（L2 距离，值越小越相似）
    - table.add(data) 支持 list[dict]、DataFrame
    - 可配置 distance_type("cosine") 切换度量方式
    """
    
    def __init__(self, embedding_model: EmbeddingModel | None = None, db_path: str = ""):
        self._embedding = embedding_model or LocalEmbeddingModel()
        self._db_path = db_path
    
    @property
    def name(self) -> str:
        return "lancedb"
    
    def is_available(self) -> bool:
        try:
            import lancedb
            return True
        except ImportError:
            return False
    
    def initialize(self) -> None:
        pass
    
    async def prefetch(self, query: str) -> str:
        """向量搜索，拼装结果。
        
        LanceDB 返回 list[dict]，包含 _distance 字段（L2 距离）。
        注意：_distance 越小表示越相似，需转换为相似度分数展示。
        """
        query_vec = (await self._embedding.embed_batch([query]))[0]
        table = await self._get_table()
        results = await table.search(query_vec).limit(10).to_list()
        
        if not results:
            return ""
        
        return "\n\n".join([
            f"[来源: {r['doc_id']}]\n{r['text'][:500]}"
            for r in results
        ])
    
    async def sync_document(self, content: str, doc_id: str, metadata: dict | None = None) -> None:
        """内部自行分块和 embedding。分块策略外部不感知。"""
        chunks = self._chunk_text(content)
        vectors = await self._embedding.embed_batch(chunks)
        
        table = await self._get_table()
        await table.add(
            data=[{
                "vector": vec,
                "text": chunk,
                "doc_id": doc_id,
                "metadata": metadata,
            } for vec, chunk in zip(vectors, chunks)]
        )
    
        # _chunk_text 为 module-level 共享工具，见上文
```

#### HRRProvider（实验性，不依赖 embedding API）

```python
import numpy as np

class HRRProvider(DocumentIndexProvider):
    """Holographic Reduced Representation 实现（实验性）。
    
    不依赖任何 embedding API，文本直接编码成 HRR 向量。
    检索两阶段：FTS5 先过滤（BM25），HRR 向量重排。
    
    实现参考：
    - HoloVec 库（pip install holovec）：支持 FHRR/HRR/MAP 等 VSA 模型
    - 使用高维向量（2048-10000 维）进行绑定/解绑操作
    - 纯 NumPy 实现时零额外依赖
    
    注意：HRR 用于文档检索的精度未经充分验证，建议作为降级方案。
    """
    
    def __init__(self, dim: int = 4096, model: str = "FHRR"):
        """
        Args:
            dim: 向量维度（HoloVec 建议 2048-10000）
            model: VSA 模型（"FHRR" 为默认值，支持 "HRR"/"MAP"/"GHRR" 等）
        """
        self._dim = dim
        self._model_name = model
        self._model = None  # 懒加载
    
    def _init_model(self):
        """懒加载 HoloVec 模型。"""
        if self._model is None:
            from holovec import VSA
            self._model = VSA.create(self._model_name, dim=self._dim)
        return self._model
    
    @property
    def name(self) -> str:
        return "hrr"
    
    def is_available(self) -> bool:
        try:
            import holovec
            return True
        except ImportError:
            return False
    
    def initialize(self) -> None:
        # 初始化 SQLite 表：facts, entities, fact_entities, facts_fts
        pass
    
    def _encode_hrr(self, text: str) -> bytes:
        """将文本编码为 HRR 向量（NumPy 数组序列化）。"""
        # Holographic Reduced Representation 编码...
        # 使用随机向量 + 循环卷积绑定词向量
        ...
    
    def _decode_hrr(self, blob: bytes) -> np.ndarray:
        """反序列化 HRR 向量。"""
        return np.frombuffer(blob, dtype=np.float32)
    
    async def prefetch(self, query: str) -> str:
        """两阶段检索：FTS5 过滤 + HRR 重排。"""
        # 1. FTS5 粗排，取 top 30
        candidates = await self._fts5_search(query, limit=30)
        if not candidates:
            return ""
        
        # 2. HRR 向量重排
        query_vec = self._encode_hrr(query)
        scored = []
        for doc in candidates:
            doc_vec = self._decode_hrr(doc.hrr_vector)
            similarity = np.dot(query_vec, doc_vec)
            scored.append((similarity, doc))
        
        scored.sort(reverse=True)
        top = scored[:10]
        
        return "\n\n".join([
            f"[来源: {doc.doc_id}]\n{doc.content[:500]}"
            for _, doc in top
        ])
    
    async def sync_document(self, content: str, doc_id: str, metadata: dict | None = None) -> None:
        """分块、编码 HRR、写入 SQLite。分块策略内部化。"""
        chunks = self._chunk_text(content)
        for chunk in chunks:
            hrr_vec = self._encode_hrr(chunk)
            await self._insert_fact(
                content=chunk,
                doc_id=doc_id,
                hrr_vector=hrr_vec,
                metadata=metadata,
            )
    
        # _chunk_text 为 module-level 共享工具，见上文
    
    async def _fts5_search(self, query: str, limit: int) -> list[Fact]:
        ...
    
    async def _insert_fact(self, content: str, doc_id: str, hrr_vector: bytes, metadata: dict) -> None:
        ...
```

#### NullProvider（测试 + 无向量库模式）

```python
class NullProvider(DocumentIndexProvider):
    """什么都不做。用于测试或用户不想装任何向量库时降级。"""
    
    @property
    def name(self) -> str:
        return "null"
    
    def is_available(self) -> bool:
        return True
    
    def initialize(self) -> None:
        pass
    
    async def prefetch(self, query: str) -> str:
        return ""
    
    async def sync_document(self, content: str, doc_id: str, metadata: dict | None = None) -> None:
        pass
    
    def get_tool_schemas(self) -> list[dict]:
        return []
```

#### 插件加载（目录发现模式）

```python
import importlib
from pathlib import Path

_PROVIDER_REGISTRY: dict[str, type[DocumentIndexProvider]] = {}

def _discover_providers() -> None:
    """从 plugins/memory/ 目录自动发现 Provider 实现。
    
    目录结构：
    plugins/memory/
        cognee/
            __init__.py  # 导出 Provider = CogneeProvider
        lancedb/
            __init__.py  # 导出 Provider = LanceDBProvider
        hrr/
            __init__.py  # 导出 Provider = HRRProvider
    """
    plugins_dir = Path(__file__).parent / "plugins" / "memory"
    if not plugins_dir.exists():
        return
    
    for provider_dir in plugins_dir.iterdir():
        if not provider_dir.is_dir():
            continue
        
        init_file = provider_dir / "__init__.py"
        if not init_file.exists():
            continue
        
        module_name = f"plugins.memory.{provider_dir.name}"
        try:
            module = importlib.import_module(module_name)
            provider_cls = getattr(module, "Provider", None)
            if provider_cls and issubclass(provider_cls, DocumentIndexProvider):
                _PROVIDER_REGISTRY[provider_cls().name] = provider_cls
        except Exception as e:
            logger.warning(f"Failed to load provider {provider_dir.name}: {e}")

def create_document_index_provider(config: dict) -> DocumentIndexProvider:
    """根据配置创建 DocumentIndexProvider 实例。"""
    backend = config.get("document_index_provider", "cognee")
    
    # 首次调用时自动发现
    if not _PROVIDER_REGISTRY:
        _discover_providers()
    
    provider_cls = _PROVIDER_REGISTRY.get(backend)
    if not provider_cls:
        raise ValueError(f"Unknown memory provider: {backend}")
    
    return provider_cls(**config.get(backend, {}))
```

### 3.5 Pipeline 层改造

```python
class IngestionPipeline:
    def __init__(
        self,
        document_index_provider: DocumentIndexProvider,
        batch_size: int = 100,
        flush_interval: float = 5.0,
    ):
        self._memory = document_index_provider
        self._batch: list[StructuredDocument] = []
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        # maxsize=1000：队列满时 put() 挂起，对上游产生背压，防止内存爆炸
        self._memory_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    async def ingest_document(self, doc: StructuredDocument) -> None:
        self._batch.append(doc)
        if len(self._batch) >= self._batch_size:
            await self._flush_batch()
        # 记忆入队，不阻塞 DB 写入；队列满时自动背压
        await self._memory_queue.put(doc)

    async def _flush_batch(self) -> None:
        """批量 UPSERT，单事务。"""
        async with get_async_session_maker()() as db:
            # 使用 ORM bulk insert
            ...
            # 同时更新 ingestion_state 表
            ...
            await db.commit()
        
    async def _memory_worker(self) -> None:
        """后台消费记忆队列。"""
        while True:
            doc = await self._memory_queue.get()
            try:
                # Pipeline 不关心分块策略，只传完整文档
                await self._memory.sync_document(
                    content=doc.content,
                    doc_id=doc.external_id,
                    metadata=doc.metadata,
                )
            except Exception:
                logger.exception("memory_sync_failed", doc_id=doc.external_id)
```

**错误分类**：
```python
from dataclasses import dataclass

@dataclass
class IngestionError(Exception):
    message: str
    retryable: bool = False   # 网络超时 → 重试
    skip: bool = False        # 格式错误 → 跳过并记录
    
    def __str__(self) -> str:
        return f"[{type(self).__name__}] {self.message} (retryable={self.retryable}, skip={self.skip})"
```

### 3.6 状态层改造

移除 `IngestionStore` JSON 文件，改为 DB 表：

```sql
CREATE TABLE ingestion_state (
    data_source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,        -- indexed | failed | skipped
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    indexed_at TIMESTAMP,
    PRIMARY KEY (data_source_id, external_id)
);
```

优势：
- 与 `external_items` 同事务写入，强一致
- 支持多进程（DB 锁）
- 无需兼容性代码

### 3.7 记忆层解耦

Pipeline 通过 `DocumentIndexProvider` 接口与具体后端交互，不直接引用 `cognee` 模块：

```python
# pipeline.py —— 注入 provider，无硬编码后端
class IngestionPipeline:
    def __init__(self, document_index_provider: DocumentIndexProvider, ...):
        self._memory = document_index_provider

# main.py —— 启动时根据配置实例化
memory = create_document_index_provider(config.document_index_provider)
pipeline = IngestionPipeline(document_index_provider=memory, ...)
```

优势：
- **可测试**：单元测试可用 `NullProvider()` 注入
- **可切换**：改一行配置从 Cognee 切到 HRR，无需改 Pipeline
- **可降级**：记忆后端失败时，DB + FTS5 仍可正常搜索
- **无 embedding 依赖**：HRRProvider 无需 API Key，纯本地计算

### 3.8 监听层改造

`IngestionOrchestrator` 统一管理监听：

```python
class IngestionOrchestrator:
    def __init__(self):
        self._change_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._watchers: dict[str, DataSourceConnector] = {}

    def register_watcher(self, source_id: str, connector: DataSourceConnector):
        connector.start_watching(
            on_change=self._on_change,
            on_delete=self._on_delete,
            loop=asyncio.get_running_loop(),
        )
        self._watchers[source_id] = connector

    async def _on_change(self, raw: RawBytes):
        # 入队，由 consumer 控制速率
        await self._change_queue.put(("change", raw))

    async def _drain_queue(self, timeout: float) -> list[tuple[str, RawBytes]]:
        """Drain queue with timeout debounce.
        
        伪码逻辑：
        1. block 等待第一个元素（无 timeout）
        2. 启动 timer = timeout
        3. 在 timer 内非阻塞 poll 后续元素，直到 queue 空或 timer 到期
        4. 返回收集到的 batch
        """
        ...

    async def _change_consumer(self):
        # 批量消费，1.5s debounce
        while True:
            batch = await self._drain_queue(timeout=1.5)
            await self._process_batch(batch)
```

---

## 4. 迁移计划

### Phase 1：状态归一（低风险）
1. 创建 `ingestion_state` 表（主键 `(data_source_id, external_id)`）
2. 将 `IngestionStore` JSON 数据迁移至 DB
   - 旧格式 key 为裸路径（如 `"E:\\MyNote\\我的笔记\\file.md"`），无 `data_source_id` 前缀
   - 迁移策略：从路径反推 `data_source_id`（匹配最长的已知数据源根目录前缀）；
     若无法匹配，归入 `legacy` 数据源，标记 `status='migrated_legacy'`，留待人工清理
3. 修改 `pipeline.py` 读写 `ingestion_state` 表
4. 保留旧 JSON 文件作为备份，验证无误后删除

### Phase 2：Connector 与 Parser 分离（中风险）
1. 新增 `RawBytes`、`StructuredDocument` 模型
2. 创建 `DocumentParser` ABC 和 `MarkdownParser`
3. 改造 `FilesystemConnector` 返回 `RawBytes`
4. Pipeline 中增加 `parse()` 步骤
5. 回滚策略：如 Parser 失败，fallback 到纯文本

### Phase 3：批量写入 + DocumentIndexProvider 解耦（中风险）
1. Pipeline 增加 batch buffer
2. 改为 ORM bulk insert
3. 创建 `DocumentIndexProvider` ABC + `CogneeProvider`
4. 删除 `semantic_store.py`，其逻辑内联到 `CogneeProvider`（`prefetch`/`sync_document`/`clear`）
5. Pipeline 注入 DocumentIndexProvider，移除硬编码 Cognee 调用
6. 增加 `backpressure` 配置
7. Agent 工具从 DocumentIndexProvider 动态获取（`get_tool_schemas`）

### Phase 4：扩展格式 + 可选后端（高风险）
1. 增加 `PdfParser`、`HtmlParser`
2. 实现 `LanceDBProvider`（需 embedding 模型）
3. 实现 `HRRProvider`（无 embedding API 依赖）
4. 支持运行时切换记忆后端（配置热重载）
5. 前端适配：记忆召回结果统一格式展示

---

## 5. 预期收益

| 指标 | 现状 | 目标 |
|---|---|---|
| 新增数据源开发成本 | 2-3 天（重写解析） | 2-3 小时（只写 Connector） |
| 10k 文件首次同步时间 | ~5min（逐条写入） | ~30s（批量写入） |
| 状态一致性 | 弱（JSON + DB） | 强（单事务） |
| 记忆后端失败影响 | 整批失败 | 仅记忆搜索降级，DB+FTS5 正常 |
| 支持文件格式 | 3 种 | 10+ 种 |
| 单文档最大长度 | 50k chars | 无限制（记忆搜索路径：Provider 内部分块；FTS5 路径仍存整文档） |
| 高频修改稳定性 | 差（无队列） | 好（队列 + debounce） |

---

## 6. 决策点

**D1 — 记忆后端选型**

通过 `DocumentIndexProvider` 解耦后，默认后端与可选后端的权衡：

| 后端 | 分块策略 | 需 API Key | 本地部署 | 依赖体积 | 适用场景 |
|---|---|---|---|---|---|
| **Cognee** | 内部自动 | 否 | 是 | ~50MB | 默认，需要图谱推理 |
| **LanceDB** | 内部实现 | 是（embedding） | 是 | ~20MB | 轻量，有 API Key 时 |
| **HRR** | 内部实现 | **否** | 是 | ~5MB | 无网络、无 API Key 环境 |
| **Null** | 无 | 否 | 是 | 0MB | 测试、降级 |

**推荐：Cognee 作为默认后端**，HRR 作为无 embedding 依赖的轻量替代。通过 `MEMORY_PROVIDER=cognee/hrr/lancedb/null` 环境变量切换。

**D2 — 是否引入任务队列（Celery / RQ）**

| 方案 | 描述 | 适用场景 |
|---|---|---|
| A) asyncio Queue | 轻量，同进程 | 当前单用户模式足够 |
| B) Celery + Redis | 独立 worker，可横向扩展 | 多用户、大规模 |

**推荐：方案 A** — 保持单文件部署的简洁性。未来如有多用户需求再迁移。

**D3 — PDF/Word 解析依赖**

| 方案 | 依赖 | 体积 |
|---|---|---|
| A) PyMuPDF (fitz) | `PyMuPDF` | +20MB |
| B) pdfplumber | `pdfplumber` + `pdfminer.six` | +15MB |
| C) 外部服务调用 | 无本地依赖 | 需网络 |

**推荐：方案 A** — PyMuPDF 速度快、支持广，且也支持图片提取（未来需求）。

---

## 7. 相关文件变更清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `backend/modules/data_sources/ingestion/` | 重构 | 核心目录 |
| `connector.py` | 修改 | `RawDocument` → `RawBytes` |
| `connectors/local_folder.py` | 修改 | 返回 bytes，增加 mime/charset 检测 |
| `parser.py` | **新增** | Parser ABC + 注册表 |
| `parsers/markdown.py` | **新增** | Markdown 解析 |
| `document_index_provider.py` | **新增** | DocumentIndexProvider ABC + 插件发现 |
| `providers/cognee.py` | **新增** | CogneeProvider 实现 |
| `providers/lancedb.py` | **新增** | LanceDBProvider 实现（Phase 4） |
| `providers/hrr.py` | **新增** | HRRProvider 实现（Phase 4） |
| `pipeline.py` | 重写 | 批量写入、DocumentIndexProvider 注入、ORM |
| `store.py` | 删除 | 状态并入 DB |
| `models.py` | 修改 | 新增 `IngestionState` 模型 |
| `db_migrations.py` | 修改 | 新增表 + 迁移脚本 |
| `service.py` | 修改 | 使用新 Pipeline API |

---

## 8. 接入新数据源指南

### 情形 A：新来源，已有格式（如 GitHub 上的 .md 文件）

只需写一个 Connector，其他层不动：

```python
# backend/modules/data_sources/ingestion/connectors/github.py
class GithubConnector(DataSourceConnector):
    def __init__(self, repo: str, token: str, data_source_id: str):
        self._repo = repo
        self._token = token
        self._data_source_id = data_source_id

    async def scan(self) -> AsyncIterator[RawBytes]:
        # 调 GitHub API 枚举文件，yield RawBytes
        for file in await self._list_files():
            content = await self._fetch_file(file.path)
            yield RawBytes(
                data_source_id=self._data_source_id,
                external_id=file.sha,        # Git blob SHA 做 dedup key
                uri=file.html_url,
                content_bytes=content,
                mime_type="text/markdown",
                metadata={"path": file.path, "repo": self._repo},
                last_modified=file.last_modified_ts,
            )

    def start_watching(self, on_change, on_delete, loop): ...  # Webhook 或轮询
```

注册到 Orchestrator（在 `service.py` 的 `create_connector()` 工厂里加一个 case）：
```python
case "github":
    return GithubConnector(
        repo=config["repo"],
        token=config["token"],
        data_source_id=data_source.id,
    )
```

**Parser、Pipeline、DocumentIndexProvider 一行不用改。** 文件扫到之后走现有的 MarkdownParser → IngestionPipeline → SQLite + FTS5 + Provider 路径。

---

### 情形 B：新来源，新格式（如 Notion 富文本）

需要 Connector + Parser，共两步：

**Step 1** — 写 Connector（同情形 A，返回 `mime_type="application/notion+json"`）

**Step 2** — 写 Parser：

```python
# backend/modules/data_sources/ingestion/parsers/notion.py
class NotionParser(DocumentParser):
    def supports(self, mime_type: str, extension: str) -> bool:
        return mime_type == "application/notion+json"

    def parse(self, raw: RawBytes) -> StructuredDocument:
        data = json.loads(raw.content_bytes)
        # 解析 Notion block tree → 纯文本 + sections
        content, sections = _parse_notion_blocks(data["blocks"])
        return StructuredDocument(
            data_source_id=raw.data_source_id,
            external_id=raw.external_id,
            uri=raw.uri,
            title=data.get("title", "未命名"),
            content=content,
            sections=sections,
            metadata={**raw.metadata, "notion_id": data["id"]},
            links=[b["url"] for b in data["blocks"] if b["type"] == "link"],
            content_hash=hashlib.sha256(raw.content_bytes).hexdigest(),
        )
```

在 `_PARSER_REGISTRY` 中注册：
```python
_PARSER_REGISTRY.append(NotionParser())
```

**Pipeline、DocumentIndexProvider 不用改。**

---

### 开发成本对比

| 情形 | 需要写的代码 | 工作量 |
|---|---|---|
| 新来源 + 已有格式 | 1 个 Connector | ~2 小时 |
| 新来源 + 新格式 | 1 个 Connector + 1 个 Parser | ~4 小时 |
| 新索引后端 | 1 个 DocumentIndexProvider 实现 | ~1 天（含测试） |
| 现在（无分层） | Connector 内重写全部逻辑 | 2-3 天 |

---

*Design doc v3 revised. Ready for Phase 1 implementation upon approval.*
