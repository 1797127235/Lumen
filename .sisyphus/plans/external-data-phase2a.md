# Phase 2a：外部数据接入（本地文件系统 + FTS5）

## TL;DR

> **目标**：让 Lumen 能搜索到用户本地 Markdown 笔记（Obsidian 等）
>
> **核心原则**：
> - 新增 `external_items` 表（独立于 `growth_events`，互不干扰）
> - DataSourceConnector 抽象基类隔离变化，Phase 2b/2c 复用同一接口
> - `recall()` 保持向后兼容，新增 `source_scope` 参数控制数据源范围，避免和现有 `scope=profile/emotions/reference/chat` 语义冲突
> - 不引入 MCP 服务进程（Phase 2a 直接用 Python 文件扫描）
>
> **预计工作量**：Medium（5-8 小时）
> **并行执行**：YES — 3 个 Wave

---

## Context

### 现有架构

- **双管线记忆**：Profile 事件（→ .md 投影）+ Narrative 事件（→ FTS5 `growth_events_fts`）
- **召回入口**：`LumenMemory.recall()` → `search_all()` → `_search_fts5()` / `_search_cognee()`
- **工具系统**：`ToolRegistry` + `ToolDispatcher` + `ToolDefinition`，builtin 工具在 `backend/agent/tools/builtin/`
- **配置**：`backend/config.py` 读 `.env`，已有 `get_settings()` 单例

### 要解决的问题

用户的 Obsidian 笔记、技术文档散布在本地文件系统，Lumen 无法感知。需要一条独立的"外部数据管线"，扫描配置的目录，将文件内容索引到 FTS5，并通过 `recall()` 统一召回。

### 参考项目

`E:\OpenHub\hermes-agent` 的以下模式直接可用：
- `plugins/teams_pipeline/store.py` — 线程安全 JSON store + 原子写入
- `agent/retry_utils.py` — Jittered 指数退避

---

## 新增文件结构

```
backend/
  ingestion/
    __init__.py            # 导出 IngestionPipeline, get_pipeline
    connector.py           # DataSourceConnector ABC + RawDocument
    pipeline.py            # IngestionPipeline（扫描 → 索引状态机）
    store.py               # IngestionStore（JSON 原子写入，dedup 状态）
    retry.py               # jittered_retry 装饰器
    connectors/
      __init__.py
      filesystem.py        # FilesystemConnector（.md/.txt 扫描 + watchdog）
```

---

## Wave 1：基础设施（不涉及现有文件）

### Task 1.1 — `backend/ingestion/connector.py`

新建文件，定义核心数据模型和抽象接口：

```python
"""外部数据源连接器基类。"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class RawDocument:
    """从外部数据源读取的原始文档。"""
    source_id: str        # "filesystem" / "github"
    doc_id: str           # 文档唯一标识（文件绝对路径 或 "owner/repo/path"）
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # title, tags, last_modified 等

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


class DataSourceConnector(ABC):
    """外部数据源连接器抽象基类。
    
    每种数据源（filesystem / github / web）实现此接口，
    核心 Pipeline 不感知具体来源。
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """数据源唯一标识。"""

    @abstractmethod
    def is_configured(self) -> bool:
        """是否已配置（有效的路径 / token）。"""

    @abstractmethod
    async def scan(self) -> AsyncIterator[RawDocument]:
        """全量扫描，返回所有文档。启动时和手动触发时调用。"""

    @abstractmethod
    def start_watching(
        self,
        on_change: Callable[[RawDocument], Coroutine],
        on_delete: Callable[[str, str], Coroutine],
        *,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """启动增量监听。on_change 处理新增/修改，on_delete 处理删除/重命名。"""

    @abstractmethod
    def stop_watching(self) -> None:
        """停止监听。"""
```

**注意**：`Callable` 和 `Coroutine` 需要从 `collections.abc` 导入。

---

### Task 1.2 — `backend/ingestion/store.py`

线程安全 JSON store，追踪哪些文档已被索引（hermes `TeamsPipelineStore` 模式）：

```python
"""摄入状态 store — 线程安全 JSON 原子写入。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class IngestionStore:
    """追踪外部文档的索引状态。
    
    状态结构：
      indexed: {doc_id: {hash, indexed_at, source_id}}
      failed:  {doc_id: {reason, retry_count, last_attempt}}
      last_scan: {source_id: ISO timestamp}
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"indexed": {}, "failed": {}, "last_scan": {}}
        if path.exists():
            self._state = json.loads(path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        """原子写入：tempfile → replace，防止写入中途崩溃。"""
        with NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=self.path.parent, suffix=".tmp", delete=False
        ) as tmp:
            json.dump(self._state, tmp, ensure_ascii=False, indent=2)
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.path)

    def is_indexed(self, doc_id: str, content_hash: str) -> bool:
        with self._lock:
            entry = self._state["indexed"].get(doc_id)
            return entry is not None and entry.get("hash") == content_hash

    def mark_indexed(self, doc_id: str, content_hash: str, source_id: str) -> None:
        from datetime import UTC, datetime
        with self._lock:
            self._state["indexed"][doc_id] = {
                "hash": content_hash,
                "indexed_at": datetime.now(UTC).isoformat(),
                "source_id": source_id,
            }
            self._state["failed"].pop(doc_id, None)
            self._save()

    def mark_failed(self, doc_id: str, reason: str) -> None:
        from datetime import UTC, datetime
        with self._lock:
            entry = self._state["failed"].get(doc_id, {"retry_count": 0})
            entry["reason"] = reason
            entry["retry_count"] = entry.get("retry_count", 0) + 1
            entry["last_attempt"] = datetime.now(UTC).isoformat()
            self._state["failed"][doc_id] = entry
            self._save()

    def get_retry_count(self, doc_id: str) -> int:
        with self._lock:
            return self._state["failed"].get(doc_id, {}).get("retry_count", 0)

    def set_last_scan(self, source_id: str) -> None:
        from datetime import UTC, datetime
        with self._lock:
            self._state["last_scan"][source_id] = datetime.now(UTC).isoformat()
            self._save()
```

store 文件路径：`~/.lumen/ingestion_state.json`（复用 `backend.config.USER_DATA_DIR`，不要新建 `~/.career-os` 目录）。

---

### Task 1.3 — `backend/ingestion/retry.py`

Jittered 指数退避（仿 hermes `retry_utils.py`，58 行）：

```python
"""Jittered 指数退避 — 防止并发重试风暴。"""
from __future__ import annotations

import asyncio
import random


async def jittered_sleep(attempt: int, base: float = 5.0, max_delay: float = 120.0, jitter: float = 0.5) -> None:
    """在重试前等待带抖动的退避时间。
    
    公式：min(base * 2^(attempt-1), max_delay) + random(0, jitter_ratio * delay)
    """
    delay = min(base * (2 ** (attempt - 1)), max_delay)
    delay += random.uniform(0, jitter * delay)
    await asyncio.sleep(delay)
```

---

### Task 1.4 — `backend/ingestion/connectors/filesystem.py`

文件系统连接器，**复用 `file_security.py` 的大小检查和二进制检测**，不重复实现：

```python
"""本地文件系统连接器 — 扫描 .md / .txt 文件。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path

from backend.agent.tools.file_security import DEFAULT_MAX_READ_CHARS, check_size_limits, is_binary_file
from backend.ingestion.connector import DataSourceConnector, RawDocument
from backend.logging_config import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown"}


class FilesystemConnector(DataSourceConnector):
    """扫描本地目录中的 Markdown / 文本文件。"""

    source_id = "filesystem"

    def __init__(self, directories: list[str]) -> None:
        self._dirs = [Path(d) for d in directories]
        self._observer = None  # watchdog Observer，延迟初始化

    def is_configured(self) -> bool:
        return any(d.is_dir() for d in self._dirs)

    async def scan(self) -> AsyncIterator[RawDocument]:
        for directory in self._dirs:
            if not directory.is_dir():
                logger.warning("ingestion.filesystem.dir_not_found", path=str(directory))
                continue
            for file_path in directory.rglob("*"):
                # 跳过隐藏目录（.obsidian、.git 等）
                if any(part.startswith(".") for part in file_path.parts):
                    continue
                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                doc = self._read_file(file_path)
                if doc:
                    yield doc
                await asyncio.sleep(0)  # 让出事件循环，避免阻塞

    def _read_file(self, path: Path) -> RawDocument | None:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")

            # 复用 file_security：二进制检测
            is_bin, _ = is_binary_file(path, content)
            if is_bin:
                return None

            content = content.strip()
            if not content:
                return None

            # 复用 file_security：大小检查，超限截断而非跳过
            size_err = check_size_limits(path.stat().st_size, len(content))
            if size_err:
                logger.debug("ingestion.filesystem.file_truncated", path=str(path))
                content = content[:DEFAULT_MAX_READ_CHARS]

            return RawDocument(
                source_id=self.source_id,
                doc_id=str(path.resolve()),
                content=content,
                metadata={
                    "title": path.stem,
                    "extension": path.suffix,
                    "last_modified": path.stat().st_mtime,
                },
            )
        except Exception as exc:
            logger.warning("ingestion.filesystem.read_error", path=str(path), error=str(exc))
            return None

    def start_watching(
        self,
        on_change: Callable[[RawDocument], Coroutine],
        on_delete: Callable[[str, str], Coroutine],
        *,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """启动 watchdog 文件监听。需要 `pip install watchdog`。

        Args:
            on_change: 文件新增/修改时的回调，参数为 RawDocument。
            on_delete: 文件删除时的回调，参数为 (source_id, doc_id)。
            loop:      主线程的事件循环，由调用方显式传入（不在 watchdog 线程内调用 get_event_loop）。
        """
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning("ingestion.filesystem.watchdog_not_installed")
            return

        connector = self
        DEBOUNCE_SECONDS = 1.5  # Obsidian 自动保存会连续触发多次，合并处理

        class _Handler(FileSystemEventHandler):
            def __init__(self) -> None:
                self._timers: dict[str, asyncio.TimerHandle] = {}

            def _is_supported(self, path: str) -> bool:
                return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

            def _schedule_change(self, src_path: str) -> None:
                """防抖：DEBOUNCE_SECONDS 内的重复事件只处理最后一次。"""
                handle = self._timers.pop(src_path, None)
                if handle:
                    handle.cancel()

                def _fire():
                    self._timers.pop(src_path, None)
                    doc = connector._read_file(Path(src_path))
                    if doc:
                        asyncio.run_coroutine_threadsafe(on_change(doc), loop)

                self._timers[src_path] = loop.call_later(DEBOUNCE_SECONDS, _fire)

            def on_modified(self, event):
                if not event.is_directory and self._is_supported(event.src_path):
                    self._schedule_change(event.src_path)

            def on_created(self, event):
                if not event.is_directory and self._is_supported(event.src_path):
                    self._schedule_change(event.src_path)

            def on_deleted(self, event):
                if not event.is_directory and self._is_supported(event.src_path):
                    doc_id = str(Path(event.src_path).resolve())
                    asyncio.run_coroutine_threadsafe(
                        on_delete(connector.source_id, doc_id), loop
                    )

            def on_moved(self, event):
                # 重命名 = 旧路径删除 + 新路径新增
                if not event.is_directory:
                    if self._is_supported(event.src_path):
                        doc_id = str(Path(event.src_path).resolve())
                        asyncio.run_coroutine_threadsafe(
                            on_delete(connector.source_id, doc_id), loop
                        )
                    if self._is_supported(event.dest_path):
                        self._schedule_change(event.dest_path)

        self._observer = Observer()
        handler = _Handler()
        for directory in self._dirs:
            if directory.is_dir():
                self._observer.schedule(handler, str(directory), recursive=True)
        self._observer.start()
        logger.info("ingestion.filesystem.watching", dirs=[str(d) for d in self._dirs])

    def stop_watching(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None

---

## Wave 2：数据库表 + Pipeline 核心

### Task 2.1 — 新增 `external_items` 表

**修改文件**：`backend/db_migrations.py` 的 `migrate_sqlite()`。

本项目已经把 SQLite 兼容迁移、FTS5 表、触发器集中放在 `backend/db_migrations.py`。外部数据表也应放在这里，`main.py` 只负责调用 `migrate_sqlite(conn)`。

在 `migrate_sqlite()` 的 SQL 列表末尾追加以下幂等 DDL：

```python
"""CREATE TABLE IF NOT EXISTS external_items (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    content TEXT,
    content_hash TEXT,
    metadata_json TEXT,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, doc_id)
)""",
"""CREATE INDEX IF NOT EXISTS ix_external_items_source_doc
    ON external_items (source_id, doc_id)""",
"""CREATE VIRTUAL TABLE IF NOT EXISTS external_items_fts USING fts5(
    content
)""",
"""CREATE TRIGGER IF NOT EXISTS trg_external_items_ai AFTER INSERT ON external_items BEGIN
    INSERT INTO external_items_fts(rowid, content)
    VALUES (new.rowid, new.content);
END""",
"""CREATE TRIGGER IF NOT EXISTS trg_external_items_ad AFTER DELETE ON external_items BEGIN
    DELETE FROM external_items_fts WHERE rowid = old.rowid;
END""",
"""CREATE TRIGGER IF NOT EXISTS trg_external_items_au AFTER UPDATE ON external_items BEGIN
    DELETE FROM external_items_fts WHERE rowid = old.rowid;
    INSERT INTO external_items_fts(rowid, content)
    VALUES (new.rowid, new.content);
END""",
"""INSERT INTO external_items_fts(rowid, content)
    SELECT rowid, content FROM external_items
    WHERE rowid NOT IN (SELECT rowid FROM external_items_fts)""",
"""CREATE VIRTUAL TABLE IF NOT EXISTS external_items_fts_trigram USING fts5(
    content,
    tokenize='trigram'
)""",
"""CREATE TRIGGER IF NOT EXISTS trg_external_items_tri_ai AFTER INSERT ON external_items BEGIN
    INSERT INTO external_items_fts_trigram(rowid, content)
    VALUES (new.rowid, new.content);
END""",
"""CREATE TRIGGER IF NOT EXISTS trg_external_items_tri_ad AFTER DELETE ON external_items BEGIN
    DELETE FROM external_items_fts_trigram WHERE rowid = old.rowid;
END""",
"""CREATE TRIGGER IF NOT EXISTS trg_external_items_tri_au AFTER UPDATE ON external_items BEGIN
    DELETE FROM external_items_fts_trigram WHERE rowid = old.rowid;
    INSERT INTO external_items_fts_trigram(rowid, content)
    VALUES (new.rowid, new.content);
END""",
"""INSERT INTO external_items_fts_trigram(rowid, content)
    SELECT rowid, content FROM external_items
    WHERE rowid NOT IN (SELECT rowid FROM external_items_fts_trigram)""",
```

**原因**：SQLite FTS5 虚拟表不支持普通表那种 `ON CONFLICT DO UPDATE`。不要在 Pipeline 里手动 upsert FTS 表；Pipeline 只写 `external_items`，FTS 同步交给触发器。

**验证过的约束**：这里的 FTS 表是普通 FTS5 表，不是 `content=external_items` 外部内容表；删除/更新时使用 `DELETE FROM external_items_fts WHERE rowid = old.rowid`。如果改成 external-content FTS5，则 trigger 写法要整体调整。Trigram 查询建议用至少 3 个字符的中文关键词验证，例如 `"中文测"`。

---

### Task 2.2 — `backend/ingestion/pipeline.py`

**这是核心文件**，实现扫描 → dedup → 写入 FTS5 的完整状态机：

```python
"""摄入管线 — 驱动 DataSourceConnector 写入 external_items FTS5 表。"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import text

from backend.db import get_async_session_maker
from backend.ingestion.connector import DataSourceConnector, RawDocument
from backend.ingestion.retry import jittered_sleep
from backend.ingestion.store import IngestionStore
from backend.logging_config import get_logger

logger = get_logger(__name__)

MAX_RETRY = 3


class IngestionPipeline:
    """协调多个 DataSourceConnector，将文档写入 external_items。
    
    状态机（每个文档）：
        discovered → dedup_check → indexing → completed
                                             → failed → retrying
    """

    def __init__(self, store_path: Path) -> None:
        self._store = IngestionStore(store_path)
        self._connectors: list[DataSourceConnector] = []
        self._running = False

    def register(self, connector: DataSourceConnector) -> None:
        """注册一个数据源连接器。"""
        self._connectors.append(connector)

    async def run_full_scan(self) -> dict[str, int]:
        """全量扫描所有已配置的连接器，返回 {source_id: indexed_count}。"""
        summary: dict[str, int] = {}
        for connector in self._connectors:
            if not connector.is_configured():
                logger.info("ingestion.skip_unconfigured", source=connector.source_id)
                continue
            count = await self._scan_connector(connector)
            summary[connector.source_id] = count
            self._store.set_last_scan(connector.source_id)
        return summary

    async def _scan_connector(self, connector: DataSourceConnector) -> int:
        count = 0
        async for doc in connector.scan():
            ok = await self._ingest_with_retry(doc)
            if ok:
                count += 1
        logger.info("ingestion.scan_done", source=connector.source_id, indexed=count)
        return count

    async def _ingest_with_retry(self, doc: RawDocument) -> bool:
        """带 jittered 重试的单文档摄入。"""
        if self._store.is_indexed(doc.doc_id, doc.content_hash):
            return False  # 内容未变，跳过

        for attempt in range(1, MAX_RETRY + 1):
            try:
                await self._write_to_db(doc)
                self._store.mark_indexed(doc.doc_id, doc.content_hash, doc.source_id)
                return True
            except Exception as exc:
                logger.warning(
                    "ingestion.write_failed",
                    doc_id=doc.doc_id,
                    attempt=attempt,
                    error=str(exc),
                )
                self._store.mark_failed(doc.doc_id, str(exc))
                if attempt < MAX_RETRY:
                    await jittered_sleep(attempt)
        return False

    async def _write_to_db(self, doc: RawDocument) -> None:
        """UPSERT 文档到 external_items。FTS5 由 SQLite trigger 同步。"""
        item_id = f"{doc.source_id}:{uuid.uuid5(uuid.NAMESPACE_URL, doc.doc_id)}"
        metadata_json = json.dumps(doc.metadata, ensure_ascii=False)

        async with get_async_session_maker()() as db:
            # UPSERT（ON CONFLICT 更新内容）
            await db.execute(text("""
                INSERT INTO external_items (id, source_id, doc_id, content, content_hash, metadata_json)
                VALUES (:id, :source_id, :doc_id, :content, :hash, :meta)
                ON CONFLICT(source_id, doc_id) DO UPDATE SET
                    content = excluded.content,
                    content_hash = excluded.content_hash,
                    metadata_json = excluded.metadata_json,
                    indexed_at = CURRENT_TIMESTAMP
            """), {
                "id": item_id,
                "source_id": doc.source_id,
                "doc_id": doc.doc_id,
                "content": doc.content[:50000],  # 截断超长文档
                "hash": doc.content_hash,
                "meta": metadata_json,
            })
            await db.commit()

    async def handle_change(self, doc: RawDocument) -> None:
        """文件监听回调：单个文件变更时调用。"""
        logger.info("ingestion.file_changed", doc_id=doc.doc_id)
        await self._ingest_with_retry(doc)

    async def handle_delete(self, source_id: str, doc_id: str) -> None:
        """文件删除回调：从 external_items 移除并清理 store。"""
        async with get_async_session_maker()() as db:
            await db.execute(
                text("DELETE FROM external_items WHERE source_id=:sid AND doc_id=:did"),
                {"sid": source_id, "did": doc_id},
            )
            await db.commit()
        with self._store._lock:
            self._store._state["indexed"].pop(doc_id, None)
            self._store._state["failed"].pop(doc_id, None)
            self._store._save()
        logger.info("ingestion.deleted", source_id=source_id, doc_id=doc_id)

    def start_watching_all(self) -> None:
        """启动所有连接器的增量监听。loop 在主线程获取，显式传入 watchdog 线程。"""
        loop = asyncio.get_event_loop()
        for connector in self._connectors:
            if connector.is_configured():
                connector.start_watching(self.handle_change, self.handle_delete, loop=loop)

    def stop_watching_all(self) -> None:
        for connector in self._connectors:
            connector.stop_watching()


# 全局单例
_pipeline: IngestionPipeline | None = None


def get_pipeline() -> IngestionPipeline:
    global _pipeline
    assert _pipeline is not None, "IngestionPipeline 未初始化"
    return _pipeline


def init_pipeline(store_dir: Path) -> IngestionPipeline:
    global _pipeline
    _pipeline = IngestionPipeline(store_dir / "ingestion_state.json")
    return _pipeline
```

**重要**：不要对 FTS5 虚拟表使用 `ON CONFLICT(rowid) DO UPDATE`，SQLite 会报 `UPSERT not implemented for virtual table`。新增/更新/删除 `external_items` 后，FTS5 同步统一依赖 `db_migrations.py` 中的 trigger。实现后必须用单元测试验证普通英文搜索和中文 trigram 搜索都能命中新写入的文档。

---

### Task 2.3 — `backend/ingestion/__init__.py`

```python
from backend.ingestion.pipeline import IngestionPipeline, get_pipeline, init_pipeline

__all__ = ["IngestionPipeline", "get_pipeline", "init_pipeline"]
```

---

## Wave 3：配置、搜索扩展、工具注册、Startup

### Task 3.1 — 配置扩展

**修改文件**：`backend/config.py`

先把 import 改为：

```python
from pydantic import Field, field_validator
```

在 `Settings` 类中新增：

```python
# 外部数据接入
external_data_enabled: bool = False
external_data_dirs: list[str] = Field(default_factory=list)
# 格式：逗号分隔的目录路径，如 "C:\Obsidian,C:\Notes"

@field_validator("external_data_dirs", mode="before")
@classmethod
def parse_dirs(cls, v):
    if isinstance(v, str):
        return [d.strip() for d in v.split(",") if d.strip()]
    return v
```

对应 `.env` 变量（只需在 `.env.example` 里加注释说明）：
```
EXTERNAL_DATA_ENABLED=false
EXTERNAL_DATA_DIRS=
```

开发验收时使用的真实目录（勿写入代码，仅供本地测试）：
```
EXTERNAL_DATA_ENABLED=true
EXTERNAL_DATA_DIRS=E:\MyNote\我的笔记
```

该目录为 Obsidian vault，含 `.obsidian/` 配置目录（应被跳过）和中文路径，可作为验收环境。

---

### Task 3.2 — 搜索层扩展

**修改文件**：`backend/memory/search.py`

1. 在文件末尾新增 `_search_external_fts5` 函数（与现有 `_search_fts5` 同层级，不改现有函数）：

```python
async def _search_external_fts5(query: str, limit: int, seen: set[str]) -> list[MemoryItem]:
    """FTS5 全文搜索 external_items — Phase 2 外部数据。"""
    results: list[MemoryItem] = []
    safe_query = _escape_fts5(query)
    if not safe_query:
        return results
    try:
        fts_table = "external_items_fts_trigram" if _CJK_RE.search(query) else "external_items_fts"
        async with get_async_session_maker()() as db:
            rows = (
                await db.execute(
                    text(f"""
                        SELECT ei.id, ei.content, ei.source_id, ei.doc_id, ei.indexed_at
                        FROM external_items ei
                        JOIN {fts_table} fts ON fts.rowid = ei.rowid
                        AND {fts_table} MATCH :q
                        ORDER BY ei.indexed_at DESC
                        LIMIT :lim
                    """),
                    {"q": safe_query, "lim": limit},
                )
            ).all()

            for row in rows:
                eid = f"ext:{row[0]}"
                if eid in seen:
                    continue
                seen.add(eid)
                created_at = row[4]
                results.append(
                    MemoryItem(
                        id=eid,
                        content=row[1][:500] if row[1] else "",
                        created_at=created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                        categories=[f"external:{row[2]}"],
                    )
                )
    except Exception as exc:
        logger.warning("external_fts5 search failed", error=str(exc))
    return results
```

注意：`tokenize='trigram'` 对中文短词有天然限制，2 个字符的查询可能无法命中；测试用例应覆盖 3 个及以上中文字符。后续如果要支持 1-2 字中文短词，需要额外 fallback 到 `LIKE` 或自定义分词策略。

2. 修改 `search_all` 函数签名，新增 `source_scope` 参数（向后兼容，默认 `"narrative"`）。不要使用 `scope`，因为当前工具层已经把 `scope` 用作 `profile / emotions / reference / chat` 的 Cognee dataset 过滤。

```python
async def search_all(
    user_id: str,
    query: str,
    limit: int = 10,
    *,
    datasets: list[str] | None = None,
    include_cognee: bool = False,
    source_scope: str = "narrative",  # "narrative" | "external" | "all"
) -> list[MemoryItem]:
```

函数体中按 `source_scope` 分别执行 Narrative 和 External 搜索：

```python
    if source_scope in ("narrative", "all"):
        results.extend(await _search_fts5(user_id, query, limit, seen))

    if source_scope in ("external", "all"):
        results.extend(await _search_external_fts5(query, limit, seen))

    return results[:limit]
```

---

### Task 3.3 — `recall()` 扩展

**修改文件**：`backend/memory/searcher.py`

在 `recall()` 方法签名新增 `source_scope` 参数（向后兼容）：

```python
async def recall(
    self,
    user_id: str,
    query: str,
    limit: int = 10,
    datasets: list[str] | None = None,
    *,
    search_mode: str = "keyword",
    time_filter: str | None = None,
    source_scope: str = "narrative",  # 新增，透传给 search_all
) -> list[MemoryItem]:
```

在 `keyword` 分支：
```python
    return await search_all(user_id, query, limit=limit, datasets=datasets, source_scope=source_scope)
```

现有 `handle_memory_search()` 的 `scope` 参数继续只表示 dataset scope，不要改成外部数据 scope。外部文档优先通过独立工具 `search_external_docs` 调用；如后续要让 `memory_search` 支持外部数据，应新增参数名 `source_scope`。

---

### Task 3.4 — 工具注册 `search_external_docs`

按照项目现有模式：handler 函数 → `builtin/__init__.py` 导出 → `factory.py` 内联注册。

**新建文件**：`backend/agent/tools/builtin/external.py`

```python
"""外部文档搜索工具 Handler。"""
from __future__ import annotations

from typing import Any

from backend.agent.tools.core.context import ToolRuntimeContext
from backend.logging_config import get_logger
from backend.memory.search import _search_external_fts5

logger = get_logger(__name__)


async def handle_search_external_docs(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """搜索用户本地外部文档（Obsidian 笔记等）。"""
    query = args.get("query", "").strip()
    limit = min(int(args.get("limit", 5)), 10)
    if not query:
        return "[工具错误] 请提供搜索关键词。"
    results = await _search_external_fts5(query, limit, set())
    if not results:
        return "未找到相关外部文档。"
    lines = [f"找到 {len(results)} 条外部文档："]
    for item in results:
        source = item.categories[0] if item.categories else "unknown"
        lines.append(f"[{source}] {item.content[:200]}")
    return "\n".join(lines)
```

**修改文件**：`backend/agent/tools/builtin/schemas.py`，在末尾追加：

```python
class SearchExternalDocsArgs(TypedDict):
    """search_external_docs 工具的输入参数。"""

    query: str
    limit: NotRequired[int]
```

**修改文件**：`backend/agent/tools/builtin/__init__.py`，追加导出：

```python
from backend.agent.tools.builtin.external import handle_search_external_docs

# __all__ 中追加：
"handle_search_external_docs",
```

**修改文件**：`backend/agent/tools/core/factory.py` 的 `create_tool_runtime()` 函数

在记忆工具注册块之后新增：

```python
    # ── 注册外部文档工具 ──
    from backend.config import get_settings
    if get_settings().external_data_enabled:
        registry.register(
            ToolDefinition(
                name="search_external_docs",
                description=(
                    "搜索用户本地文档（Obsidian 笔记、Markdown 文件等）。"
                    "当用户提到某个技术、项目或想法，但对话记忆中找不到时，"
                    "可用此工具搜索外部笔记。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "limit": {"type": "integer", "description": "最多返回条数", "default": 5},
                    },
                    "required": ["query"],
                },
                category="builtin",
                read_only=True,
                handler=handle_search_external_docs,
            )
        )
```

同时在 `chat-core` toolset 的 `tools` 列表中条件追加（或新建 `external` toolset）：

```python
    # chat-core toolset 改为条件构建
    chat_core_tools = ["memory_search", "memory_save", "get_profile", "update_profile"]
    if get_settings().external_data_enabled:
        chat_core_tools.append("search_external_docs")

    resolver.register(
        "chat-core",
        ToolsetConfig(description="核心对话工具", tools=chat_core_tools),
    )
```

**缓存注意**：`backend/agent/pydantic_agent.py` 的 `_tool_runtime` 是全局缓存，`_config_fingerprint()` 当前只包含 LLM 配置。若 `external_data_enabled` 可能在运行时变化，需要把 `external_data_enabled` 和 `external_data_dirs` 纳入 `_config_fingerprint()`，或在配置更新后清空 `_tool_runtime`，否则工具列表不会刷新。

---

### Task 3.5 — Startup 集成

**修改文件**：`backend/main.py` 的 `lifespan` 函数

在现有初始化逻辑（`init_db` 之后）新增：

```python
from backend.ingestion import init_pipeline
from backend.ingestion.connectors.filesystem import FilesystemConnector
from backend.config import USER_DATA_DIR

# 在 lifespan 中：
settings = get_settings()
if settings.external_data_enabled:
    store_dir = USER_DATA_DIR
    store_dir.mkdir(exist_ok=True)
    
    pipeline = init_pipeline(store_dir)
    
    if settings.external_data_dirs:
        pipeline.register(FilesystemConnector(settings.external_data_dirs))
    
    # 后台全量扫描（不阻塞启动）
    ingestion_task = asyncio.create_task(_run_initial_scan(pipeline), name="external-ingestion-initial-scan")
    
    # 启动文件监听
    pipeline.start_watching_all()

yield  # FastAPI lifespan yield

# 清理（在 yield 之后）
if settings.external_data_enabled:
    get_pipeline().stop_watching_all()
    if ingestion_task and not ingestion_task.done():
        ingestion_task.cancel()


async def _run_initial_scan(pipeline) -> None:
    """后台全量扫描，不阻塞启动。"""
    import asyncio
    try:
        await asyncio.sleep(2)  # 等待 DB 完全初始化
        summary = await pipeline.run_full_scan()
        logger.info("ingestion.initial_scan_complete", summary=summary)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("ingestion.initial_scan_failed", error=str(exc))
```

注意：`ingestion_task` 需要在 `lifespan` 中先初始化为 `None`，避免 `external_data_enabled=False` 时 shutdown 引用未定义变量。

---

## Definition of Done

- [ ] `EXTERNAL_DATA_ENABLED=true` + `EXTERNAL_DATA_DIRS=/path/to/obsidian` 后，启动日志出现 `ingestion.initial_scan_complete`
- [ ] FTS5 表 `external_items_fts` 中能查到笔记内容
- [ ] FTS5 表 `external_items_fts_trigram` 中能查到中文笔记内容
- [ ] `recall(user_id, "某个关键词", source_scope="external")` 返回匹配的笔记片段
- [ ] `recall(user_id, "某个关键词", source_scope="all")` 同时返回 Narrative + External 结果
- [ ] Agent 能调用 `search_external_docs` 工具并返回有效结果
- [ ] 修改一个 .md 文件后，watchdog 触发重新索引（日志出现 `ingestion.file_changed`）
- [ ] `is_indexed` dedup 正常工作：内容未变的文件不重复写入 DB
- [ ] 现有 `recall()` 调用（无 `source_scope` 参数）行为不变
- [ ] 现有 `memory_search scope=profile/emotions/reference/chat` 行为不变
- [ ] 单元测试覆盖：外部文档 upsert 后可搜索、更新后搜索结果更新、中文 trigram 命中、无外部表时搜索优雅失败

---

## 不在本 Story 范围

- MCP filesystem server 进程化（Phase 2a 直接 Python 扫描，MCP 是 Phase 2b+ 的事）
- GitHub 数据源（Phase 2b）
- Cognee 语义搜索（Phase 2c）
- 前端配置 UI（配置通过 `.env` 设置）
- 全文本分块（chunk）策略 — Phase 2a 直接存完整文件内容，分块是 Phase 2c 优化

---

## 依赖项

```
watchdog>=4.0.0    # 文件系统监听
```

加入 `requirements.txt`（或 `pyproject.toml`）。
