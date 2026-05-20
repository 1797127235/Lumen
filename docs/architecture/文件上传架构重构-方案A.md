# 文件上传架构重构 — 方案A：桌面版 Agent 自主读取

> 基于桌面版（Tauri）场景，将文件解析从"发送前阻塞"改为"Agent 按需读取"。

---

## 一、现状问题

### 1.1 已修复的代码 bug

| 问题 | 根因 | 修复 |
|---|---|---|
| `useRef` 括号缺失 | `chatSession.tsx` 替换代码时遗漏闭合括号 | 已补 `)` |
| `build_multimodal_parts` KeyError | `name` 与 `file_name` 字段名不一致 | 改为兼容两种 key |
| 发送后附件预览残留 | `clearAttachments()` 放在 `onDone` 回调 | 移到 `chatStream` 调用前 |
| 上传接口阻塞 | `save_upload` 里同步做 Docling 解析 | 移到 `resolve_attachment_metas` |

### 1.2 未解决的架构问题

```
用户点击发送
  ↓
stream_chat 开始
  ↓
resolve_attachment_metas → Docling 解析 100+ 页 PDF  ← 阻塞！
  ↓  ← 可能 1 分钟+
_inject_context_frame → 注入全文
  ↓
agent.run_stream_events()  ← SSE 终于开始
```

1. **阻塞 SSE**：Docling 解析在 SSE 开始前同步运行，用户点击发送后前端完全无响应
2. **内存耗尽**：`std::bad_alloc`（100+ 页扫描版 PDF），可能 kill 后端进程
3. **无边界控制**：未限制页数、未限制注入文本长度，大文件撑爆 context window
4. **职责错位**：文件解析硬编码在 `stream_chat`，本应是 Agent 的能力
5. **过度持久化**：原有设计把文件复制到 `~/.lumen/uploads/` 并建 `Attachment` ORM 表，对于单用户桌面场景过于复杂。改为轻量复制到 `session-files/`（无 ORM、无上传路由），按会话生命周期管理。

---

## 二、目标设计

**核心原则**：桌面版不走 multipart 上传接口，发送前**轻量复制**到 `session-files/`（仅 copy，不解析），Agent 按需读取副本。

```
前端（Tauri）→ dialog 选择本地文件 → 拿到绝对路径 → 显示文件名预览
用户发送消息 → POST /api/chat → body: { message, attachments: ["/Users/xxx/简历.pdf"] }
后端 → stream_chat:
  1. 复制附件到 ~/.lumen/session-files/{conversation_id}/（仅 copy，不解析）
  2. 注入 [attached_file: /copy/path] 标记
  3. SSE 启动延迟从分钟级降到秒级（仅文件复制 IO，不再受 Docling 解析影响）
Agent → 按需调用 file_read tool → 读取/解析副本内容
对话删除 → 清理对应的 session-files 目录
```

---

## 三、桌面版新架构

### 3.1 选择文件（前端）

```typescript
import { open } from '@tauri-apps/plugin-dialog'
import { stat } from '@tauri-apps/plugin-fs'

const MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024  // 20MB

const paths = await open({
  multiple: true,
  filters: [{
    name: '文档与图片',
    extensions: ['txt','md','json','csv','pdf','docx','pptx','xlsx','png','jpg','jpeg','gif','webp']
  }]
})

// 过滤超大文件
const validPaths = []
for (const p of paths || []) {
  try {
    const info = await stat(p)
    if (info.size > MAX_ATTACHMENT_SIZE) {
      alert(`文件过大：${p} (${(info.size / 1024 / 1024).toFixed(1)}MB，最大 20MB)`)
      continue
    }
    validPaths.push(p)
  } catch {
    validPaths.push(p)  // 获取不到大小就放行，后端会二次校验
  }
}
```

前端只保留路径和文件名：
```typescript
type PendingAttachment = {
  path: string;   // 本地绝对路径
  name: string;   // 文件名（UI 预览用）
}
```

### 3.2 发送消息（前端 → 后端）

```typescript
body: JSON.stringify({
  message: content,
  conversation_id: targetCid ?? undefined,
  attachments: attachments.map(a => a.path),  // 原始绝对路径 string[]
})
```

后端 `stream_chat` 在 SSE 开始前轻量复制（仅 `shutil.copy2`，不做任何解析，带完整安全校验）：

```python
import shutil
import secrets
import time
from pathlib import Path

MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
MAX_ATTACHMENTS = 5
MAX_FILENAME_BYTES = 255
SESSION_FILES_DIR = Path.home() / ".lumen" / "session-files"

# 敏感路径黑名单
_SENSITIVE_PATHS = [
    Path.home() / ".ssh",
    Path.home() / ".gnupg",
    Path.home() / ".aws",
    Path.home() / ".config" / "gcloud",
    Path.home() / ".kube",
    Path.home() / ".lumen",  # 防止循环复制到自身
]

_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}

def _is_sensitive_path(file_path: str) -> bool:
    p = Path(file_path).resolve()
    return any(p == sp or p.is_relative_to(sp) for sp in _SENSITIVE_PATHS)

def _sanitize_filename(name: str) -> str:
    safe = Path(name).name
    # 替换控制字符和文件系统保留字符
    for cp in range(0x00, 0x20):
        safe = safe.replace(chr(cp), "_")
    for ch in '<>:"/\\|?*':
        safe = safe.replace(ch, "_")
    # 去除尾部空格和点（Windows 限制）
    safe = safe.rstrip(" .")
    if not safe:
        safe = "file"
    # 处理 Windows 保留设备名
    base = Path(safe).stem
    ext = Path(safe).suffix
    if base.lower() in _WINDOWS_RESERVED_NAMES:
        safe = f"file-{safe}"
    # UTF-8 字节截断到 255 字节
    encoded = safe.encode("utf-8")
    if len(encoded) > MAX_FILENAME_BYTES:
        truncated = encoded[:MAX_FILENAME_BYTES]
        while truncated:
            try:
                safe = truncated.decode("utf-8")
                break
            except UnicodeDecodeError:
                truncated = truncated[:-1]
    return safe

def _unique_name(base: str, ext: str) -> str:
    suffix = f"_{int(time.time())}_{secrets.token_hex(4)}"
    max_base = MAX_FILENAME_BYTES - len(suffix.encode("utf-8")) - len(ext.encode("utf-8"))
    if max_base < 1:
        max_base = 1
    truncated = base.encode("utf-8")[:max_base].decode("utf-8", errors="ignore")
    return f"{truncated}{suffix}{ext}"

async def _copy_attachments(conv_id: str, attachments: list[str]) -> list[str]:
    """复制附件到 session-files，返回复制后的路径列表。"""
    if len(attachments) > MAX_ATTACHMENTS:
        logger.warning("附件数量超限", count=len(attachments), max=MAX_ATTACHMENTS)
        attachments = attachments[:MAX_ATTACHMENTS]

    dest_dir = SESSION_FILES_DIR / conv_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = []

    for src in attachments:
        try:
            if not os.path.isabs(src):
                logger.warning("附件路径必须是绝对路径", path=src); continue
            if os.path.islink(src):
                logger.warning("附件拒绝符号链接", path=src); continue
            if _is_sensitive_path(src):
                logger.warning("附件路径在敏感目录中", path=src); continue
            if os.path.isdir(src):
                logger.warning("附件不支持目录", path=src); continue
            size = os.path.getsize(src)
            if size > MAX_ATTACHMENT_SIZE:
                logger.warning("附件过大，已跳过", path=src, size=size); continue

            ext = Path(src).suffix
            base = Path(src).stem
            dest_name = _unique_name(_sanitize_filename(base), ext)
            dest = dest_dir / dest_name
            await asyncio.to_thread(shutil.copy2, src, dest)
            copied.append(str(dest))
        except OSError:
            logger.warning("附件复制失败", path=src)
    return copied
```

### 3.3 接收消息（后端 → Agent）

```python
# ChatRequest
class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    attachments: list[str] = []  # 本地绝对路径列表
```

`stream_chat` 中：
```python
async def stream_chat(..., attachments: list[str] | None = None):
    # ... 保存用户消息、获取/创建 conversation ...
    conv_id = str(conversation.id)
    
    # 轻量复制到 session-files（仅 copy，不解析）
    copy_paths = await _copy_attachments(conv_id, attachments or [])
    
    # 注入文件标记到 user prompt（使用复制后的路径）
    if copy_paths:
        markers = "\n".join(f"[attached_file: {p}]" for p in copy_paths)
        user_input = f"{user_input}\n\n{markers}"
    
    # 图片从副本读取构造 BinaryContent
    image_parts = []
    for p in copy_paths:
        if is_image(p):
            try:
                image_parts.append(BinaryContent.from_path(p))
            except Exception:
                logger.warning("图片读取失败", path=p)
    
    user_content = [user_input] + image_parts if image_parts else user_input
    
    # SSE 启动（复制延迟与文件总大小线性相关，不再受解析影响）
    async for event in agent.run_stream_events(user_content, ...):
        yield event
```

### 3.4 Agent 读取（按需）

Agent system prompt 增加：

> 当用户消息中包含 `[attached_file: {path}]` 标记时，如果你需要了解该文件内容，请调用 `file_read` 工具，传入 `file_path` 参数。

Agent 决策：
```
用户：帮我看看这份简历
消息：帮我看看这份简历\n[attached_file: C:\Users\xxx\.lumen\session-files\conv-123\简历_abc123.pdf]

Agent：
  1. 判断需要读取 → 调用 file_read(file_path="C:\Users\xxx\.lumen\session-files\conv-123\简历_abc123.pdf")
  2. tool 内部：Docling 解析（max_pages=20）→ 返回 markdown 文本
  3. Agent 基于内容生成回复
```

### 3.5 Session-Files 生命周期管理

**对话删除时清理**：

```python
import shutil

async def cleanup_session_files(conv_id: str):
    """删除指定会话的附件副本目录。"""
    dest_dir = SESSION_FILES_DIR / conv_id
    if dest_dir.exists():
        await asyncio.to_thread(shutil.rmtree, dest_dir, ignore_errors=True)
```

在删除对话的 API 中调用：`await cleanup_session_files(conv_id)`

**定期清理孤儿目录**（后台任务，每天一次）：

```python
async def cleanup_orphan_session_files(db_session):
    """清理没有对应 conversation 的孤儿 session-files 目录。"""
    try:
        result = await db_session.execute(select(Conversation.id))
        existing_ids = {str(r[0]) for r in result}
        for entry in SESSION_FILES_DIR.iterdir():
            if entry.is_dir() and entry.name not in existing_ids:
                await asyncio.to_thread(shutil.rmtree, entry, ignore_errors=True)
                logger.info("清理孤儿 session-files", dir=entry.name)
    except Exception:
        logger.warning("清理孤儿 session-files 失败")
```

---

## 四、Agent Tool 扩展

### 4.1 扩展现有 `file_read`

当前 `lib/tools/files.py` 已有 `file_read`，只需两处扩展：

**扩展 1：`_resolve` 增加 `allow_session_files` 参数（仅 `file_read` 允许）**

> ⚠️ `_resolve` 被 `file_read` / `file_write` / `file_grep` 共用。如果把 session-files 无条件加入白名单，Agent 就能 `file_write` 覆盖用户附件副本，或 `file_grep` 扫描整个 session-files 目录。必须通过参数显式区分。

```python
from pathlib import Path

SESSION_FILES_DIR = Path.home() / ".lumen" / "session-files"

def _resolve(
    raw_path: str,
    workspace_root: str,
    *,
    allow_session_files: bool = False,
) -> tuple[str, str | None]:
    # 仅 file_read 允许 session-files（只读附件副本）
    if allow_session_files and os.path.isabs(raw_path):
        resolved = os.path.realpath(raw_path)
        real_session = os.path.realpath(str(SESSION_FILES_DIR))
        if resolved == real_session or resolved.startswith(real_session + os.sep):
            return resolved, None
        return resolved, f"路径不在允许的 session-files 范围内：{resolved}"
    
    # file_write / file_grep：保持原有 workspace_root 沙箱，不允许 session-files
    if not os.path.isabs(raw_path):
        raw_path = os.path.join(workspace_root, raw_path)
    resolved = os.path.realpath(raw_path)
    real_root = os.path.realpath(workspace_root)
    if resolved != real_root and not resolved.startswith(real_root + os.sep):
        return resolved, f"路径超出工作区范围：{resolved}"
    return resolved, None
```

使用处：
```python
# file_read：允许 session-files
resolved, err = _resolve(raw, str(deps.workspace_root), allow_session_files=True)

# file_write / file_grep：保持原有沙箱
resolved, err = _resolve(raw, str(deps.workspace_root))
```

**扩展 2：`_file_read` 支持文档解析**

两套参数策略：

| 文件类型 | 分页方式 | 说明 |
|---|---|---|
| 文本文件（.py/.md/.txt 等） | `offset`（行号）+ `limit`（行数） | 保持现有逻辑不变 |
| 文档文件（.pdf/.docx/.pptx 等） | `max_length`（字符数） | 忽略 offset/limit，用 max_length 截断 |

```python
async def _file_read(args, deps) -> str:
    raw = args.get("file_path", "").strip()
    if not raw:
        return tool_error("请提供 file_path")
    
    resolved, err = _resolve(raw, str(deps.workspace_root))
    if err:
        return tool_error(err)
    
    ext = os.path.splitext(resolved)[1].lower()
    
    # ── 文档文件：Docling 解析，用 max_length 截断 ──
    if ext in {".pdf", ".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".tex", ".rtf"}:
        # 最后一道防线：工具内部也检查大小
        file_size = os.path.getsize(resolved)
        if file_size > 20 * 1024 * 1024:
            return tool_error(f"文件过大：{file_size / 1024 / 1024:.1f}MB（最大 20MB）")
        max_length = min(int(args.get("max_length", 10000)), 50000)
        return await _read_docling(resolved, max_length)
    
    # ── 图片：提示模型通过 vision 查看 ──
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "[图片文件，已作为视觉输入直接提供]"
    
    # ── 文本文件：保持现有 offset/limit 行分页逻辑 ──
    offset = max(1, int(args.get("offset", 1)))
    limit = min(int(args.get("limit", MAX_READ_LINES)), MAX_READ_LINES)
    # ... 现有文本读取逻辑不变
```

**input_schema 说明**：
```json
{
  "offset": {"type": "integer", "description": "起始行号（1 开始），仅对文本文件生效"},
  "limit": {"type": "integer", "description": "最多读取行数，仅对文本文件生效"},
  "max_length": {"type": "integer", "description": "最大返回字符数，仅对 PDF/DOCX/PPTX 等文档类型生效"}
}
```

`_read_docling` 实现（**进程隔离** — 唯一能真正 kill C++ 解析的方式）：

> ⚠️ `asyncio.wait_for` + `asyncio.to_thread` **不可行**：`wait_for` 取消的是协程，但 `to_thread` 派发的 OS 线程不可强制终止，Docling 的 C++ 层会继续解析直到 OOM。

```python
import multiprocessing as mp
import time

def _docling_worker(file_path: str, result_queue: mp.Queue):
    """在独立进程中运行 Docling，父进程可通过 terminate() 强制 kill。"""
    try:
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(file_path)
        text = result.document.export_to_markdown()
        result_queue.put(("ok", text))
    except Exception as e:
        result_queue.put(("error", str(e)))

async def _read_docling(file_path: str, max_length: int) -> str:
    """
    Docling 在子进程中运行，超时 10s 后 terminate() → kill() 强制终止，
    彻底避免 C++ 层 OOM kill 主进程。
    """
    result_queue = mp.Queue()
    process = mp.Process(target=_docling_worker, args=(file_path, result_queue))
    process.start()

    try:
        start = time.time()
        while time.time() - start < 10.0:
            if not result_queue.empty():
                status, payload = result_queue.get()
                process.join()
                if status == "error":
                    return tool_error(f"文档解析失败：{payload}")
                text = payload
                if len(text) > max_length:
                    text = text[:max_length] + f"\n\n[已截断，原文件共 {len(text)} 字符]"
                return text
            await asyncio.sleep(0.05)
        raise asyncio.TimeoutError()
    except asyncio.TimeoutError:
        process.terminate()
        process.join(timeout=2.0)
        if process.is_alive():
            process.kill()
            process.join()
        logger.warning("Docling 解析超时，降级到 pypdf", path=file_path)
        return await _read_pdf_fallback(file_path, max_length)
```

**进程隔离的代价**：每次调用创建新进程，启动开销约 200-500ms。但 Docling 仅在 Agent 需要读 PDF/DOCX 时才触发，频率极低，可接受。

---

## 五、图片多模态

图片不走 `file_read` tool，而是直接作为 `BinaryContent` 注入 user prompt：

```python
# stream_chat 中
image_paths = [p for p in attachments if is_image(p)]
text_paths = [p for p in attachments if not is_image(p)]

# 文本标记
if text_paths:
    markers = "\n".join(f"[attached_file: {p}]" for p in text_paths)
    user_input = f"{user_input}\n\n{markers}"

# 图片作为 BinaryContent
image_parts = []
for img_path in image_paths:
    try:
        binary = BinaryContent.from_path(img_path)
        image_parts.append(binary)
    except Exception:
        logger.warning("图片读取失败", path=img_path)

# 构造 user prompt
if image_parts:
    user_content: list = [user_input] + image_parts
else:
    user_content = user_input

async for event in agent.run_stream_events(user_content, ...):
```

---

## 六、后端变更清单

### 删除
- `server/routes/upload.py` — 不再需要 multipart 上传路由（原接收 File 二进制）
- `lib/chat/attachment.py` 中的解析逻辑 — Docling/pypdf 解析移到 `file_read` tool
- `src/lib/api/upload.ts` — 不再需要上传 API
- `lib/chat/models.py` 中的 `Attachment` ORM — 不保存附件元数据到数据库
- `core/migrations.py` 中的 attachments DDL — 回滚

### 新增
- `lib/chat/session_files.py` — 轻量复制逻辑：`_copy_attachments`、`_sanitize_filename`、清理
- `~/.lumen/session-files/{conversation_id}/` — 按会话组织的附件副本目录

### 修改
- `server/routes/chat.py` — `ChatRequest.attachments: list[str]`（原始路径列表）
- `lib/chat/service.py` — 
  - `stream_chat` 接收 `attachments: list[str]`
  - 调用 `_copy_attachments` 复制到 `session-files`
  - 注入 `[attached_file: copy_path]` 标记（使用副本路径）
  - 图片从副本构造 `BinaryContent` 注入
  - 去掉 `resolve_attachment_metas`、`build_multimodal_parts`
- `lib/tools/files.py` — `file_read` 支持 Docling 解析 + session-files 白名单
- `core/agent.py` — system prompt 增加 attached_file 标记说明
- 对话删除逻辑 — 删除对话时连带清理 `session-files/{conversation_id}/`

### 前端修改
- `src/pages/Chat.tsx` — 附件按钮调用 Tauri `open()` dialog，不再走 upload API
- `src/lib/chatSession.tsx` — attachments 改为 `{path: string, name: string}[]`
- `src/lib/api/chat.ts` — `chatStream` 支持 `attachments?: string[]`

### Tauri 侧
- `src-tauri/src/lib.rs` — 确认 `tauri_plugin_dialog` 已注册（当前已有）
- 前端通过 `@tauri-apps/plugin-dialog` 调用 `open()`
- **Capability 配置**：`src-tauri/capabilities/default.json` 需添加 `fs:allow-stat`，用于前端获取文件大小：
  ```json
  {
    "permissions": [
      "fs:default",
      "fs:allow-stat",
      "fs:allow-read-file",
      "dialog:allow-open"
    ]
  }
  ```

---

## 七、Web 降级方案（可选）

如果未来需要支持纯浏览器模式：

```
前端（浏览器）→ FileReader.readAsArrayBuffer() → base64
             → POST /api/chat → body: { message, fileBlobs: [{name, base64, mimeType}] }
后端 → 内存中解析 → 不保存磁盘
```

但当前主场景是桌面版，**P0 不做 Web 降级**。

---

## 八、实施顺序

1. **Step 1**：修改 `server/routes/chat.py`（`attachments: list[str]`）

2. **Step 2 + Step 3（必须在同一 commit/PR 中原子完成）**：
   - **Step 2**：修改 `lib/chat/service.py`（注入 `[attached_file: path]` 标记、图片 BinaryContent、**去掉旧解析逻辑**）
   - **Step 3**：扩展 `lib/tools/files.py` 的 `file_read`（支持绝对路径 + Docling 解析）

   > ⚠️ **注意**：Step 2 移除旧解析后，如果 Step 3 未实装，附件会完全不可用。这两步必须同时部署。

4. **Step 4**：更新 `core/agent.py` system prompt（attached_file 说明）
5. **Step 5**：修改前端 `Chat.tsx` + `chatSession.tsx`（Tauri dialog + 路径列表）
6. **Step 6**：删除 `upload.py`、`attachment.py`、`upload.ts`、`Attachment` ORM
7. **Step 7**：测试（文本、PDF、图片多模态）

---

## 九、与 OpenHanako 对比

| 设计点 | OpenHanako | Lumen（方案A 桌面版） |
|---|---|---|
| 前端如何选文件 | Electron dialog → 本地路径 | Tauri dialog → 本地路径 |
| 发送传什么 | 绝对路径 | 绝对路径 |
| 是否保存副本 | **是**（copyFile 到 `session-files/` 或 `uploads/`）| **是**（copy 到 `~/.lumen/session-files/{conv_id}/`） |
| 复制时机 | 前端选文件后立即调用 `/api/upload` | 发送消息时后端统一复制 |
| 复制内容 | 原文件完整副本 | 原文件完整副本（仅 copy，不解析） |
| 元数据注册 | SessionFileRegistry（sidecar JSON）| 无注册表，文件系统即真相 |
| 目录支持 | 是（`fs.cp` recursive）| 否（仅单文件） |
| 谁解析文件 | Agent / Skill | Agent Tool (`file_read`) |
| 是否阻塞 SSE | 否 | 否 |
| 图片多模态 | `[attached_image: path]` 标记 | `BinaryContent` 直接注入 |

**Lumen 与 OpenHanako 的差异**：
- Lumen 少了一次前端-后端往返（OpenHanako 先 upload 再发消息，Lumen 发送时一次性完成）
- Lumen 无 SessionFileRegistry、无 fileId、无 kind/mime 推断，更轻量
- 两者都复制文件，解决了"原文件删除后 Agent 读不到"的问题

---

## 结论

桌面版的核心收益：
1. **SSE 启动延迟大幅缩短** — 复制仅涉及文件系统 IO（无解析），延迟从 Docling 的分钟级降到秒级，与文件总大小线性相关
2. **内存安全** — Docling 在 Agent tool 中运行，失败只影响单个 tool call，不会 kill 后端进程
3. **文件生命周期绑定** — 副本随会话管理，对话删除时自动清理，不污染用户原文件
4. **代码简化** — 删除 multipart upload 路由、Attachment ORM、uploads 目录、解析中间层
