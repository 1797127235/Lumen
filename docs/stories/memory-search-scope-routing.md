# Story: memory_search scope 路由

## 背景

当前 `memory_search` 工具走单一路径搜索所有内容，随着数据增长（日记、公司调研、学习笔记等），不同类型内容会互相污染搜索结果。

**问题根源**：`search_all()` 调用 Cognee 时 `datasets=None`，内部默认只搜 `DATASET_PROFILE`。其他 dataset（`lumen_reflection`、`lumen_reference` 等）里的数据永远无法被语义搜索命中。

**解法**：在 `memory_search` 工具加 `scope` 参数，Agent 根据对话上下文选择搜索范围，内部映射到对应 Cognee dataset 列表。

---

## 变更范围（4 个文件）

### 1. `app/backend/memory/cognee_admin/datasets.py`

新增 scope → datasets 映射常量：

```python
"""Cognee dataset 命名常量 — 新内容类型加入时在此注册。"""

# 用户本人档案（简历、项目描述、经历）
DATASET_PROFILE = "lumen_profile"

# 外部参考（学长帖、公司调研、行业报告）
DATASET_REFERENCE = "lumen_reference"

# 反思与情绪（日记、复盘、随手想法）
DATASET_REFLECTION = "lumen_reflection"

# 对话摘要
DATASET_CHAT = "lumen_chat"

# 所有 dataset 列表，供 cognify loop 遍历
ALL_DATASETS = [
    DATASET_PROFILE,
    DATASET_REFERENCE,
    DATASET_REFLECTION,
    DATASET_CHAT,
]

# Agent scope 参数 → Cognee datasets 映射
# scope=None 时使用 ALL_DATASETS
SCOPE_DATASETS: dict[str, list[str]] = {
    "profile": [DATASET_PROFILE],
    "emotions": [DATASET_REFLECTION],
    "reference": [DATASET_REFERENCE],
    "chat": [DATASET_CHAT],
    "all": ALL_DATASETS,
}
```

---

### 2. `app/backend/memory/search.py`

`search_all()` 和 `_search_cognee()` 加 `datasets` 参数：

```python
async def search_all(
    user_id: str,
    query: str,
    limit: int = 10,
    *,
    include_cognee: bool = True,
    datasets: list[str] | None = None,
) -> list[MemoryItem]:
    """多源搜索记忆。datasets=None 时 Cognee 搜全部 dataset。"""
    seen: set[str] = set()
    results: list[MemoryItem] = []

    if include_cognee:
        results.extend(await _search_cognee(query, limit, seen, datasets=datasets))
    results.extend(await _search_fts5(user_id, query, limit, seen))
    results.extend(await _search_md(user_id, query, seen))

    return results[:limit]


async def _search_cognee(
    query: str,
    limit: int,
    seen: set[str],
    *,
    datasets: list[str] | None = None,
) -> list[MemoryItem]:
    """Cognee 语义搜索。datasets=None 时搜全部 dataset。"""
    results: list[MemoryItem] = []
    try:
        from app.backend.memory.cognee_admin.datasets import ALL_DATASETS
        from app.backend.memory.stores.semantic import SemanticStore

        store = SemanticStore()
        search_datasets = datasets if datasets is not None else ALL_DATASETS
        texts = await store.search(query, datasets=search_datasets, top_k=limit)
        for text_content in texts:
            content = text_content.strip()
            if not content or content in seen:
                continue
            seen.add(content)
            results.append(
                MemoryItem(
                    id=f"cognee:{hash(content)}",
                    content=content[:500],
                )
            )
    except Exception as exc:
        logger.warning("Cognee search skipped", error=str(exc))
    return results
```

注意：`_search_fts5` 和 `_search_md` 签名不变，这两个函数不受 datasets 参数影响。

---

### 3. `app/backend/memory/facade.py`

`recall()` 加 `datasets` 参数，转发给 `search_all()`：

```python
async def recall(
    self,
    user_id: str,
    query: str,
    limit: int = 10,
    datasets: list[str] | None = None,
) -> list[MemoryItem]:
    """搜索记忆：Cognee 语义 → FTS5 全文 → .md 兜底。"""
    return await search_all(user_id, query, limit=limit, datasets=datasets)
```

其余方法不变。

---

### 4. `app/backend/agent/pydantic_tools.py`

`memory_search` 工具：

- **删除** `files` 参数（旧的 .md 子串搜索路径，已被三层搜索兜底覆盖）
- **新增** `scope` 参数，映射到 datasets

```python
@agent.tool
async def memory_search(
    ctx: RunContext[LumenDeps],
    query: str,
    scope: str | None = None,
) -> str:
    """搜索记忆。

    scope 选择规则（有明确范围时填，否则不传）：
    - "profile"   — 技能/经历/画像/目标/学校等个人档案
    - "emotions"  — 情绪/焦虑/心情/日记/内心想法
    - "reference" — 公司信息/行业报告/学长经验/外部资料
    - "chat"      — 历史对话摘要
    - 不传（None）— 跨领域或不确定时，搜全部
    """
    logger.info("Tool call: memory_search", query=query, scope=scope)

    if not query or not query.strip():
        return "请提供搜索关键词。"

    from app.backend.memory import get_memory
    from app.backend.memory.cognee_admin.datasets import SCOPE_DATASETS

    # scope → datasets 映射，不传或值非法则搜全部
    datasets = SCOPE_DATASETS.get(scope) if scope else None

    memory = get_memory()
    items = await memory.recall(ctx.deps.user_id, query, datasets=datasets)
    if items:
        return "\n".join(
            f"- [{item.categories[0] if item.categories else '?'}] {item.content[:300]}"
            for item in items
        )
    return "未找到相关内容。"
```

---

### 5. `app/backend/agent/pydantic_agent.py`

在静态 `system_prompt` 字符串末尾追加 scope 选择规则（追加到现有字符串内，不改变其他内容）：

```python
system_prompt=(
    "你是「Lumen」，用户的 AI 伴侣。性格：深谋远虑但平易近人，说话像一个真正认识你的朋友，"
    "不是客服，不奉承，有时候会说实话，包括用户不想听的。\n\n"
    "规则：用户提到目标/技能/经历/学校/偏好/决定时必须调用工具保存。\n"
    "目标→memory_save('goals',方向,动机) | 技能→memory_save('skills',名称,程度)\n"
    "经历→memory_save('experiences',标题,描述) | 学校→update_profile() | 偏好→memory_save('preferences',名,内容)\n"
    "先保存再回答，一句话告知，不要只回「已记录」。\n\n"
    "memory_search scope 选择：问技能/经历/画像→profile；问情绪/焦虑/内心→emotions；"
    "问公司/行业/学长→reference；问历史对话→chat；跨领域或不确定→不传 scope。\n\n"
    "开场白：简短自然，不罗列功能，不问「有什么可以帮您」。"
    "示例：「我是 Lumen。你在哪个阶段，就从哪里说起。」"
),
```

---

## 验收标准

1. `memory_search` 工具无 `files` 参数，有 `scope` 参数（可选，默认 None）
2. `scope="emotions"` 时，Cognee 只搜 `lumen_reflection`
3. `scope=None` 时，Cognee 搜全部 4 个 dataset
4. `scope` 值不在 SCOPE_DATASETS 中（如传了非法值），等同于 None（全搜），不报错
5. `recall()` 签名变为 `recall(user_id, query, limit=10, datasets=None)`，原有调用方（`build_context` 内的 `recall(uid, query, limit=5)`）不需要改动，默认行为不变

## 不改动的内容

- `_search_fts5()` 签名和逻辑不变
- `_search_md()` 签名和逻辑不变
- `remember()`、`flush_projections()` 等写入路径不变
- 所有其他工具（`memory_save`、`update_profile`、`get_profile`）不变
