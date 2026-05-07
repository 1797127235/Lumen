# Story: Cognee LLM/Embedding 配置修复

## 根因

Cognee 一直静默失败，所有记忆搜索降级到 SQLite。原因是三个 bug：

### Bug 1（致命）：init_cognee() 没有配置 LLM 和 Embedding

`cognee.remember()` 内部自动调用 `cognify()`，`cognify()` 需要：
- **LLM**：从文本中提取实体和关系，构建 Kuzu 知识图谱
- **Embedding 模型**：向量化文本，写入 LanceDB

`init_cognee()` 只配了 `data_root_directory`，LLM 和 Embedding 完全未配。
结果：每次 `remember()` 内部调用 `cognify()` 时报错，被 `SemanticStore.ingest()` 的 `try/except` 吞掉，
日志打 `Cognee ingest failed`，Cognee 永远没有数据，`recall()` 永远返回空，降级 SQLite。

### Bug 2（冗余）：cognify_loop 是多余的

`cognee.remember()` 文档明确：*"Without session_id: Runs add() + cognify()"*。
我们在 `ingest()` 里调 `remember()` 已经触发了 cognify，
60 秒后 `cognify_loop` 再对 ALL_DATASETS 调一遍 `cognify()`，双重执行且浪费。

**修复**：改用 `cognee.add()` 做纯数据写入（不触发 cognify），
保留 `cognify_loop` 做批量定时 cognify（这是原本设计的正确用法）。

### Bug 3（次要）：_extract_text() 漏了 context 属性

Cognee 1.0.5 返回两种类型，实际字段：
- `ResponseQAEntry`：有 `answer`（已取）、`context`（未取，往往更丰富）
- `ResponseGraphEntry`：有 `text`（已取）

---

## 变更范围（3 个文件）

### 1. `app/backend/memory/cognee_admin/cognify_loop.py`

`init_cognee()` 加 LLM + Embedding 配置：

```python
def init_cognee() -> str:
    """初始化 Cognee：路径 + LLM + Embedding 配置。"""
    global _cognee_status

    os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"

    try:
        import cognee as _cognee

        from app.backend.config import get_settings
        settings = get_settings()

        # 数据目录
        _cognee.config.data_root_directory(str(USER_DATA_DIR))

        # LLM 配置（用 openai provider，兼容所有 OpenAI 兼容接口）
        llm_api_key = settings.llm_api_key or settings.dashscope_api_key
        llm_base_url = settings.llm_base_url
        if not llm_base_url:
            if settings.llm_provider == "dashscope":
                llm_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            elif settings.llm_provider == "deepseek":
                llm_base_url = "https://api.deepseek.com"

        if llm_api_key and llm_base_url:
            _cognee.config.set_llm_provider("openai")
            _cognee.config.set_llm_model(settings.llm_model or "qwen-plus")
            _cognee.config.set_llm_api_key(llm_api_key)
            _cognee.config.set_llm_endpoint(llm_base_url)

        # Embedding 配置
        embedding_api_key = settings.embedding_api_key or llm_api_key
        embedding_base_url = settings.embedding_base_url
        if not embedding_base_url:
            if settings.embedding_provider == "dashscope":
                embedding_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

        if embedding_api_key and embedding_base_url:
            _cognee.config.set_embedding_provider("openai")
            _cognee.config.set_embedding_model(settings.embedding_model or "text-embedding-v4")
            _cognee.config.set_embedding_api_key(embedding_api_key)
            _cognee.config.set_embedding_endpoint(embedding_base_url)

        _cognee_status = "ready"
        logger.info(
            "Cognee initialized",
            data_root=str(USER_DATA_DIR),
            llm_model=settings.llm_model,
            embedding_model=settings.embedding_model,
            has_llm_key=bool(llm_api_key),
            has_embed_key=bool(embedding_api_key),
        )
        return _cognee_status

    except ImportError:
        logger.warning("Cognee not installed")
        _cognee_status = "not_installed"
        return _cognee_status
    except Exception as exc:
        logger.error("Cognee init failed", error=str(exc))
        _cognee_status = "error"
        return _cognee_status
```

`cognify_loop()` 不变（保留批量定时 cognify）。

---

### 2. `app/backend/memory/stores/semantic.py`

`ingest()` 改用 `cognee.add()` 替代 `cognee.remember()`，避免重复触发 cognify：

```python
async def ingest(self, content: str, doc_id: str, dataset: str = DATASET_PROFILE) -> bool:
    """存储文本到 Cognee，标记待 cognify（定时批量处理）。"""
    try:
        import cognee as _cognee

        # 用 add() 纯写入，不触发 cognify（cognify_loop 统一批量处理）
        await _cognee.add(content, dataset_name=dataset)

        from app.backend.memory.cognee_admin.cognify_loop import mark_needs_cognify
        mark_needs_cognify()

        logger.debug("Cognee add ok", doc_id=doc_id, dataset=dataset, content_length=len(content))
        return True
    except Exception as exc:
        logger.error("Cognee ingest failed", doc_id=doc_id, dataset=dataset, error=str(exc))
        return False
```

`_extract_text()` 加 `context` 属性（ResponseQAEntry 的完整上下文）：

```python
@staticmethod
def _extract_text(result: Any) -> str | None:
    """从 Cognee 返回的各种对象类型中提取文本。"""
    for attr in ("text", "answer", "context", "content"):
        val = getattr(result, attr, None)
        if val:
            return str(val)
    return None
```

---

### 3. `app/backend/memory/cognee_admin/__init__.py`

确认导出正确（init_cognee 和 cognify_loop 都导出）：

```python
from app.backend.memory.cognee_admin.cognify_loop import cognify_loop, init_cognee

__all__ = ["cognify_loop", "init_cognee"]
```

---

## 不变的内容

- `main.py` 不变（`init_cognee` 在 thread 里跑、`cognify_loop` 作为 async task 跑，逻辑正确）
- `cognify_loop()` 函数体不变
- `facade.py` 不变
- `search.py` 不变
- 所有工具不变

---

## 验收标准

1. 启动服务后，日志中可以看到 `Cognee initialized, llm_model=..., has_llm_key=True, has_embed_key=True`
2. Agent 保存一条记忆后，60 秒内日志出现 `Cognee cognify completed`（或 `Cognee add ok`）
3. `memory_search` 调用后，日志中 Cognee search 不再报错，能返回结果（不再只有 SQLite 结果）
4. 如果 LLM/Embedding API key 未配，`init_cognee()` 仍设置 `_cognee_status = "ready"`，但跳过 LLM/Embedding 配置（Cognee 会用默认配置，可能仍失败，但不影响主流程）

## 注意事项

- `cognee.add()` 是 V1 API，Cognee 1.0.5 仍支持（文档注明 "V1 add/cognify/search still work"）
- DashScope embedding 端点和 LLM 端点相同，都是 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- Cognee `set_embedding_provider("openai")` 表示使用 OpenAI 兼容协议，不是必须用 OpenAI 的服务
- 如果用户配了 DeepSeek 作为 LLM 但没有 embedding API，embedding 配置会被跳过（无 key），Cognee 的语义搜索会失败降级，但主流程不受影响
