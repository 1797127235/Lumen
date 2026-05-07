# Code Review: memory 层重构

**Reviewed**: 2026-05-07
**Scope**: 13 new files + 5 modified files
**Decision**: APPROVE — 2 HIGH, 3 MEDIUM, 1 LOW

---

## Summary

整体架构清晰，分层正确，ruff check + format 全部通过。以下问题不影响合并，但建议迭代修复。

---

## HIGH

### 1. search.py:32 `search_all()` — 100 行，三个搜索策略耦合在一个函数里

三个搜索逻辑（Cognee / FTS5 / .md）共用 `seen` set 和 `results` list，但各自完全不同。100 行函数可读性差，新增搜索源时必须改这个函数。

**建议**：拆成 `_search_cognee()`, `_search_fts5()`, `_search_md()` 三个私有函数，`search_all()` 只做编排。

```python
async def search_all(user_id, query, limit=10, *, include_cognee=True):
    seen: set[str] = set()
    results: list[MemoryItem] = []

    if include_cognee:
        results.extend(await _search_cognee(query, limit, seen))
    results.extend(await _search_fts5(user_id, query, limit, seen))
    results.extend(await _search_md(user_id, query, seen))

    return results[:limit]
```

### 2. facade.py `remember()` / `remember_batch()` — 重复的 session 管理逻辑

两个函数都在做：
```python
if db is not None:
    # flush only
else:
    async with get_async_session_maker()() as db:
        # commit + projections
```

这个模式出现 4 次。如果以后第三方也调 facade 传 db，任何地方写错都可能导致事务不回滚。

**建议**：抽一个 `_write_events()` 私有方法：

```python
async def _write_events(self, user_id, events, db):
    repo = GrowthEventRepository(db)
    created = []
    for spec in events:
        event = await repo.create_with_dedup(user_id=user_id, **spec)
        if event:
            created.append(event)
    if created:
        await db.flush()
    return created
```

`remember()` 和 `remember_batch()` 各自只关心「拿 session → 调用 _write_events → commit/flush + projections」。

---

## MEDIUM

### 3. search.py:70-73 — SQL 表名插值

```python
if _CJK_RE.search(query):
    fts_table = "growth_events_fts_trigram"
else:
    fts_table = "growth_events_fts"

fts_sql = text(f"""
    SELECT ... FROM growth_events ge
    JOIN {fts_table} fts ON ...
""")
```

虽然 `fts_table` 只有两个硬编码值，SQL 注入风险极低，但 SQL 字面量插值总是一个坏信号。

**建议**：用 `case` 表达式或 SQL 子查询避免字符串插值：

```python
fts_sql = text("""
    SELECT ge.id, ge.payload_json, ge.event_type, ge.entity_type, ge.created_at
    FROM growth_events ge
    JOIN (
        SELECT rowid FROM growth_events_fts WHERE ... 
        UNION ALL
        SELECT rowid FROM growth_events_fts_trigram WHERE ...
    ) fts ON fts.rowid = ge.rowid
    ...
""")
```

### 4. facade.py:296 `_build_cognee_content()` — 模块级函数，不属于 facade

这个函数决定什么数据进 Cognee、什么不进。它现在在 `facade.py` 底部，但语义上属于 `SemanticStore` 的职责。

**建议**：移到 `stores/semantic.py` 作为 `SemanticStore.build_event_content(event)` 类方法。

### 5. markdown.py:459 行 + `projections/snapshot.py` 重复的字符限制常量

`snapshot.py` 重复定义了 `MEMORY_CHAR_LIMIT`/`SKILLS_CHAR_LIMIT`/`EXPERIENCES_CHAR_LIMIT`，和 `markdown.py` 的相同。

**建议**：抽到 `memory/constants.py`：

```python
# memory/constants.py
MD_CHAR_LIMITS = {
    "memory": 4000,
    "skills": 3000,
    "experiences": 5000,
}
```

然后 `markdown.py` 和 `snapshot.py` 都从常量模块导入。

---

## LOW

### 6. cognify_loop.py:35 `os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"` — 副作用

在 `init_cognee()` 中设置环境变量是 Cognee 要求的，但这是个副作用。调用多次只第一次有效。如果后续 Cognee 版本改了这个环境变量名，会静默失效。

**建议**：加一行注释说明为什么这样做。

---

## Validation Results

| Check | Result |
|---|---|
| ruff check | ✅ Pass |
| ruff format | ✅ 40 files formatted |

## Files Reviewed

| File | Status | Lines |
|---|---|---|
| `memory/__init__.py` | **New** | 24 |
| `memory/cognee_admin/__init__.py` | **New** | 4 |
| `memory/cognee_admin/datasets.py` | **New** | 16 |
| `memory/cognee_admin/cognify_loop.py` | **New** | 52 |
| `memory/facade.py` | **New** | 285 |
| `memory/projections/__init__.py` | **New** | 0 |
| `memory/projections/markdown.py` | **New** | 459 |
| `memory/projections/snapshot.py` | **New** | 47 |
| `memory/search.py` | **New** | 133 |
| `memory/stores/__init__.py` | **New** | 0 |
| `memory/stores/documents.py` | **New** | 47 |
| `memory/stores/relational.py` | **New** | 97 |
| `memory/stores/semantic.py` | **New** | 65 |
| `agent/pydantic_tools.py` | **Modified** | 197 |
| `agent/pydantic_agent.py` | **Modified** | 143 |
| `main.py` | **Modified** | 139 |
| `routers/health.py` | **Modified** | 7 |
| `routers/memory.py` | **Modified** | 257 |
| `services/chat_service.py` | **Modified** | 289 |
| (9 deleted) | **Deleted** | – |
