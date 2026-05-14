# Lumen 外部数据接入 — 设计补充

> 补充文档：针对 `external-data-mcp-design.md` 中缺失的安全、错误处理、可观测性、文件监听与搜索集成维度的详细设计。
>
> 状态：DRAFT | 生成于 CEO Review | 适用 Phase 2a/2b/2c

---

## 1. 安全设计（Security Architecture）

### 1.1 核心威胁模型

| 威胁 | 可能性 | 影响 | 缓解策略 |
|------|--------|------|----------|
| 目录遍历攻击 | **高** | **高** | 路径白名单 + `realpath` 解析 |
| 敏感文件索引（密钥/凭证） | **中** | **高** | 默认忽略列表 + 用户自定义排除 |
| GitHub Token 泄露 | **中** | **高** | `.env` 存储 + 日志脱敏 |
| MCP Server 命令注入 | **低** | **高** | 参数化调用 + 输入校验 |
| LLM 提示注入（通过文件内容） | **中** | **中** | 文件内容基础过滤 |
| 资源耗尽（超大文件/无限递归） | **中** | **中** | 文件大小上限 + 递归深度限制 |

### 1.2 路径验证（Path Validation）

所有外部数据路径必须经过三层校验：

```
用户输入路径
    ↓
[Layer 1] 格式校验 — 必须是绝对路径，拒绝相对路径
    ↓
[Layer 2] realpath 解析 — 解析符号链接，获取真实物理路径
    ↓
[Layer 3] 白名单校验 — 必须在 `EXTERNAL_DATA_PATHS` 配置范围内
    ↓
[Layer 4] 子路径校验 — 拒绝包含 `..` 或绝对路径跳转的相对段
```

**白名单机制**：
- 配置项：`EXTERNAL_DATA_PATHS`（逗号分隔的绝对路径列表）
- 启动时验证路径存在且可读
- 运行时所有文件操作前执行 `is_path_allowed()` 检查
- 违反时记录安全日志并返回 403

**路径校验伪代码**：
```python
def validate_external_path(user_path: str, allowed_paths: list[str]) -> str:
    # Layer 1: 必须是绝对路径
    if not os.path.isabs(user_path):
        raise SecurityError("Path must be absolute")
    
    # Layer 2: 解析真实路径
    real_path = os.path.realpath(user_path)
    
    # Layer 3: 白名单检查
    for allowed in allowed_paths:
        real_allowed = os.path.realpath(allowed)
        if real_path.startswith(real_allowed + os.sep) or real_path == real_allowed:
            return real_path
    
    # Layer 4: 安全审计
    audit_log.warning("Path traversal attempt", 
                      attempted=user_path, 
                      resolved=real_path)
    raise SecurityError("Path not in allowed list")
```

### 1.3 敏感文件过滤

**默认排除列表**（不可覆盖）：
```python
SENSITIVE_PATTERNS = {
    # 凭证类
    "*.pem", "*.key", "*.pfx", "*.p12", "*.crt",
    ".env", ".env.*", "*.secret", "*.credentials",
    
    # 密钥类
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "*.keystore", "*.jks",
    
    # 系统类
    "/etc/passwd", "/etc/shadow", "/etc/hosts",
    
    # 版本控制内部
    ".git/", ".svn/", ".hg/",
    
    # 依赖目录
    "node_modules/", "vendor/", "__pycache__/",
    
    # 构建产物
    "dist/", "build/", "target/",
    
    # 临时文件
    "*.tmp", "*.temp", "*.swp", "*.swo", "*~",
}
```

**用户自定义排除**：
- 配置项：`EXTERNAL_DATA_IGNORE_PATTERNS`（支持 glob 语法）
- 在默认排除列表基础上追加
- 不支持取消默认排除（安全基线不可突破）

**过滤执行点**：
1. 文件扫描阶段：发现匹配文件时跳过并记录 debug 日志
2. 索引阶段：二次校验防止绕过
3. 搜索阶段：只返回通过过滤的文件结果

### 1.4 GitHub Token 安全

| 要求 | 实现 |
|------|------|
| 存储位置 | `.env` 文件，`GITHUB_TOKEN` 键 |
| 前端可见性 | **不可见**。后端读取，绝不传递到前端 |
| 日志处理 | 自动脱敏，替换为 `ghp_***` |
| 失效检测 | 401 响应时标记 Token 失效，通知用户重新配置 |
| 权限最小化 | 只读权限（不需要 write access） |

**Token 脱敏函数**：
```python
def mask_token(token: str) -> str:
    if len(token) > 8:
        return f"{token[:4]}***{token[-4:]}"
    return "***"
```

---

## 2. 错误处理策略（Error & Rescue Strategy）

### 2.1 错误分层模型

```
L1: 瞬态错误 — 网络抖动、短暂锁竞争
  → 指数退避重试（最多 3 次）

L2: 持续错误 — MCP Server 不可用、磁盘满
  → 降级到本地实现 / 返回空结果

L3: 致命错误 — 配置错误、权限丢失
  → 记录错误，通知用户，停止重试
```

### 2.2 MCP 调用错误处理

**通用 MCP 调用装饰器**：
```python
import asyncio
from functools import wraps
from typing import Callable, TypeVar

T = TypeVar("T")

async def safe_mcp_call(
    coro: Callable[[], T],
    *,
    timeout: float = 5.0,
    max_retries: int = 2,
    fallback: Callable[[], T] | None = None,
    error_context: dict | None = None,
) -> T | None:
    """安全执行 MCP 调用。
    
    策略：
    - 超时：默认 5 秒（文件搜索可能慢，读取应该快）
    - 重试：指数退避 1s → 2s → 4s
    - 降级：重试耗尽后执行 fallback
    - 日志：每次失败记录详细上下文
    """
    context = error_context or {}
    
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(coro(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "MCP call timeout",
                attempt=attempt + 1,
                max_retries=max_retries,
                timeout=timeout,
                **context,
            )
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
        except ConnectionError as e:
            logger.error(
                "MCP connection error",
                error=str(e),
                attempt=attempt + 1,
                **context,
            )
            # 连接错误不 retry，直接 fallback
            break
        except Exception as e:
            logger.exception(
                "MCP call unexpected error",
                error_type=type(e).__name__,
                **context,
            )
            break
    
    # 所有重试耗尽，执行 fallback
    if fallback:
        logger.info("MCP call falling back to local implementation", **context)
        try:
            return fallback()
        except Exception as e:
            logger.error("Fallback also failed", error=str(e), **context)
    
    return None
```

**各 MCP 工具的 fallback 策略**：

| MCP 工具 | 主要错误 | Fallback | 用户感知 |
|----------|---------|----------|---------|
| `search_files` | 连接超时 | 本地 `os.walk()` 搜索 | 无（结果可能不全） |
| `read_file` | 文件不存在 | 返回空字符串 | 无（Agent 知道文件不存在） |
| `list_commits` | GitHub API 限流 | 返回空列表 | 提示「GitHub 暂时不可用」 |
| `get_file_contents` | 权限不足 | 返回空字符串 | 无 |

### 2.3 文件操作错误处理

| 场景 | 异常 | 处理 | 日志级别 |
|------|------|------|---------|
| 文件被删除（竞争条件） | `FileNotFoundError` | 跳过，记录 warning | WARNING |
| 无读取权限 | `PermissionError` | 跳过，记录 warning | WARNING |
| 文件被其他进程锁定 | `OSError` (errno=13/32) | 跳过，下次扫描重试 | WARNING |
| 编码错误（非 UTF-8） | `UnicodeDecodeError` | 尝试 latin-1/gbk 回退，失败则跳过 | WARNING |
| 符号链接循环 | `OSError` (errno=40) | 检测递归深度 >10，跳过 | ERROR |

### 2.4 索引层错误处理

**FTS5 索引写入**：
```python
async def safe_index_document(doc_id: str, content: str, db: AsyncSession):
    for attempt in range(3):
        try:
            await insert_to_fts5(doc_id, content, db)
            return True
        except OperationalError as e:
            if "database is locked" in str(e):
                logger.warning("FTS5 locked, retrying", doc_id=doc_id, attempt=attempt)
                await asyncio.sleep(0.5 * (2 ** attempt))
            else:
                raise
    logger.error("FTS5 index failed after retries", doc_id=doc_id)
    return False
```

**Cognee 索引写入**：
- Cognee 失败 **不阻塞** 主流程
- 记录失败文件，后台重试队列
- 如果 Cognee 持续失败，自动降级到 FTS5（Phase 2c）

---

## 3. 可观测性设计（Observability）

### 3.1 结构化日志规范

**日志字段标准**（所有外部数据操作）：
```python
{
    "event": "external_data.index",      # 事件类型
    "user_id": "demo_user",               # 用户标识
    "source": "filesystem",               # 数据来源: filesystem/github
    "file_path": "/home/user/notes/",     # 文件路径（相对白名单根目录）
    "file_size": 1024,                    # 文件大小（字节）
    "chunks": 3,                          # 分块数量
    "duration_ms": 45.2,                  # 处理耗时
    "status": "success",                  # success | skipped | failed
    "error": None,                        # 失败时填充
    "timestamp": "2026-05-12T10:30:00Z",  # ISO 8601
}
```

**关键日志事件**：

| 事件 | 级别 | 触发条件 |
|------|------|---------|
| `external_data.config_loaded` | INFO | 外部数据配置加载成功 |
| `external_data.scan_started` | INFO | 开始扫描目录 |
| `external_data.scan_completed` | INFO | 扫描完成，报告文件数/耗时 |
| `external_data.file_indexed` | DEBUG | 单个文件索引成功 |
| `external_data.file_skipped` | DEBUG | 文件被跳过（敏感/大文件/忽略列表） |
| `external_data.index_failed` | WARNING | 单个文件索引失败 |
| `external_data.mcp_timeout` | WARNING | MCP 调用超时 |
| `external_data.security.blocked` | WARNING | 路径被安全规则拦截 |
| `external_data.search.queried` | DEBUG | 搜索查询 |
| `external_data.search.slow` | WARNING | 搜索耗时 > 2s |

### 3.2 指标设计（Metrics）

**Prometheus-style 指标**（如果未来接入监控系统）：

```
# 索引指标
lumen_external_indexed_files_total{source="filesystem"} 127
lumen_external_indexed_chunks_total{source="filesystem"} 342
lumen_external_index_failures_total{source="filesystem",reason="timeout"} 2
lumen_external_index_duration_seconds{quantile="0.99"} 0.45

# 文件监听指标
lumen_external_watcher_events_total{event_type="created"} 15
lumen_external_watcher_events_total{event_type="modified"} 42
lumen_external_watcher_events_total{event_type="deleted"} 3
lumen_external_watcher_debounce_aggregated 12  # 被 debounce 合并的事件数

# 搜索指标
lumen_external_search_duration_seconds{source="fts5"} 0.023
lumen_external_search_duration_seconds{source="cognee"} 0.156
lumen_external_search_results_count{source="fts5"} 5

# MCP 指标
lumen_mcp_calls_total{tool="search_files",status="success"} 89
lumen_mcp_calls_total{tool="search_files",status="timeout"} 3
lumen_mcp_call_duration_seconds{tool="search_files"} 0.12
```

**最小实现**（无 Prometheus 依赖）：
- 使用 Python `statistics` 模块在内存中维护滑动窗口统计
- 每 60 秒输出一次摘要日志
- 通过 `/api/health` 或新 `/api/external-data/stats` 端点暴露

### 3.3 状态追踪

**索引状态机**：

```
[未配置] → [扫描中] → [索引中] → [就绪]
              ↓           ↓
           [错误] ← [部分就绪]
```

**状态存储**：
- 内存中：`IndexStatusTracker` 类（单例）
- 持久化：SQLite 表 `external_index_status`

**状态表结构**：
```sql
CREATE TABLE external_index_status (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,           -- 'filesystem' | 'github'
    source_path TEXT NOT NULL,      -- 目录路径或 repo 名
    status TEXT NOT NULL,           -- 'scanning' | 'indexing' | 'ready' | 'error' | 'partial'
    total_files INTEGER,
    indexed_files INTEGER,
    failed_files INTEGER,
    last_scan_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**前端状态推送**：
- SSE 流：`/api/external-data/status-stream`
- 轮询 fallback：`GET /api/external-data/status`
- 状态变化时主动推送（扫描完成、索引进度、错误发生）

### 3.4 调试能力

**问题排查清单**：

| 问题 | 排查命令/日志 |
|------|--------------|
| 文件未被索引 | 检查 `external_data.file_skipped` 日志，确认是否在忽略列表 |
| 搜索结果为空 | 检查 `external_data.search.queried` 日志，确认 query 和 scope |
| 索引卡住 | 检查 `external_data.scan_completed` 是否到达，对比 `indexed_files`/`total_files` |
| MCP 不可用 | 检查 `external_data.mcp_timeout` 频率，确认 MCP Server 进程 |
| Cognee 失败 | 检查 `CogneeProvider` 或 `cognify_loop.py` 的 error 日志 |

**调试端点**（仅 DEBUG 模式）：
```
GET /api/external-data/debug/validate-path?path=/home/user/notes
→ {"valid": true, "resolved": "/home/user/notes", "allowed_by": "/home/user"}

GET /api/external-data/debug/list-ignored?path=/home/user/notes
→ {"ignored_files": [".env", "id_rsa"], "reasons": ["sensitive", "sensitive"]}
```

---

## 4. 文件监听与索引策略

### 4.1 文件监听架构

```
文件系统事件（watchdog/notify）
    ↓
Event Queue（内存队列）
    ↓
Debounce Processor（1 分钟窗口）
    ↓
Content Deduplicator（MD5 校验）
    ↓
Batch Indexer（每批 50 个文件）
    ↓
FTS5 / Cognee 写入
```

### 4.2 Debounce 机制

**策略**：时间窗口 + 事件聚合

```python
class DebouncedWatcher:
    def __init__(self, window_seconds: float = 60.0):
        self.window = window_seconds
        self.pending: dict[str, FileEvent] = {}  # path -> latest_event
        self._timer: asyncio.TimerHandle | None = None
    
    def on_file_event(self, path: str, event_type: str):
        """文件变更时调用。"""
        self.pending[path] = FileEvent(path, event_type, time.time())
        self._schedule_flush()
    
    def _schedule_flush(self):
        """安排窗口结束后的批量处理。"""
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.get_event_loop().call_later(
            self.window, self._flush
        )
    
    async def _flush(self):
        """窗口结束：批量处理所有 pending 事件。"""
        batch = list(self.pending.values())
        self.pending.clear()
        
        # 合并同一文件的多次事件（取最新）
        await self.indexer.process_batch(batch)
```

**行为**：
- 用户连续编辑文件 A 5 次（1 分钟内）：只索引最后一次的内容
- 用户编辑文件 A、B、C：1 分钟后一次性批量索引 3 个文件
- 用户 2 分钟后又编辑文件 A：新的 debounce 窗口，正常索引

### 4.3 内容去重（Content Hash）

**目的**：避免重复索引相同内容的文件

```python
import hashlib

class ContentDeduplicator:
    def __init__(self):
        # 内存缓存 + SQLite 持久化
        self._cache: dict[str, str] = {}  # path -> md5
    
    def is_changed(self, path: str, content: str) -> bool:
        """返回 True 如果内容确实变了。"""
        new_hash = hashlib.md5(content.encode()).hexdigest()
        old_hash = self._cache.get(path)
        
        if old_hash == new_hash:
            return False  # 内容没变，跳过索引
        
        self._cache[path] = new_hash
        return True
```

**存储**：
- 内存：运行期缓存
- SQLite：`external_file_hashes(path TEXT PRIMARY KEY, md5 TEXT, indexed_at TIMESTAMP)`
- 启动时从 SQLite 加载到内存

### 4.4 批量索引

**批次大小**：50 个文件/批（可配置）
**并发度**：最多 4 个文件同时处理（受 GIL 和 SQLite 锁限制）
**进度报告**：每完成一批，更新 `external_index_status` 表并推送 SSE

```python
async def index_batch(files: list[FileEvent]):
    semaphore = asyncio.Semaphore(4)
    
    async def index_one(file: FileEvent):
        async with semaphore:
            content = await read_file(file.path)
            if not content_dedup.is_changed(file.path, content):
                return {"path": file.path, "status": "skipped", "reason": "no_change"}
            
            chunks = chunk_text(content)
            for chunk in chunks:
                await safe_index_document(f"{file.path}#{chunk.index}", chunk.text, db)
            
            return {"path": file.path, "status": "indexed", "chunks": len(chunks)}
    
    results = await asyncio.gather(*[index_one(f) for f in files])
    return results
```

### 4.5 初始索引（首次配置）

**场景**：用户配置了一个有 1000 个文件的 Obsidian 目录

**策略**：
1. **扫描阶段**：快速遍历目录，统计文件数，过滤敏感/大文件
2. **分阶段索引**：每 50 个文件为一批，间隔 100ms（避免阻塞）
3. **进度报告**：每批完成后更新进度（"索引中 350/1000..."）
4. **可取消**：用户关闭设置页时，后台继续完成当前批次，优雅停止

**性能预估**：
- 1000 个 .md 文件，平均 5KB/文件
- 总内容 5MB，分块后约 6000 个 chunks
- FTS5 索引耗时：约 30-60 秒
- Cognee 索引耗时：约 3-5 分钟（含 cognify）

---

## 5. 搜索集成设计

### 5.1 数据来源标签（Source Tagging）

**标签体系**：

| 标签 | 来源 | 说明 |
|------|------|------|
| `internal` | GrowthEvent (Narrative) | 用户对话中产生的记忆 |
| `external:filesystem` | 本地文件系统 | Obsidian/笔记目录 |
| `external:github` | GitHub repos | 代码仓库 |
| `profile` | GrowthEvent (Profile) | 用户画像（L0，不进入 L2 搜索） |

**数据模型扩展**：
```python
class MemoryItem:
    id: str
    content: str
    source: str           # "internal" | "external:filesystem" | "external:github"
    source_path: str | None  # 文件路径或 repo 名
    created_at: str | None
    categories: list[str]
```

### 5.2 搜索结果合并

**Phase 2a/2b（FTS5 混合搜索）**：

```
用户查询: "Rust 笔记"
    ↓
[Parallel Query]
    ├── FTS5(internal) → [记忆1, 记忆2]
    └── FTS5(external) → [文件A#chunk1, 文件A#chunk2, 文件B#chunk1]
    ↓
[Merge & Rank]
    - 合并结果，按相关性排序
    - 同一文件的多 chunks 合并为一个结果项
    - 标注来源标签
    ↓
[返回]
    - [internal] 用户提到在学 Rust（来自对话）
    - [external:filesystem] Rust 学习笔记.md（来自 Obsidian）
    - [external:filesystem] Cargo.toml 配置说明（来自 Obsidian）
```

**Phase 2c（FTS5 + Cognee 混合搜索）**：

```
用户查询: "设计模式"
    ↓
[Parallel Query]
    ├── FTS5(internal) → [...]
    ├── FTS5(external) → [...]
    └── Cognee(semantic) → [...]
    ↓
[Merge & Deduplicate]
    - Cognee 结果可能包含 FTS5 已返回的文件
    - 去重：优先保留 Cognee 结果（语义更准），标记为 `semantic`
    ↓
[返回]
    - [semantic] 设计模式笔记.md（Cognee 语义匹配）
    - [external:filesystem] 代码实现.py（FTS5 关键词匹配）
    - [internal] 用户讨论过工厂模式（FTS5 内部记忆）
```

### 5.3 Scope 路由

**扩展 `memory_search` 工具的 scope 参数**：

```python
SCOPE_DATASETS = {
    # Phase 1 已有
    "profile": ["lumen_profile"],
    "emotions": ["lumen_emotions"],
    "reference": ["lumen_reference"],
    "chat": ["lumen_chat"],
    
    # Phase 2 新增
    "external": ["external_filesystem", "external_github"],
    "notes": ["external_filesystem"],
    "github": ["external_github"],
    
    # 默认：全部
    None: ["lumen_profile", "lumen_emotions", "lumen_reference", 
           "lumen_chat", "external_filesystem", "external_github"],
}
```

**Agent 工具提示词更新**：
```
scope（仅 keyword 模式生效）：
- "profile"   — 技能/经历/画像/目标/学校等
- "emotions"  — 情绪/焦虑/心情/日记
- "reference" — 公司/行业/学长经验
- "chat"      — 历史对话摘要
- "external"  — 外部数据源（笔记 + GitHub）
- "notes"     — 本地笔记/Obsidian
- "github"    — GitHub 代码仓库
- 不传（None）— 搜索全部（包括外部数据）
```

### 5.4 前端搜索结果展示

**结果分组**：
```
┌─────────────────────────────────────────┐
│ 搜索结果："Rust"                        │
├─────────────────────────────────────────┤
│ 📁 来自你的笔记 (2)                     │
│   • Rust 学习笔记.md                    │
│     "所有权系统是 Rust 的核心特性..."   │
│   • Cargo.toml 配置指南.md              │
│     "[dependencies] 段配置..."          │
├─────────────────────────────────────────┤
│ 💬 来自对话 (1)                         │
│   • 你提到正在学习 Rust（3天前）        │
├─────────────────────────────────────────┤
│ 🐙 来自 GitHub (1)                      │
│   • questionliuxinyu/lumen              │
│     "backend/ 目录下的 Rust 绑定..."    │
└─────────────────────────────────────────┘
```

**交互**：
- 点击外部文件结果 → 打开文件预览（只读）
- 点击 GitHub 结果 → 打开浏览器跳转到对应文件
- 来源标签颜色编码：内部（蓝）、笔记（绿）、GitHub（紫）

---

## 6. 配置管理

### 6.1 后端配置（.env）

```bash
# === Phase 2: 外部数据接入 ===

# 功能总开关
ENABLE_EXTERNAL_DATA=true

# 本地文件系统数据源（逗号分隔的绝对路径）
EXTERNAL_DATA_PATHS=/home/user/Obsidian,/home/user/Documents

# 文件忽略模式（glob，逗号分隔，追加到默认列表）
EXTERNAL_DATA_IGNORE_PATTERNS=*.tmp,*.log,draft/

# GitHub 集成
ENABLE_GITHUB_INTEGRATION=true
GITHUB_TOKEN=ghp_xxxxxxxx
GITHUB_REPOS=questionliuxinyu/lumen,questionliuxinyu/dotfiles

# 索引配置
EXTERNAL_INDEX_BATCH_SIZE=50
EXTERNAL_INDEX_DEBOUNCE_SECONDS=60
EXTERNAL_FILE_SIZE_MAX_MB=5
EXTERNAL_INDEX_WORKERS=4

# 搜索配置
EXTERNAL_SEARCH_TIMEOUT=2.0
EXTERNAL_SEARCH_MAX_RESULTS=20

# 调试（开发环境）
EXTERNAL_DATA_DEBUG_ENDPOINTS=true
```

### 6.2 前端配置界面

**设置页新增「外部数据」卡片**：

```
┌──────────────────────────────────────┐
│ 📂 外部数据                           │
├──────────────────────────────────────┤
│                                      │
│ 本地笔记目录                          │
│ [/home/user/Obsidian    ] [浏览]    │
│ [添加目录]                            │
│                                      │
│ 已连接：                              │
│ ✅ /home/user/Obsidian (127 文件)    │
│   └─ 最后索引：2 分钟前              │
│                                      │
│ GitHub 连接                           │
│ Token: [****************    ]        │
│ Repos: [questionliuxinyu/lumen]      │
│       [+ 添加]                        │
│                                      │
│ [断开连接]  [重新索引]                │
│                                      │
│ 索引状态：🟢 就绪                     │
│ 已索引：127 文件 | 342 块            │
└──────────────────────────────────────┘
```

---

## 7. 实施顺序建议

### Phase 2a（第 1-2 周）：本地文件系统 + FTS5

**Week 1 优先级**：
1. **P0**: 路径验证 + 敏感文件过滤（安全基线）
2. **P0**: 错误处理装饰器 + MCP fallback
3. **P1**: 文件扫描 + 文本提取 + chunk
4. **P1**: FTS5 `external_items` 表 + 索引写入

**Week 2 优先级**：
1. **P1**: 文件监听 + debounce
2. **P1**: 内容哈希去重
3. **P2**: 前端配置界面
4. **P2**: 搜索来源标签

### Phase 2b（第 3 周）：GitHub 接入

1. **P0**: GitHub MCP Server 连接 + Token 安全
2. **P1**: Repo 扫描 + 代码文件提取
3. **P1**: 代码文件特殊 chunk 策略（函数级）
4. **P2**: Commit 历史索引（可选）

### Phase 2c（第 4-5 周）：Cognee 语义搜索

1. **P0**: Ingestion Pipeline 适配 Cognee
2. **P1**: NodeSets 分区（`lumen_external`）
3. **P1**: 混合搜索（FTS5 + Cognee）
4. **P2**: 语义搜索结果去重
5. **P2**: 性能基准测试

---

## 8. 风险评估与缓解

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| MCP Server 生态不成熟 | 中 | 中 | 同时保留本地 fallback 实现 |
| Cognee 稳定性问题 | 中 | 高 | FTS5 作为永久兜底，Cognee 可选开启 |
| 大量文件索引性能差 | 高 | 中 | 批量索引 + debounce + 异步后台 |
| 用户配置路径包含敏感数据 | 高 | 高 | 默认排除列表 + 路径白名单 |
| 文件监听跨平台差异 | 中 | 低 | watchdog 库封装，各平台测试 |
| GitHub API 限流 | 高 | 低 | 本地缓存 + 限流处理 |

---

*文档结束。本补充应与 `external-data-mcp-design.md` 一起阅读。*
