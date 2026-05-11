# Story: 文件上传与知识导入

## 背景

Lumen 当前只能通过对话交互积累记忆。用户无法把已有的资料（简历、笔记、文档）直接喂给 Lumen。

**目标**：用户可以上传 PDF/DOCX/PPTX/XLSX/MD/TXT 等文件，Lumen 自动解析为 Markdown → 分块 → 语义索引。之后 Agent 能在对话中引用这些内容回答问题。同时提供独立的**知识库管理页面**，支持查看、删除已上传文件。

---

## 设计原则

1. **独立文件实体** — UploadedFile ORM 模型，有自己的生命周期（非纯事件流）
2. **状态机** — pending → processing → ready → failed，大文件不阻塞
3. **格式路由器** — 解析器按文件扩展名分发，后续可插拔替换（如 PDF 换 pymupdf4llm/MinerU）
4. **复用已有基础设施** — GrowthEvent 事件系统 + DocumentStore + Cognee 语义索引
5. **双入口** — Chat 📎 按钮 + 独立 Knowledge 页面

---

## 现有架构（可复用）

| 已有 | 位置 | 说明 |
|------|------|------|
| `FilePayload` schema | `backend/schemas.py:159` | 已定义，需扩展 |
| `DocumentStore` | `backend/memory/documents.py` | 原始文件存储（save/read/delete） |
| `DATASET_REFERENCE` | `backend/memory/datasets.py` | Cognee 数据集常量 |
| `LumenMemory.remember()` | `backend/memory/facade.py` | 通用事件写入 |
| `SemanticStore.ingest()` | `backend/memory/semantic_store.py` | Cognee 语义索引 |
| `GrowthEventRepository` | `backend/memory/relational_store.py` | SHA256 去重 + FTS5 |
| `.md 投影管线` | `backend/memory/markdown.py` | 事件 → .md 文件生成 |

---

## 数据流

```
┌─ 前端（双入口）──────────────────────────────────────┐
│  Knowledge.tsx 上传  或  Chat.tsx 📎 按钮             │
│       │ POST /api/knowledge/upload (multipart)        │
└───────┼──────────────────────────────────────────────┘
        │
┌─ 后端 knowledge.py ──┼───────────────────────────────┐
│  1. 写 UploadedFile 记录 (status=pending)             │
│  2. 存原始文件 DocumentStore.save()                   │
│  3. 后台任务: 解析 → 分块 → 索引                      │
│     ├─ status → processing                           │
│     ├─ ParserFactory.get(ext).parse() → markdown     │
│     ├─ chunk_text() → N 块                           │
│     ├─ GrowthEvent("document_uploaded", FilePayload)  │
│     ├─ SemanticStore.ingest() × N chunks             │
│     ├─ sync_projections() → documents.md             │
│     └─ status → ready (或 failed)                    │
│  4. 前端轮询 GET /api/knowledge/{id}/status           │
└──────────────────────────────────────────────────────┘

Agent 对话时（已有 L2 召回路径）：
  memory_search → recall() → Cognee 语义搜索 + FTS5
  → 命中 document_uploaded 分块 → Agent 引用回答
```

---

## 变更范围（13 个文件，4 个新增，9 个改动）

---

### 1. `backend/models.py` — 新增 UploadedFile ORM

**改动：** 新增独立文件实体模型，带状态机字段。

```python
class UploadedFile(Base):
    """用户上传的文件实体 — 独立于 GrowthEvent 的文件生命周期管理。"""
    __tablename__ = "uploaded_files"
    __table_args__ = (
        Index("ix_uploaded_files_user_id", "user_id"),
        Index("ix_uploaded_files_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf/docx/md/...
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA256
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # pending → processing → ready / failed
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 关联的 GrowthEvent ID
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

**状态机**：

```
pending ──→ processing ──→ ready
                │
                └──→ failed (error_message 记录原因)
```

---

### 2. `backend/schemas.py` — 扩展 FilePayload + 新增 Knowledge schemas

**改动：** 扩展已有 FilePayload，新增 API 响应/请求模型。

```python
# ═══════════════════════════════════════════
#  文件上传 Payload（扩展已有的 FilePayload）
# ═══════════════════════════════════════════

class FilePayload(BaseModel):
    filename: str
    file_type: str                          # "pdf" | "docx" | "md" | ...
    file_hash: str = ""
    size_bytes: int = 0
    storage_path: str = ""                  # DocumentStore 相对路径
    chunk_count: int = 0
    preview: str = ""                       # 前 200 字预览
    metadata: dict = Field(default_factory=dict)


# ═══════════════════════════════════════════
#  知识库 API schemas（新增）
# ═══════════════════════════════════════════

class KnowledgeFileResponse(BaseModel):
    """单个文件的状态响应。"""
    id: str
    filename: str
    file_type: str
    size_bytes: int
    status: str                             # pending | processing | ready | failed
    chunk_count: int = 0
    preview: str | None = None
    error_message: str | None = None
    created_at: str | None = None

class KnowledgeListResponse(BaseModel):
    """文件列表响应。"""
    files: list[KnowledgeFileResponse]
    total: int

class KnowledgeUploadResponse(BaseModel):
    """上传提交响应 — 立即返回，前端轮询状态。"""
    id: str
    filename: str
    status: str                             # "pending"

# EVENT_PAYLOAD_MAP 新增映射
EVENT_PAYLOAD_MAP["document_uploaded"] = FilePayload
```

---

### 3. `backend/memory/parsers.py` — 新增文件解析器（格式路由器）

**新增文件。** 格式路由器模式：按扩展名分发到不同解析策略。当前全部走 markitdown，后续可针对 PDF 换 pymupdf4llm/MinerU。

```python
"""文件解析器 — 格式路由器模式。

按文件扩展名分发到对应的解析策略：
- 纯文本 (md/txt/markdown)：直接读取
- 其他格式：markitdown 统一处理
- 图片：拒绝（需要 llm_client）

后续可针对特定格式替换解析器（如 PDF 换 pymupdf4llm），只需修改 _PARSER_MAP。
"""

import tempfile
from pathlib import PurePosixPath
from typing import Protocol

from markitdown import MarkItDown


class ParseResult:
    __slots__ = ("text", "metadata")

    def __init__(self, text: str, metadata: dict | None = None):
        self.text = text
        self.metadata = metadata or {}


class FileParser(Protocol):
    async def __call__(self, filename: str, content: bytes) -> ParseResult: ...


# ── 解析策略 ──────────────────────────────────


async def _parse_plain_text(filename: str, content: bytes) -> ParseResult:
    """纯文本直接读取，不走 markitdown。"""
    ext = PurePosixPath(filename).suffix.lower().lstrip(".")
    text = content.decode("utf-8", errors="replace")
    return ParseResult(text, {"chars": len(text), "ext": ext, "parser": "plain"})


_converter = MarkItDown()


async def _parse_markitdown(filename: str, content: bytes) -> ParseResult:
    """markitdown 统一解析（PDF/DOCX/PPTX/XLSX/HTML/EPUB 等）。"""
    ext = PurePosixPath(filename).suffix.lower().lstrip(".")

    if ext in _IMAGE_EXTENSIONS:
        raise ValueError(
            f"图片文件 .{ext} 暂不支持直接索引。"
            "请将内容整理为文本文件后上传。"
        )

    suffix = PurePosixPath(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = _converter.convert(tmp_path)
        text = result.text_content or ""
    except Exception as e:
        raise ValueError(f"无法解析文件 .{ext}: {e}") from e
    finally:
        from pathlib import Path
        Path(tmp_path).unlink(missing_ok=True)

    if not text.strip():
        raise ValueError(f"文件 .{ext} 内容为空或无法提取文本")

    return ParseResult(text, {"chars": len(text), "ext": ext, "parser": "markitdown"})


# ── 未来增强：PDF 专用解析器（示例） ──────────
# async def _parse_pdf_pymupdf(filename: str, content: bytes) -> ParseResult:
#     """pymupdf4llm — 社区推荐的最佳 PDF→MD 方案。"""
#     import pymupdf4llm
#     import fitz
#     doc = fitz.open(stream=content, filetype="pdf")
#     text = pymupdf4llm.to_markdown(doc)
#     return ParseResult(text, {"chars": len(text), "ext": "pdf", "parser": "pymupdf4llm"})


# ── 格式路由表 ────────────────────────────────

_PLAIN_EXTS = {"md", "txt", "markdown", "rst", "csv", "json"}

_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp", ".svg",
}

_PARSER_MAP: dict[str, FileParser] = {
    **{ext: _parse_plain_text for ext in _PLAIN_EXTS},
}

_DEFAULT_PARSER: FileParser = _parse_markitdown

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls",
    ".html", ".htm", ".csv", ".json",
    ".md", ".txt", ".markdown", ".rst",
    ".epub", ".ipynb", ".zip",
}


async def parse_file(filename: str, content: bytes) -> ParseResult:
    """解析上传文件，返回 ParseResult(text, metadata)。"""
    ext = PurePosixPath(filename).suffix.lower().lstrip(".")

    full_ext = f".{ext}"
    if full_ext in _IMAGE_EXTENSIONS:
        raise ValueError(
            f"图片文件 .{ext} 暂不支持直接索引。"
            "请将内容整理为文本文件后上传。"
        )

    parser = _PARSER_MAP.get(ext, _DEFAULT_PARSER)
    return await parser(filename, content)
```

---

### 4. `backend/memory/chunker.py` — 新增文本分块器

**新增文件。** 递归字符分割，保留上下文重叠。

```python
"""文本分块器 — 递归字符分割，保留上下文重叠。"""


def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 100,
    separators: list[str] | None = None,
) -> list[str]:
    if separators is None:
        separators = ["\n\n\n", "\n\n", "\n", "。", ".", " "]

    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    for sep in separators:
        if sep in text:
            parts = text.split(sep)
            chunks: list[str] = []
            current = ""

            for part in parts:
                candidate = current + sep + part if current else part
                if len(candidate) <= chunk_size:
                    current = candidate
                else:
                    if current:
                        chunks.append(current.strip())
                    current = part

            if current:
                chunks.append(current.strip())

            if overlap > 0 and len(chunks) > 1:
                overlapped = []
                for i, chunk in enumerate(chunks):
                    if i > 0:
                        prev_tail = chunks[i - 1][-overlap:]
                        chunk = prev_tail + chunk
                    overlapped.append(chunk)
                chunks = overlapped

            return [c for c in chunks if c.strip()]

    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size - overlap)]
```

---

### 5. `backend/services/knowledge.py` — 新增知识库 API

**新增文件。** 完整 CRUD + 异步处理。

```python
"""知识库 API — 文件上传、列表、删除、状态轮询。"""

import asyncio
import hashlib

from fastapi import APIRouter, UploadFile, File, Form, Query, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """上传文件 → 存原始文件 + 创建 UploadedFile(status=pending)。
    后台异步处理，前端轮询状态。"""
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "文件大小超过 10MB 限制")
    if not file.filename:
        raise HTTPException(400, "缺少文件名")

    ext = _get_ext(file.filename)
    if f".{ext}" not in _get_supported_extensions():
        raise HTTPException(400, f"不支持的文件格式 .{ext}")

    # 存原始文件
    from backend.memory.documents import DocumentStore
    file_hash = hashlib.sha256(content).hexdigest()
    doc_store = DocumentStore()
    storage_path = doc_store.save(user_id, "uploaded", file.filename, content)

    # 创建 UploadedFile 记录
    from backend.models import UploadedFile
    import uuid
    record = UploadedFile(
        id=str(uuid.uuid4()),
        user_id=user_id,
        filename=file.filename,
        file_type=ext,
        file_hash=file_hash,
        size_bytes=len(content),
        storage_path=storage_path,
        status="pending",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # 后台处理
    asyncio.create_task(
        _process_file(
            record_id=record.id, user_id=user_id, filename=file.filename,
            file_type=ext, content=content, file_hash=file_hash, storage_path=storage_path,
        )
    )

    return {"id": record.id, "filename": file.filename, "status": "pending"}


async def _process_file(
    record_id: str, user_id: str, filename: str, file_type: str,
    content: bytes, file_hash: str, storage_path: str,
) -> None:
    """后台异步：解析 → 分块 → 事件写入 → 语义索引。"""
    from backend.db import get_async_session_maker
    from backend.models import UploadedFile

    async with get_async_session_maker()() as db:
        record = await db.get(UploadedFile, record_id)
        if not record:
            return
        try:
            record.status = "processing"
            await db.commit()

            # 解析
            from backend.memory.parsers import parse_file
            result = await parse_file(filename, content)
            text = result.text
            if not text.strip():
                record.status = "failed"
                record.error_message = "文件内容为空或无法提取文本"
                await db.commit()
                return

            # 分块
            from backend.memory.chunker import chunk_text
            chunks = chunk_text(text)
            preview = text[:200].replace("\n", " ")

            # 写 GrowthEvent
            from backend.schemas import FilePayload
            from backend.memory.facade import get_memory
            payload = FilePayload(
                filename=filename, file_type=file_type, file_hash=file_hash,
                size_bytes=len(content), storage_path=storage_path,
                chunk_count=len(chunks), preview=preview, metadata=result.metadata,
            ).model_dump()

            memory = get_memory()
            event = await memory.remember(
                user_id=user_id, event_type="document_uploaded",
                entity_type="document", entity_id=file_hash[:16],
                payload=payload, source="user_upload",
            )

            # 语义索引
            if event:
                from backend.memory.semantic_store import SemanticStore
                from backend.memory.datasets import DATASET_REFERENCE
                semantic = SemanticStore()
                for i, chunk in enumerate(chunks):
                    doc_id = f"{event.id}_chunk_{i}"
                    indexed_content = f"[文件: {filename}] [块 {i+1}/{len(chunks)}]\n{chunk}"
                    await semantic.ingest(
                        content=indexed_content, doc_id=doc_id, dataset=DATASET_REFERENCE,
                    )
                await memory.sync_projections(user_id, event_ids=[event.id])

            # ready
            record.status = "ready"
            record.chunk_count = len(chunks)
            record.preview = preview
            record.event_id = str(event.id) if event else None
            await db.commit()

            logger.info("File processed", filename=filename, chunks=len(chunks), status="ready")
        except Exception as exc:
            logger.warning("File processing failed", filename=filename, error=str(exc))
            record = await db.get(UploadedFile, record_id)
            if record:
                record.status = "failed"
                record.error_message = str(exc)[:500]
                await db.commit()


@router.get("/list")
async def list_files(
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """列出用户已上传的文件。"""
    from backend.models import UploadedFile
    result = await db.execute(
        select(UploadedFile)
        .where(UploadedFile.user_id == user_id)
        .order_by(UploadedFile.created_at.desc())
    )
    files = result.scalars().all()
    return {
        "files": [_file_to_dict(f) for f in files],
        "total": len(files),
    }


@router.get("/{file_id}/status")
async def get_file_status(
    file_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """轮询单个文件的处理状态。"""
    from backend.models import UploadedFile
    record = await db.get(UploadedFile, file_id)
    if not record or record.user_id != user_id:
        raise HTTPException(404, "文件不存在")
    return {
        "id": str(record.id),
        "status": record.status,
        "chunk_count": record.chunk_count,
        "preview": record.preview,
        "error_message": record.error_message,
    }


@router.delete("/{file_id}")
async def delete_file(
    file_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """删除文件记录 + 原始文件 + 关联的 GrowthEvent。"""
    from backend.models import UploadedFile
    record = await db.get(UploadedFile, file_id)
    if not record or record.user_id != user_id:
        raise HTTPException(404, "文件不存在")

    # 删原始文件
    from backend.memory.documents import DocumentStore
    DocumentStore().delete(record.storage_path)

    # 删关联事件
    if record.event_id:
        from backend.memory.facade import get_memory
        await get_memory().delete_event(user_id, record.event_id)

    await db.delete(record)
    await db.commit()

    # 重建投影
    from backend.memory.facade import get_memory
    await get_memory().force_md_rebuild(user_id)

    return {"deleted": True}


def _file_to_dict(f) -> dict:
    return {
        "id": str(f.id), "filename": f.filename, "file_type": f.file_type,
        "size_bytes": f.size_bytes, "status": f.status,
        "chunk_count": f.chunk_count, "preview": f.preview,
        "error_message": f.error_message,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }

def _get_ext(filename: str) -> str:
    from pathlib import PurePosixPath
    return PurePosixPath(filename).suffix.lower().lstrip(".") or "unknown"

def _get_supported_extensions() -> set[str]:
    from backend.memory.parsers import SUPPORTED_EXTENSIONS
    return SUPPORTED_EXTENSIONS
```

---

### 6. `backend/main.py` — 注册路由

**改动：** 新增一行。

```python
from backend.services import knowledge
app.include_router(knowledge.router, prefix="/api")
```

---

### 7. `backend/memory/events_merger.py` — 新增 document 合并函数

**改动：** 新增 `merge_document_events()` + `generate_documents_md()`。

```python
def merge_document_events(events: list) -> list[dict]:
    """合并文件上传事件，返回文档列表（按上传时间倒序，SHA256 去重）。"""
    docs = []
    seen_hashes: set[str] = set()
    for event in reversed(events):
        payload = load_payload(event)
        h = payload.get("file_hash", "")
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        docs.append({
            "filename": payload.get("filename", ""),
            "file_type": payload.get("file_type", ""),
            "size_bytes": payload.get("size_bytes", 0),
            "chunk_count": payload.get("chunk_count", 0),
            "preview": payload.get("preview", ""),
            "uploaded_at": str(event.created_at) if hasattr(event, "created_at") else "",
        })
    return list(reversed(docs))


def generate_documents_md(documents: list[dict]) -> str:
    """生成 documents.md 投影。"""
    if not documents:
        return ""
    lines = ["# 已上传文件\n"]
    for doc in documents:
        size_kb = doc["size_bytes"] / 1024
        lines.append(f"## {doc['filename']}")
        lines.append(f"- 类型：{doc['file_type']}")
        lines.append(f"- 大小：{size_kb:.1f} KB")
        lines.append(f"- 分块：{doc['chunk_count']} 块")
        if doc.get("uploaded_at"):
            lines.append(f"- 上传时间：{doc['uploaded_at'][:10]}")
        if doc.get("preview"):
            lines.append(f"- 摘要：{doc['preview']}")
        lines.append("")
    return "\n".join(lines)
```

---

### 8. `backend/memory/markdown.py` — 新增 documents.md 投影

**改动：** 在 `project_user_to_md()` 中追加文档投影。

```python
# 在 project_user_to_md() 内部，已有 skill/experience 投影之后：

doc_events = [e for e in events if e.event_type == "document_uploaded"]
if doc_events:
    from backend.memory.events_merger import merge_document_events, generate_documents_md
    documents = merge_document_events(doc_events)
    docs_md = generate_documents_md(documents)
    _write_md_file_safe(base_dir / "documents.md", docs_md)
```

---

### 9. `backend/memory/search.py` — 扩展搜索范围

**改动：** `recall()` / `search_all()` 增加 `DATASET_REFERENCE` 数据集覆盖。

```python
from backend.memory.datasets import DATASET_REFERENCE
# 搜索时 datasets 参数包含 DATASET_REFERENCE
```

---

### 10. `src/lib/api.ts` — 新增 Knowledge API 函数

**改动：** 新增知识库相关 API 调用。

```typescript
export async function uploadKnowledgeFile(file: File): Promise<{ id: string; filename: string; status: string }> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('user_id', getUserId())
  const res = await fetch('/api/knowledge/upload', { method: 'POST', body: formData })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '上传失败' }))
    throw new Error(err.detail || '上传失败')
  }
  return res.json()
}

export async function getKnowledgeFiles(userId?: string): Promise<{ files: KnowledgeFile[]; total: number }> {
  const uid = userId || getUserId()
  const res = await fetch(`/api/knowledge/list?user_id=${uid}`)
  return res.json()
}

export async function getKnowledgeFileStatus(fileId: string, userId?: string): Promise<KnowledgeFileStatus> {
  const uid = userId || getUserId()
  const res = await fetch(`/api/knowledge/${fileId}/status?user_id=${uid}`)
  return res.json()
}

export async function deleteKnowledgeFile(fileId: string, userId?: string): Promise<void> {
  const uid = userId || getUserId()
  await fetch(`/api/knowledge/${fileId}?user_id=${uid}`, { method: 'DELETE' })
}

export interface KnowledgeFile {
  id: string; filename: string; file_type: string; size_bytes: number
  status: 'pending' | 'processing' | 'ready' | 'failed'
  chunk_count: number; preview: string | null; error_message: string | null; created_at: string | null
}

export interface KnowledgeFileStatus {
  id: string; status: string; chunk_count: number; preview: string | null; error_message: string | null
}
```

---

### 11. `src/pages/Knowledge.tsx` — 新建知识库管理页面

**新增文件。** 独立的知识库管理页面：文件列表 + 上传 + 状态轮询 + 删除。

```tsx
/**
 * Knowledge.tsx — 知识库管理页面
 *
 * 功能：
 * - 文件列表（显示状态：pending/processing/ready/failed）
 * - 上传文件（拖拽 + 按钮）
 * - 状态轮询（processing 时每 2s 轮询）
 * - 删除文件
 * - 文件大小/类型/分块数显示
 */
```

*（完整 JSX 实现在实施阶段编写，遵循现有页面风格：Tailwind CSS 4 + 项目 OKLCH 色板）*

---

### 12. `src/main.tsx` + `src/components/Sidebar.tsx` — 路由 + 导航

**main.tsx 改动：**
```tsx
import Knowledge from './pages/Knowledge'
// 在 Route 树中新增：
<Route path="knowledge" element={<Knowledge />} />
```

**Sidebar.tsx 改动：**
```tsx
// 在页面导航区域新增：
<NavLink to="/knowledge" className={navLinkClass}>知识库</NavLink>
```

---

### 13. `src/pages/Chat.tsx` — InputBox 添加 📎 上传按钮

**改动：** 在发送按钮左侧添加附件按钮，调用 `uploadKnowledgeFile()` + toast 提示。

```tsx
// InputBox 新增 onFileUpload prop
// 📎 按钮 → 选择文件 → POST /api/knowledge/upload
// 成功后 toast "已上传 {filename}，正在处理..."
// 不阻塞对话流
```

---

## API 端点总结

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/knowledge/upload` | 上传文件（multipart），立即返回 pending 状态 |
| `GET` | `/api/knowledge/list?user_id=` | 文件列表（含状态） |
| `GET` | `/api/knowledge/{file_id}/status?user_id=` | 轮询处理状态 |
| `DELETE` | `/api/knowledge/{file_id}?user_id=` | 删除文件 + 记录 + 关联事件 |

---

## 文件存储结构

```
~/.lumen/
├── files/
│   └── {user_id}/
│       └── uploaded/
│           └── 17153..._resume.pdf     # 原始文件（DocumentStore）
├── memory/
│   └── {user_id}/
│       ├── memory.md                   # 已有
│       ├── skills.md                   # 已有
│       ├── experiences.md              # 已有
│       └── documents.md                # 新增：上传文件清单
├── kuzu/                               # Cognee 图谱
└── lancedb/                            # Cognee 向量
```

---

## 验收标准

1. **上传成功**：Knowledge 页面上传 PDF → 状态从 pending → processing → ready
2. **Chat 上传**：Chat 📎 按钮上传 → toast 提示 → 不阻塞对话
3. **Agent 引用**：上传简历后问"我的教育背景" → Agent 引用简历内容
4. **多格式**：PDF/DOCX/PPTX/XLSX/MD/TXT 均可上传索引
5. **图片拒绝**：上传图片返回明确错误提示
6. **状态轮询**：processing 状态自动轮询，ready 后停止
7. **删除**：删除文件 → 记录 + 原始文件 + GrowthEvent 全部清理
8. **去重**：重复文件不会创建重复事件（SHA256 去重）
9. **错误处理**：解析失败 → status=failed + error_message 显示
10. **大小限制**：单文件上限 10MB
11. **投影**：documents.md 正确生成

---

## 实施顺序

| 步骤 | 内容 | 依赖 | 可并行 |
|------|------|------|--------|
| 1 | `parsers.py` — 文件解析器（格式路由器） | 无 | ✅ |
| 2 | `chunker.py` — 文本分块器 | 无 | ✅ |
| 3 | `models.py` — UploadedFile ORM | 无 | ✅ |
| 4 | `schemas.py` — 扩展 FilePayload + Knowledge schemas | 无 | ✅ |
| 5 | `services/knowledge.py` — 知识库 API | 1-4 | |
| 6 | `main.py` — 注册路由 | 5 | |
| 7 | `events_merger.py` + `markdown.py` — 投影 | 5 | |
| 8 | `search.py` — 扩展搜索范围 | 5 | |
| 9 | `api.ts` — Knowledge API 函数 | 5 | |
| 10 | `Knowledge.tsx` — 知识库页面 | 9 | |
| 11 | `main.tsx` + `Sidebar.tsx` — 路由+导航 | 10 | |
| 12 | `Chat.tsx` — 📎 上传按钮 | 9 | |
| 13 | 集成测试 | 全部 | |

步骤 1-4 可完全并行，步骤 5 是核心串联点。

---

## 注意事项

1. **格式路由器**：`parsers.py` 用 `_PARSER_MAP` 字典分发，未来 PDF 换解析器只需改一行映射
2. **markitdown 需要文件路径**：不能从 bytes 直接转换，需写临时文件（见 `parsers.py` tempfile 方案）
3. **markitdown 图片需 LLM**：ImageConverter 需要 `llm_client` + `llm_model`，当前不支持图片
4. **异步处理不阻塞**：`upload_file` 立即返回 pending，`_process_file` 后台 asyncio task
5. **UploadedFile 独立生命周期**：不依赖 GrowthEvent 存在，文件管理不受事件流影响
6. **Cognee 用 DATASET_REFERENCE**：与 profile 数据分开，便于按数据集管理
7. **前端轮询**：processing 状态时每 2s 轮询 `/status`，ready 后停止
8. **不新增 Agent 工具**：文件上传是用户操作，Agent 通过已有 `memory_search` 检索
9. **FTS5 自动同步**：GrowthEvent 写入后 FTS5 trigger 自动更新
10. **markitdown 已在 requirements.txt**：无新增依赖

---

## PDF 解析改进路径（未来）

当前用 markitdown，如 PDF 效果不满意：

| 方案 | 质量 | 体积 | 备注 |
|------|------|------|------|
| markitdown（当前） | 中 | 轻 | 表格/结构提取差（issue #41） |
| **pymupdf4llm** | 高 | 中 | 社区推荐最佳，pip install pymupdf4llm |
| MinerU (OpenDataLab) | 很高 | 重 | 依赖 torch，适合专业场景 |
| Docling (IBM) | 中高 | 中 | 42K+ stars，依赖较重 |
| Azure DocIntel | 最高 | 云 | 非离线，有成本 |

架构已预留格式路由器，替换时只需在 `parsers.py` 的 `_PARSER_MAP` 中将 `"pdf"` 映射到新解析器。
