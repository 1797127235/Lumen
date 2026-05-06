# 后台记忆审查 — 实现计划

> 参考：Hermes Agent (`_spawn_background_review`) — nousresearch/hermes-agent

## 背景

CareerOS 当前记忆写入仅依赖一条路径：Agent 在对话中主动调用 `memory_save` / `update_profile` 工具。
如果模型没有主动调用（指令被淹没、上下文太长、偷懒），重要信息就丢失了。

Hermes Agent 提供了可靠的兜底方案：**后台 fork Agent 审查**。

## 两层记忆架构（目标状态）

```
你说话 → Agent Loop → 模型主动调 memory_save ──→ 写入 growth_events ✅（已有）
  │                                                        ↓
  │                                               sync_projections → 更新 .md
  │
  └→ Agent 没调？→ 后台 fork Agent → review prompt + 本轮对话
                                        ↓
                              模型决定是否值得保存
                                        ↓
                              写入 growth_events (source="后台审查")
                                        ↓
                              sync_projections → 更新 .md
```

## 改动范围

### 1. `app/backend/agent/pydantic_agent.py` — 无需改动

**决策**：不创建独立的 review agent 工厂。`_background_memory_review` 直接复用 `get_agent()`，
靠 review prompt 约束行为（只允许 memory_save / update_profile，其他工具不调用）。
避免代码重复，保持简单。

### 2. `app/backend/agent/pydantic_tools.py` — 无需改动

同上，不创建 `register_memory_tools_only()`。

### 3. `app/backend/services/chat_service.py` — 改动最小（~40 行）

在 `stream_chat` 的 `finally` 块中，Agent 回复保存后：

```python
# 现有代码 ~L148-154
if deps.pending_event_ids:
    try:
        from app.backend.services.careeros_memory import get_memory
        await get_memory().sync_projections(user_id, deps.pending_event_ids)
    except Exception as e:
        logger.warning("记忆投影失败", error=str(e))

# ── 新增：后台记忆审查 ──
if not deps.pending_event_ids and full_content:
    # Agent 本轮没调任何记忆工具 → 后台审查兜底
    task = asyncio.create_task(_background_memory_review(
        user_id=user_id,
        user_message=user_input,
        assistant_response=full_content,
        conversation_id=conv.conversation_id,
    ))
    task.add_done_callback(_log_task_error)
```

### 4. `app/backend/services/chat_service.py` — 新增 `_background_memory_review`

```python
_REVIEW_PROMPT = """审查上一轮对话，判断是否包含值得保存的用户信息。

重点关注：
1. 用户是否透露了关于自己的新信息（目标、技能、经历、偏好、状态）？
2. 用户是否纠正了你、表达了偏好、或做出了决策？

如果有值得保存的信息，调用 memory_save 或 update_profile 保存。
如果没有任何新信息，回复「无需保存」。

【对话】
用户：{user_message}

助手：{assistant_response}"""


async def _background_memory_review(
    user_id: str,
    user_message: str,
    assistant_response: str,
    conversation_id: str,
) -> None:
    """后台审查本轮对话，判断是否有值得保存的记忆。

    仅在 Agent 本轮未主动调用 memory_save/update_profile 时触发。
    使用独立 db session，不阻塞用户看到回复。
    """
    try:
        from app.backend.db.base import get_async_session_maker

        async with get_async_session_maker()() as db:
            from app.backend.agent.deps import CareerOSDeps
            from app.backend.agent.pydantic_agent import get_agent

            agent = get_agent()
            deps = CareerOSDeps(
                user_id=user_id,
                db=db,
                conversation_id=conversation_id,
                current_user_input=user_message,
            )

            prompt = _REVIEW_PROMPT.format(
                user_message=user_message,
                assistant_response=assistant_response,
            )

            result = await agent.run(prompt, deps=deps)

            # 如果审查 Agent 调了工具 → 触发投影
            if deps.pending_event_ids:
                from app.backend.services.careeros_memory import get_memory

                await get_memory().sync_projections(user_id, deps.pending_event_ids)
                logger.info(
                    "后台审查已保存 %d 条记忆",
                    len(deps.pending_event_ids),
                    conversation_id=conversation_id,
                )
            await db.commit()
    except Exception:
        # 后台审查失败不影响用户
        logger.exception("后台记忆审查失败", conversation_id=conversation_id)
```

### 5. 模型选择（成本控制）

| 策略 | 优点 | 缺点 |
|------|------|------|
| **同模型** (Hermes 做法) | 简单，复用缓存 | 每条对话多一次调用 |
| **廉价模型** (qwen-turbo) | 成本低 | 需额外配置 |
| **1/4 概率抽样** | 大幅降成本 | 可能漏保存 |

**建议**：先用同模型（最简单），后续根据成本数据决定是否切换。

## 实现顺序（预计 1 小时）

1. [ ] `chat_service.py` — 实现 `_background_memory_review()`（~30 行）
2. [ ] `chat_service.py` — 在 `finally` 块末尾加触发逻辑（~6 行）
3. [ ] 手动测试：对话 → 不提任何个人信息 → 检查审查 Agent 回复"无需保存"
4. [ ] 手动测试：对话 → 提到新目标/技能 → 检查审查 Agent 自动保存
5. [ ] `lsp_diagnostics` chat_service.py → `ruff check` → `pytest`

## 验证标准

- [ ] Agent 本轮调了 memory_save → 后台审查不触发（`pending_event_ids` 非空）
- [ ] Agent 本轮没调 memory_save → 后台审查触发（`pending_event_ids` 为空）
- [ ] 对话无有效信息 → 审查 Agent 回复「无需保存」→ growth_events 无新增
- [ ] 对话有有效信息 → 审查 Agent 调 memory_save → growth_events 新增记录（source="后台审查"）
- [ ] 后台审查失败 → 用户对话不受影响
- [ ] `lsp_diagnostics` 通过 → `ruff check` 通过 → `pytest` 通过

## 风险和缓解

| 风险 | 缓解 |
|------|------|
| 额外 LLM 调用增加成本 | 先上线，用一周数据评估；后续可切廉价模型 |
| 审查 Agent 误保存闲聊 | review prompt 明确要求「如果没有新信息就回复无需保存」 |
| 审查 Agent 与主 Agent 竞态 | 独立 db session，独立 commit |
| async task 被事件循环过早取消 | `asyncio.create_task` 创建独立 task，不随请求结束取消 |
