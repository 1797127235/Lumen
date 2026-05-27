# CLI /resume — 会话恢复功能

## 1. 概述

CLI Channel 新增 `/resume` 命令，支持查看历史会话列表并通过键盘导航选择恢复，无需记住会话 ID。

## 2. 问题

- CLI 每次启动创建新 `cli-xxxxxxxx` 作为 `chat_id`，无法续接历史对话
- `agent_runner.py:104` 每次调用 `ensure_conversation(db, user_id, None, user_input)` — 永远传 `None`，`msg.chat_id` 不参与持久化
- Web 端同样受影响：前端传的 `conversation_id` 被后端忽略

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `lib/chat/agent_runner.py:104` | `None` → `msg.chat_id`（bug fix，让会话 ID 参与持久化） |
| `lib/channels/cli.py` | 新增 `/resume` 命令 + 交互式会话选择器 + 会话加载逻辑 |
| `lib/channels/cli.py` | 更新 `SLASH_COMMANDS` 和 `_SlashCompleter` |

## 4. 用户交互流程

### 4.1 正常使用

```
> /resume
┌─────────────────────────────────────────┐
│  📋 最近会话                             │
│  ─────────────────────────────────────  │
│ ▸ 今天天气怎么样          8条  2分钟前   │
│   Python异步编程          12条 1小时前   │
│   周末计划                5条  昨天      │
│   React组件设计           20条 3天前     │
│  ─────────────────────────────────────  │
│  输入过滤 · ↑↓ 导航 · Enter 选中 · Esc  │
└─────────────────────────────────────────┘

（用户按 ↓ 两下，高亮移到「周末计划」，按 Enter）

  ✓ 已恢复会话「周末计划」

  你: 周末我们去哪玩？
  Lumen: 你上次说想去爬山...
```

### 4.2 快捷方式

```
> /resume 3        ← 直接加载第 3 个，跳过选择器
  ✓ 已恢复会话「周末计划」
```

### 4.3 边界情况

```
> /resume
  暂无历史会话。
```

## 5. 交互细节

| 按键 | 行为 |
|---|---|
| ↑ / ↓ | 移动高亮行 |
| 输入字符 | 即时过滤列表（标题模糊匹配） |
| Enter | 选中当前高亮项，加载会话 |
| Esc / Ctrl+C | 取消，返回正常输入 |

全程无数字输入。体验对齐 OpenCode 的 `DialogSessionList`。

## 6. 实现方案

### 6.1 数据查询

直接用 SQLAlchemy 查询 `Conversation` + `Message` 表，复用已有 ORM 模型，不走 HTTP API：

```python
# 列表查询
select(Conversation).where(
    Conversation.user_id == _USER_ID
).order_by(
    Conversation.last_message_at.desc()
).limit(10)

# 消息查询（恢复时打印最近 N 条）
select(Message).where(
    Message.conversation_id == conv_id,
    Message.role.in_(["user", "assistant"])
).order_by(Message.created_at.asc())
```

### 6.2 prompt_toolkit 实现

在现有 Application 内用 `ConditionalContainer` 渲染会话列表区域，不退出当前 Application：

- `_resume_mode` 状态变量控制显示/隐藏
- 列表用 `FormattedTextControl` 渲染，高亮行用不同 style
- `KeyBindings` 绑定 ↑↓/Enter/Esc/字符输入（仅 resume 模式生效）
- 选中后调用 `_load_conversation(conv_id)` 完成切换
- 列表替代 thinking 区域的位置（输入框上方）

### 6.3 会话加载

选中会话后：

1. `self._chat_id` = 选中的 `conversation_id`
2. 查询该会话最近 N 条 Message
3. 打印到 TUI 滚动区（user/assistant 交替显示）
4. 回到正常输入状态

### 6.4 bug fix: agent_runner.py

```python
# 现在（line 104）
conv = await ensure_conversation(db, user_id, None, user_input)

# 改为
conv = await ensure_conversation(db, user_id, msg.chat_id, user_input)
```

一行改动。`ensure_conversation` 已有逻辑：如果 `conversation_id` 存在则查找已有会话，不存在则新建。传入 `msg.chat_id` 后，CLI 恢复的会话和 Web 端传入的 `conversation_id` 都能正确续接。

## 7. 验证方式

1. 启动 CLI，发几条消息
2. 退出，重新启动
3. 输入 `/resume`，确认能看到之前的会话
4. 选择一个会话，确认能打印历史消息
5. 继续发消息，确认 Agent 能带着历史上下文回复
6. 测试 `/resume 1` 快捷加载
7. 测试 Esc 取消
8. 测试空列表提示
9. Web 端发消息后，CLI `/resume` 应能看到 Web 端的会话（共享 user_id）
