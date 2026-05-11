# Story: 文件内容智能提取（Mode 2）

## 背景

文件上传功能已实现（parse → chunk → Cognee 索引 + document_uploaded 事件）。但 Agent 不会在上传时主动学习文件内容——用户得问了才检索。

**目标**：上传文件后，Agent 自动审查内容，有价值的信息写入记忆层（memory.md / skills.md / experiences.md）。不区分文件类型，Agent 自己判断。

---

## 设计原则

1. **轻量提取 Agent** — 不复用对话 Agent，单独创建一个只做文档分析的 Agent
2. **Agent 自主判断** — 不写规则，不区分文件类型，Agent 读内容后自己决定要不要调工具
3. **优雅降级** — 提取失败不影响已有的 chunk + Cognee 索引
4. **不新增工具** — 只注册 `memory_save` 和 `update_profile`，不注册 `memory_search` / `get_profile`
5. **同步执行** — 在 `_process_file` 里做，处理完就 ready，不额外延迟

---

## 为什么不能复用对话 Agent

当前 `create_agent()` 创建的 Agent 有三个问题：

| 问题 | 原因 | 影响 |
|------|------|------|
| 对话人设干扰 | system prompt 带"你是 Lumen，用户的 AI 伴侣..." | Agent 可能生成寒暄文字，浪费 token |
| 强制生成文字回复 | "调用任何工具后必须生成文字回复" | 文件提取时我们只关心工具调用，不关心回复 |
| dynamic_prompt 加载无关上下文 | 调 `build_context()` + 查 `Conversation` 表 | 加载 memory.md + 对话摘要，文件分析不需要 |

**解决方案**：创建轻量 `_extract_agent`，不注册 `memory_search` / `get_profile`，不加 `dynamic_prompt`。

---

## 数据流

```
_process_file()：
  1. parse_file → text                    （已有）
  2. chunk_text → chunks                  （已有）
  3. GrowthEvent(document_uploaded)       （已有）
  4. Cognee 索引                          （已有）
  5. 轻量 Agent 审查 text → 调工具写事件   （新增）
  6. status → ready                       （已有）
```

步骤 5 在步骤 4 之后执行，失败不影响步骤 6（ready 状态）。

---

## 变更范围（1 个文件）

### `backend/services/knowledge.py`

#### 1. 新增轻量提取 Agent

```python
# ── 轻量提取 Agent（不加载对话上下文，只做文档分析）──


def _get_extract_agent() -> Agent[LumenDeps, str]:
    """创建轻量提取 Agent（不缓存，避免配置漂移）。

    文件上传不是高频操作，每次创建开销很小（注册两个 tool）。
    """
    from pydantic_ai import Agent
    from backend.agent.pydantic_agent import _create_model

    agent = Agent(
        model=_create_model(),
        deps_type=LumenDeps,
        output_type=str,
        system_prompt=(
            "你是一个文档信息提取助手。阅读用户上传的文档内容，判断其中是否包含"
            "对用户个人画像有价值的信息。\n\n"
            "你有这些工具：\n"
            "- update_profile：更新用户基本信息（学校、专业、目标等）\n"
            "- memory_save：保存技能、经历、偏好、目标、决策等\n\n"
            "规则：\n"
            "1. 只提取和用户本人相关的信息（学过什么、做过什么、想要什么）\n"
            "2. 参考资料、技术文档、第三方内容不提取\n"
            "3. 如果没有值得提取的信息，回复「无需提取」，不要编造\n"
            "4. 提取时保持原文意思，不要过度总结\n"
            "5. 调用工具后不需要生成额外文字，直接结束"
        ),
        retries=2,
    )

    # 只注册 memory_save 和 update_profile，不注册 memory_search / get_profile
    from backend.agent.tools.tool_memory_save import register as register_memory_save
    from backend.agent.tools.tool_profile import register as register_profile

    register_memory_save(agent)
    register_profile(agent)

    return agent
```

#### 2. 新增智能截断函数

```python
def _smart_truncate(text: str, max_chars: int = 5000) -> str:
    """按段落边界截断文本，避免断在句子/单词中间。"""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # 从末尾往前找最近的段落分隔
    last_para = max(truncated.rfind("\n\n"), truncated.rfind("\n"))
    if last_para > max_chars * 0.8:
        return truncated[:last_para]
    return truncated
```

#### 3. 新增提取函数

```python
_EXTRACT_PROMPT = (
    "阅读以下用户上传的文件内容，提取对用户画像有价值的信息。\n\n"
    "【文件：{filename}】\n{text}"
)


async def _extract_and_save(user_id: str, text: str, filename: str) -> None:
    """调轻量 Agent 审查文件内容，有价值的信息写入记忆层。"""
    # API key 检查：避免无意义的 Agent 调用
    from backend.config import get_settings
    settings = get_settings()
    if not (settings.llm_api_key or settings.dashscope_api_key):
        logger.debug("Skipping extraction: no API key configured")
        return

    from backend.agent.deps import LumenDeps
    from backend.memory import get_memory

    agent = _get_extract_agent()
    prompt = _EXTRACT_PROMPT.format(filename=filename, text=_smart_truncate(text))

    async with get_async_session_maker()() as db:
        deps = LumenDeps(
            user_id=user_id,
            db=db,
            conversation_id=None,  # 文件提取不需要 conversation
            current_user_input=prompt,
        )
        await agent.run(prompt, deps=deps)

        # 先 commit 再 sync_projections（facade.py 要求：调用方 commit 后调用）
        await db.commit()

        if deps.pending_event_ids:
            await get_memory().sync_projections(user_id, deps.pending_event_ids)
            logger.info(
                "Agent extracted %d events from file",
                len(deps.pending_event_ids),
                filename=filename,
            )
```

#### 4. 在 `_process_file` 里调用

在 Cognee 索引之后、status→ready 之前：

```python
# ── Agent 内容提取（新增）──
try:
    await _extract_and_save(user_id=user_id, text=text, filename=filename)
except Exception as exc:
    logger.warning("Agent extraction failed, skipping", filename=filename, error=str(exc))
    # 不影响后续 ready 状态
```

---

## 不需要改的

- `pydantic_agent.py` — 不改，对话 Agent 保持原样
- `deps.py` — 不改，`conversation_id: str | None = None` 已支持
- `tools.py` / `tool_*.py` — 不改，工具注册逻辑复用
- `parsers.py` / `chunker.py` / `models.py` / `schemas.py` — 不改
- 前端 — 不改

---

## 降级策略

| 失败场景 | 处理方式 |
|---------|---------|
| LLM API 超时/报错 | catch → log warning → 继续 ready |
| Agent 不调工具（回复"无需提取"） | 正常，文件没有有价值信息 |
| Agent 提取了错误信息 | 用户可在记忆页手动删除 |
| LLM 未配置（无 API key） | 跳过提取，只做 chunk + 索引 |
| `_create_model()` 报错 | catch → log warning → 继续 ready |

---

## 已知限制

| 问题 | 影响 | 处理方式 |
|------|------|---------|
| `memory_save` docstring 写的是对话场景（"用户说..."） | 提取 Agent 可能 confusion | 初版不处理，prompt 上下文权重最高，出问题再用 tool_docstring 覆盖 |

---

## 实施顺序

| 步骤 | 内容 | 依赖 |
|------|------|------|
| 1 | 新增 `_get_extract_agent()` 轻量 Agent（不缓存） | 无 |
| 2 | 新增 `_smart_truncate()` 智能截断 | 无 |
| 3 | 新增 `_extract_and_save()` 提取函数（commit → sync_projections） | 1, 2 |
| 4 | 在 `_process_file` 里调用 | 3 |
| 5 | 测试：上传简历 → 检查 skills.md / experiences.md 是否更新 | 4 |

---

## 验收标准

1. 上传简历 → memory.md / skills.md / experiences.md 自动更新
2. 上传项目文档 → experiences.md 更新（如有项目经历）
3. 上传论文/参考资料 → 不提取，只索引
4. 提取失败 → 文件仍标记为 ready，不影响使用
5. 无 API key → 跳过提取，不报错，不刷日志
6. 对话 Agent 不受影响（不注册额外工具，不加载文件上下文）
