# Story: 修复 Cognee 1.0.5 集成

## 背景

项目使用 Cognee 1.0.5 作为语义记忆层，但当前所有 Cognee 调用均在静默失败（被 `try/except` 捕获后回退到 SQLite FTS5）。用户看到状态"就绪"，但实际上语义搜索从未工作过。

## 根本原因（4 个 Bug）

### Bug 1：config 方式错误
`init_cognee()` 用 `os.environ` 设置路径，但 Cognee 1.0 在 import 时就读取配置，之后设置 env var 无效。必须用 `cognee.config.*` API。

### Bug 2：`cognee.remember()` 参数错误
```python
# 当前（错误）—— metadata 不在 remember() 签名里，TypeError 被 except 吃掉
await cognee.remember(content, metadata={...})

# 正确签名
cognee.remember(data: str, dataset_name='main_dataset', *, session_id=None, ...)
```

### Bug 3：缺少 `cognify()` 调用
Cognee 1.0 workflow：`remember()` 只是存数据，`cognify()` 才把数据建成可搜索的知识图谱。当前代码只 remember 不 cognify，数据永远不进索引，`recall()` 永远返回空。

### Bug 4：`datetime.UTC` AttributeError
```python
# 当前（错误）—— from datetime import datetime 导入的是 class，class 没有 .UTC
from datetime import datetime
datetime.now(datetime.UTC)  # AttributeError，被 except 捕获

# 修复
from datetime import datetime, timezone
datetime.now(timezone.utc)
```

## 技术决策

- **单用户模式**：禁用 Cognee 自带的 access control（`ENABLE_BACKEND_ACCESS_CONTROL=false`），简化实现
- **数据集名**：固定用 `"lumen_main"`（单用户，不需要 per-user dataset）
- **cognify 策略**：定时触发，默认每 60 秒执行一次（可通过 `COGNEE_COGNIFY_INTERVAL_SEC` env var 覆盖），只在有新数据时才执行
- **数据目录**：`cognee.config.data_root_directory("~/.lumen")` 把 kuzu/lancedb 都放到 `~/.lumen/` 下

## 涉及文件

```
app/backend/agent/cognee_client.py      ← 重写
app/backend/services/cognee_service.py  ← 重写
app/backend/services/cognee_projector.py ← 修 datetime.UTC
app/backend/main.py                     ← 新增 cognify loop 启动
```

---

## 实现规格

### 1. `app/backend/agent/cognee_client.py`（全部重写）

```python
"""Cognee 1.0.5 client — kuzu + lancedb，单用户模式"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_cognee_status: str = "not_initialized"
_needs_cognify: bool = False  # 有新数据待处理时置 True

USER_DATA_DIR = Path.home() / ".lumen"
COGNEE_DATASET = "lumen_main"
COGNEE_COGNIFY_INTERVAL_SEC = int(os.environ.get("COGNEE_COGNIFY_INTERVAL_SEC", "60"))


def get_cognee_status() -> str:
    return _cognee_status


def mark_needs_cognify() -> None:
    """remember() 后调用，标记需要 cognify"""
    global _needs_cognify
    _needs_cognify = True


def init_cognee() -> str:
    """初始化 Cognee。必须在 import cognee 之前完成配置。"""
    global _cognee_status

    # 单用户模式：禁用 Cognee 内置 access control
    os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"

    try:
        import cognee

        # 用 cognee.config API 配置（不能用 os.environ，import 后设无效）
        cognee.config.data_root_directory(str(USER_DATA_DIR))

        _cognee_status = "ready"
        logger.info("Cognee initialized, data_root=%s", USER_DATA_DIR)
        return _cognee_status
    except ImportError:
        logger.warning("Cognee not installed")
        _cognee_status = "not_installed"
        return _cognee_status
    except Exception as exc:
        logger.error("Cognee init failed: %s", exc)
        _cognee_status = "error"
        return _cognee_status


async def cognify_loop() -> None:
    """后台异步循环：有新数据时每 X 秒执行一次 cognify。在 FastAPI lifespan 中以 task 启动。"""
    import asyncio

    while True:
        await asyncio.sleep(COGNEE_COGNIFY_INTERVAL_SEC)

        global _needs_cognify
        if not _needs_cognify or _cognee_status != "ready":
            continue

        _needs_cognify = False
        try:
            import cognee

            await cognee.cognify(datasets=[COGNEE_DATASET])
            logger.info("Cognee cognify completed, dataset=%s", COGNEE_DATASET)
        except Exception as exc:
            logger.error("Cognee cognify failed: %s", exc)
            _needs_cognify = True  # 失败则下个周期重试
```

### 2. `app/backend/services/cognee_service.py`（全部重写）

```python
"""Cognee 1.0.5 封装：失败时回退到 SQLite FTS5。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.backend.db.base import get_async_session_maker
from app.backend.logging_config import get_logger

logger = get_logger(__name__)

# 从 cognee_client 引入常量，避免重复定义
from app.backend.agent.cognee_client import COGNEE_DATASET, USER_DATA_DIR, mark_needs_cognify


async def remember(user_id: str, content: str, metadata: dict[str, Any] | None = None) -> bool:
    """存储记忆到 Cognee。成功后标记需要 cognify。metadata 参数保留签名兼容，实际不传给 Cognee。"""
    try:
        import cognee

        await cognee.remember(content, dataset_name=COGNEE_DATASET)
        mark_needs_cognify()
        logger.debug("Cognee remember ok", user_id=user_id, content_length=len(content))
        return True
    except Exception as exc:
        logger.error("Cognee remember failed", user_id=user_id, error=str(exc))
        return False


async def recall(user_id: str, query: str, limit: int = 10) -> list[dict[str, str | None]]:
    """语义搜索。失败时回退到 SQLite FTS5。返回 [{"text":..., "event_id":..., ...}]。"""
    try:
        import cognee

        raw_results = await cognee.recall(query, datasets=[COGNEE_DATASET], top_k=limit)
        results = []
        for r in raw_results or []:
            text = _extract_recall_text(r)
            if text:
                results.append({"text": text, "event_id": None, "event_type": None, "created_at": None})
        if results:
            return results
    except Exception as exc:
        logger.warning("Cognee recall failed, fallback to SQLite", user_id=user_id, error=str(exc))

    return await _recall_from_sqlite(user_id, query, limit)


def _extract_recall_text(result: Any) -> str | None:
    """从 recall() 各种返回类型中提取文本。"""
    # ResponseGraphEntry
    text = getattr(result, "text", None)
    if text:
        return str(text)
    # ResponseQAEntry
    answer = getattr(result, "answer", None)
    if answer:
        return str(answer)
    # ResponseGraphContextEntry
    content = getattr(result, "content", None)
    if content:
        return str(content)
    return None


async def clear_user_index(user_id: str) -> bool:
    """清除 Cognee 索引（单用户模式：删除整个数据目录后重初始化）。"""
    try:
        from app.backend.agent.cognee_client import init_cognee

        for name in ("kuzu", "lancedb"):
            path = Path(USER_DATA_DIR) / name
            if path.exists():
                shutil.rmtree(path, ignore_errors=False)

        init_cognee()
        logger.info("Cognee index cleared", user_id=user_id)
        return True
    except Exception as exc:
        logger.error("Cognee clear_user_index failed", user_id=user_id, error=str(exc))
        return False


async def rebuild_from_sqlite(user_id: str) -> bool:
    """从 SQLite 全量重建 Cognee 索引。"""
    try:
        import cognee

        from app.backend.models.growth_event import GrowthEvent

        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at)
            )
            events = result.scalars().all()

            for event in events:
                content = event.payload_json or f"{event.event_type}: {event.entity_type or 'unknown'}"
                await cognee.remember(content, dataset_name=COGNEE_DATASET)
                event.projected_cognee_at = datetime.now(timezone.utc)  # 修复：timezone.utc

            await db.commit()

        mark_needs_cognify()
        logger.info("Cognee rebuild queued from SQLite", user_id=user_id, events_count=len(events))
        return True
    except Exception as exc:
        logger.error("Cognee rebuild failed", user_id=user_id, error=str(exc))
        return False


# ── SQLite FTS5 回退（不修改，保留原逻辑）──────────────────────────────────────

async def _recall_from_sqlite(user_id: str, query: str, limit: int) -> list[dict[str, str | None]]:
    """Cognee 不可用时的回退：FTS5 全文匹配，无结果则按时间倒序。"""
    try:
        import re as _re

        from sqlalchemy import text

        from app.backend.models.growth_event import GrowthEvent

        _CJK_RE = _re.compile(r"[一-鿿㐀-䶿豈-﫿]")

        async with get_async_session_maker()() as db:
            if query and query.strip():
                fts_table = "growth_events_fts_trigram" if _CJK_RE.search(query) else "growth_events_fts"
                fts_sql = text(f"""
                    SELECT ge.id, ge.payload_json, ge.event_type, ge.entity_type, ge.created_at
                    FROM growth_events ge
                    JOIN {fts_table} fts ON fts.rowid = ge.rowid
                    WHERE ge.user_id = :uid AND {fts_table} MATCH :q
                    ORDER BY ge.created_at DESC
                    LIMIT :lim
                """)
                rows = (await db.execute(fts_sql, {"uid": user_id, "q": query, "lim": limit})).all()
                memories = []
                for row in rows:
                    event = GrowthEvent(
                        id=row[0], payload_json=row[1], event_type=row[2],
                        entity_type=row[3], created_at=row[4],
                    )
                    memories.append({
                        "text": _format_event_text(event),
                        "event_id": str(row[0]),
                        "event_type": row[2],
                        "created_at": row[4].isoformat() if row[4] else None,
                    })
                if memories:
                    return memories

            result = await db.execute(
                select(GrowthEvent)
                .where(GrowthEvent.user_id == user_id)
                .order_by(GrowthEvent.created_at.desc())
                .limit(limit)
            )
            events = result.scalars().all()

        return [
            {
                "text": _format_event_text(event),
                "event_id": str(event.id),
                "event_type": event.event_type,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events
        ]
    except Exception as exc:
        logger.error("SQLite recall failed", user_id=user_id, error=str(exc))
        return []


def _format_event_text(event) -> str:
    payload = {}
    if event.payload_json:
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            payload = {}

    if event.event_type == "skill_added":
        skill = payload.get("skill_name") or payload.get("name") or event.entity_id or "未知技能"
        level = payload.get("level", "未知水平")
        return f"掌握了 {skill}（{level}）"
    if event.event_type == "profile_updated":
        if payload.get("memory_md"):
            return "更新了核心画像"
        school = payload.get("school_name", "")
        major = payload.get("major", "")
        return f"更新了画像：{school} {major}".strip()
    if event.event_type == "experience_added":
        title = payload.get("title", event.entity_id or "未知经历")
        desc = payload.get("description", "")
        return f"经历：{title} — {desc}" if desc else f"经历：{title}"
    if payload:
        return f"{event.event_type}: {json.dumps(payload, ensure_ascii=False)}"
    return f"{event.event_type}: {event.entity_type or 'unknown'}"
```

### 3. `app/backend/services/cognee_projector.py`（只修 Bug 4）

在文件第 7 行，将：
```python
from datetime import datetime
```
改为：
```python
from datetime import datetime, timezone
```

在第 61 行，将：
```python
event.projected_cognee_at = datetime.now(datetime.UTC)
```
改为：
```python
event.projected_cognee_at = datetime.now(timezone.utc)
```

### 4. `app/backend/main.py`（新增 cognify_loop 启动）

在 `lifespan` 函数中，找到这段：

```python
    # Cognee 记忆层初始化（后台线程，不阻塞启动）
    import threading

    from app.backend.agent.cognee_client import init_cognee

    threading.Thread(target=init_cognee, daemon=True, name="cognee-init").start()
```

替换为：

```python
    # Cognee 记忆层初始化（后台线程）+ cognify 定时循环（async task）
    import asyncio
    import threading

    from app.backend.agent.cognee_client import cognify_loop, init_cognee

    threading.Thread(target=init_cognee, daemon=True, name="cognee-init").start()
    asyncio.create_task(cognify_loop(), name="cognee-cognify-loop")
```

---

## 验收标准

1. 启动后端，日志中出现 `Cognee initialized, data_root=~/.lumen`（不是默认的 package 目录）
2. 调用 `GET /api/memory/stats` 返回 `{"status": "ready", ...}`
3. 向 AI 发几条消息（会触发 `remember()`），等待 60 秒后调用 `GET /api/memory/search?q=xxx` 返回非空语义结果
4. 调用 `POST /api/memory/rebuild` 不报错，日志中出现 `Cognee rebuild queued from SQLite`
5. 后端不出现 `datetime` 相关 AttributeError

## 不需要修改的文件

- `app/backend/services/cognee_projector.py` 中 `_build_memory_content()` 逻辑 ✓
- `app/backend/routers/memory.py` ✓
- `app/backend/services/lumen_memory.py` ✓
- 前端所有文件 ✓
- `requirements.txt` ✓（cognee==1.0.5 已正确声明）
