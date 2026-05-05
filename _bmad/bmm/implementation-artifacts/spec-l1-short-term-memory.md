---
title: 'L1 短期记忆实现（方案 A）'
type: 'feature'
created: '2026-05-05T12:06:00'
status: 'done'
route: 'one-shot'
---

## Intent

**Problem:** AI 每一轮对话都是全新启动，没有任何历史上下文。用户说"我是大三的"，AI 回复"好的"，用户接着问"那我该学什么"，AI 已经不知道用户是大三的了。

**Approach:** 在 `dynamic_prompt` 里从 DB 加载最近 20 条消息，格式化为文本段落注入 system prompt。

## Boundaries & Constraints

**Always:**
- 历史消息加载失败不影响对话
- 历史消息截断过长的回复（保留前 200 字符）
- 历史消息按时间正序排列（最早的在前）

**Ask First:**
- 无

**Never:**
- 不使用 PydanticAI 的 `message_history` 参数（会跳过 system_prompt）
- 不修改 Message 模型结构

## Code Map

- `app/backend/agent/pydantic_agent.py` -- 动态系统提示词生成，加载用户画像、记忆和历史消息
- `app/backend/agent/deps.py` -- CareerOSDeps 依赖注入类型，添加 conversation_id 属性
- `app/backend/services/chat_service.py` -- 对话入口，传递 conversation_id 给 Agent

## Tasks & Acceptance

**Execution:**
- [x] `app/backend/agent/deps.py` -- 添加 conversation_id 属性 -- 用于加载历史消息
- [x] `app/backend/agent/pydantic_agent.py` -- 修改 dynamic_prompt 函数 -- 加载最近 20 条历史消息并注入 system prompt
- [x] `app/backend/services/chat_service.py` -- 传递 conversation_id -- 让 Agent 能访问当前会话 ID

**Acceptance Criteria:**
- Given 用户发送消息，when Agent 处理请求，then system prompt 包含最近 20 条历史消息
- Given 历史消息加载失败，when Agent 处理请求，then 对话正常进行（不影响用户体验）
- Given 历史消息包含过长回复，when 格式化历史消息，then 截断为前 200 字符

## Verification

**Commands:**
- `python -m pytest tests/ -v` -- expected: 所有测试通过
- `python -m ruff check app/backend/agent/pydantic_agent.py app/backend/agent/deps.py app/backend/services/chat_service.py` -- expected: 无 lint 错误

**Manual checks:**
- 启动后端服务，发送多条消息，验证 AI 能记住之前的对话内容
