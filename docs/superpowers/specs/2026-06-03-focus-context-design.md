# FOCUS.md 当前关注功能设计

**日期**: 2026-06-03  
**状态**: 已批准  
**范围**: 当前关注上下文的存储、注入和自动更新

---

## 目标

让 Lumen 知道用户当前在关注什么，为后续 RSS 信息过滤提供上下文基础。

## 设计决策

### 1. 存储：独立 FOCUS.md 文件

**位置**: `~/.lumen/memory/{user_id}/FOCUS.md`

**理由**:
- 关注点变化快（天级），长期记忆变化慢（周级），生命周期不同
- MEMORY.md 有 2200 字符限制，关注点不应与长期记忆竞争空间
- RSS 过滤时可直接读取 FOCUS.md，无需解析 MEMORY.md

**格式**:
```markdown
## 当前关注

- Agent 记忆系统设计
- PydanticAI 用法
- RSSHub 集成
```

**字符限制**: 500 字符（约 5-8 个关注点）

### 2. 注入：独立 user message

FOCUS.md 作为独立 user message 注入，在 memory-context 之后、用户消息之前：

```
[system]  静态前缀（人格 + 工具 + 风格 + 记忆指令）
[user]   <memory-context>MEMORY.md + USER.md</memory-context>
[user]   <current-focus>FOCUS.md 内容</current-focus>
[user]   用户消息
```

**理由**:
- MEMORY.md 是冻结快照（L0），按对话缓存
- FOCUS.md 变化更频繁，独立注入不影响 memory-context 缓存
- `<current-focus>` 标签让 LLM 清晰识别当前优先事项

### 3. 更新方式

**手动**: 用户说"我在关注 X"，Lumen 调用 `focus_update` 工具写入。

**自动**: 每轮对话后，`review_service.py` 的审查 Agent 从对话中提取关注点，调用 `focus_update` 更新。

**衰减**: v1 不实现。后续版本可添加"两周没出现的话题自动移除"逻辑。

---

## 改动清单

### lib/memory/markdown.py

新增方法：
- `read_focus(user_id: str) -> str` — 读取 FOCUS.md
- `write_focus(user_id: str, content: str) -> None` — 写入 FOCUS.md（原子写入 + 安全扫描）

### lib/tools/memory.py

新增工具：
- `focus_update` — 更新 FOCUS.md 的当前关注列表
  - 输入: `topics: list[str]`（关注点列表）
  - 行为: 覆写 FOCUS.md 的 `## 当前关注` 章节

### lib/memory/review_service.py

修改审查 prompt，增加第四条指令：
```
4. 用户是否提到了正在关注的话题、项目、学习方向？
   如果有，调用 focus_update 更新 FOCUS.md。
```

### lib/chat/agent_runner.py

修改 `_inject_context_frame()`：
- 读取 FOCUS.md 内容
- 如果非空，在 memory-context 消息之后插入独立消息：
  ```python
  ModelRequest(parts=[UserPromptPart(
      content=f"<current-focus>\n{focus_content}\n</current-focus>"
  )])
  ```

---

## 数据流

```
用户说"我在关注 Rust"
  → Lumen 调用 focus_update 工具
  → write_focus() 写入 FOCUS.md

每轮对话结束
  → review_service fork Agent
  → 审查对话，提取关注点
  → 调用 focus_update 更新 FOCUS.md

下次对话开始
  → _inject_context_frame() 读取 FOCUS.md
  → 注入为 <current-focus> 独立消息
  → LLM 看到当前关注，回复更贴合用户兴趣

后续 RSS 集成
  → RSS 拉取新条目
  → 直接读 FOCUS.md 传给过滤 LLM
  → 判断相关性，推送相关内容
```

---

## 后续扩展

1. **自动衰减**: 两周没出现的关注点自动移除
2. **RSS 集成**: 定时拉取 RSSHub，用 FOCUS.md 过滤推送
3. **手动编辑**: 前端 UI 支持直接编辑 FOCUS.md
