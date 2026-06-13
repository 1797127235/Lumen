# Call:1 Token 缓存率优化方案

## 目标

将 Agent `call:1`（每轮对话的第一次 LLM 调用）的 prompt cache hit rate 从当前的 **65~85%** 稳定在 **90% 以上**。

---

## 核心思想：按 token 序列稳定性分层

> 缓存率的本质不是"哪些内容放进 system prompt"，而是**输入 token 序列中变化点出现在哪里**。

DeepSeek 和 Anthropic 的 prompt cache 底层都是 **KV cache 前缀匹配**：只要两个请求的 token 序列前缀 byte-identical，前面的 KV cache 就能复用。Anthropic 提供 `cache_control` 让你显式指定断点，DeepSeek 没有显式 API，但底层机制相同。

因此优化原则是：**按内容稳定性对 token 序列排序，把每轮必变的内容推到序列最末尾，让前面尽可能长的前缀保持稳定。**

参考 `E:\OpenHub\hermes-agent` 的设计（`agent/prompt_caching.py` + `run_agent.py:_build_system_prompt_parts()`），它把输入严格分成三层：

| 层级 | 稳定性 | 例子 | 缓存策略 |
|------|--------|------|----------|
| **stable** | 跨 session 不变 | identity、tool guidance、skills | 长期缓存（hermes 用 1h TTL） |
| **context** | session 内不变 | AGENTS.md、caller system_message | session 内缓存（hermes 用 5m TTL） |
| **volatile** | 每轮/每 session 变 | memory snapshot、user profile、timestamp | 不缓存或短缓存 |

Lumen 当前的问题不是"动态内容太多"，而是**动态内容和相对稳定的内容混在一起，导致相对稳定的内容也被拖入每轮都 miss 的区域**。

---

## 当前现状

### 实测数据

最近 30 条 `LLM usage` 中 `call:1` 的表现：

| 北京时间 | input | cache_read | cache_write | cache_hit_pct |
|----------|-------|------------|-------------|---------------|
| 20:14:50 | 9,220 | 1,920 | 7,300 | 20.8% |
| 20:26:41 | 14,179 | 9,216 | 4,963 | 65.0% |
| 20:26:59 | 16,917 | 14,080 | 2,837 | 83.2% |
| 20:27:12 | 19,671 | 16,896 | 2,775 | 85.9% |
| 20:30:55 | 24,438 | 19,584 | 4,854 | 80.1% |

`call:1` 平均命中率约 **34.4%**（按 scope 汇总），波动大。

### Prompt 组成实测

以 user_id=8595876131、输入"性格呢"为例：

| 部分 | 字符数 | 估算 tokens | 稳定性层级 |
|------|--------|-------------|------------|
| `system_prompt`（identity/tools/style/memory_rules） | 3,809 | ~1,506 | **stable** |
| `context_frame` | 2,040 | ~636 | **volatile**（当前全部作为 user message） |
| 其中：L0 用户画像 | ~1,500 | ~550 | **context**（相对稳定，被拖入 volatile user message） |
| 其中：PARTNER.md | ~200 | ~70 | **context/volatile**（相对稳定） |
| 其中：日期/L2 召回 | ~100 | ~16 | **volatile**（每轮都变） |
| 当前用户消息 | - | 几十~几百 | **volatile** |

### 当前消息序列

```text
[system]  stable system prompt（1,506 tokens）           <- ✅ 稳定

[user]    === context_frame ===                          <- ❌ 整段每轮都 miss
          当前日期：2026-06-13                            <- 每轮都变
          # 用户记忆（L0 + L1，~550 tokens）              <- 半稳定，被拖累
          <partner-rules>                                 <- 半稳定
          <memory-context>Current date: 2026-06-13</memory-context>  <- 每轮都变

[user]    性格呢                                          <- 每轮都变
```

### 根本原因

L0 用户画像和 PARTNER.md 属于**半稳定内容**（变化频率低），但它们被和日期、L2 召回等**每轮都变的内容**一起塞进 `context_frame`。结果是：
- 即使 L0 没变，它也要作为 context_frame 的一部分重新写缓存
- L0 前面是 history，后面是当前用户消息，它自己作为独立 block 变化时会打断前缀匹配
- system prompt 的 stable 前缀只有 1,506 tokens，不够厚

按当前结构，如果用户消息很短，理论缓存率上限只有 **~70%**。

---

## 优化方案

### 方案 1：system prompt 内部分层（P0，核心改造）✅ 已完成

#### 思路

不是简单地把 L0 塞进 system prompt，而是把 system prompt 分成三段：

```text
[system]
  ├── stable block    <- identity + tools + style + memory_rules（永远不变）
  ├── context block   <- L0 用户画像 + PARTNER.md（跨 session 相对稳定）
  └── volatile block  <- 可选：session 内稳定的内容
```

**为什么 L0 要放在 system prompt 末尾而不是 context_frame 里？**

因为 system prompt 前面是 stable block。如果 L0 不变，整个 system prompt 命中；如果 L0 变化，只有 L0 自己及后面的 user message 需要重新计算，前面 1,500+ tokens 的 stable 前缀仍然可以命中。

如果 L0 放在 context_frame（user message）里，它前面是每轮都可能变化的 history，后面是当前用户消息，L0 自己作为变化点无法被有效缓存。

#### 改造后效果估算

| 部分 | 当前 | 优化后 |
|------|------|--------|
| system prompt stable | 1,506 | 1,506 |
| system prompt context（L0 + PARTNER） | 0 | ~620 |
| context_frame | ~636 | ~100~150 |
| call:1 理论命中率 | ~70% | **~93%** |

#### 改造点

1. **`lib/agent/system_prompt_builder.py`** ✅
   - `build_system_prompt()` 增加 `user_id` 参数，改为 async。
   - 内部构建：
     ```python
     stable = build_stable_system_prompt()  # identity + tools + style + memory_rules + skill_requirements
     context_suffix = await _build_context_suffix(user_id)  # nickname + L0 + PARTNER.md
     fingerprint = config_fingerprint or _compute_fingerprint(context_suffix)
     # 按 (user_id, fingerprint) 缓存，30min TTL，LRU 100 用户
     ```
   - 新增 `invalidate_system_prompt_cache(user_id: str)` / `invalidate_all_system_prompt_cache()` 接口。
   - `_build_context_suffix()` 复用 `MemoryManager.build_system_prompt(user_id)` 获取 L0，并读取 `PARTNER.md` 和用户 nickname。
   - `detect_and_build()` 改为 async 并接收 `user_id`。

2. **`lib/chat/agent_runner.py`** ✅
   - `_build_context_frame()` 不再注入 L0 用户画像和 PARTNER.md。
   - 只保留：当前日期、L1 近期对话、L2 外部召回、skills、deferred hint。
   - `detect_and_build()` 调用改为 `await detect_and_build(user_input, user_id)`。

3. **记忆写入后失效缓存** ✅
   已在以下位置调用 `invalidate_system_prompt_cache(user_id)`：
   - `lib/tools/memory.py`：`memory` 工具执行后（`_memory_add` / `_memory_replace_remove`）
   - `lib/tools/profile.py`：`update_profile` 工具执行后
   - `lib/memory/understanding.py`：AI 画像刷新后
   - `server/routes/memory.py`：`PUT /me`、`PUT /partner`、`POST /reset`、`POST /understanding/correct`、`POST /tell`
   - `lib/memory/housekeeping.py`：过期条目整理写入后
   - `eval/ingest.py`：benchmark 数据摄入后

#### 风险与应对

| 风险 | 应对 |
|------|------|
| L0 更新后 system prompt 未失效 | 在所有记忆写入路径统一调用 invalidate |
| L0 变化导致 system prompt context 失效 | L0 放在 system prompt 末尾作为 suffix，变化时只影响 L0 本身及后面的 user message，前面 stable 前缀仍可能命中 |
| system prompt 过长挤占 context window | 对 L0 做长度限制，超过时截断或摘要 |
| 多用户场景下缓存 key 冲突 | 缓存按 `(user_id, config_fingerprint)` key |
| snapshot.py 和 system_prompt_builder 重复产出 L0 | L0 整块移入 system_prompt_builder，snapshot 退化为只产出 L1，避免双写 |

---

### 方案 2：历史轮次回放策略（P1）

#### 思路

当前 `Session.get_history()` 把每轮存储的 `llm_context_frame` 原样回放：

```python
context_frame = m.get("llm_context_frame")
if context_frame:
    out.append({"role": "user", "content": context_frame})
```

这导致 history 里每一轮都带一个 context_frame，动态内容随对话轮数线性增长。

#### 尝试 1：历史轮只回放用户原话（已否决）

曾尝试让历史 user 轮只回放 `user_content`，当前轮才附加完整 `context_frame`。理论上是"去重"，但实测 `call:1` 缓存率从 **85~88%** 暴跌到 **63~64%**。根因是它破坏了 prefix cache 的**滚动前缀（rolling prefix）**机制。

##### 滚动前缀机制

DeepSeek prefix cache 的关键性质：**每一轮 call:1 的完整输入，会成为下一轮的可缓存前缀**——前提是历史被逐字回放。

**保留历史 context_frame（命中链成立）：**

```text
Turn N   = [sys][ctx1][msg1][r1][ctx2][msg2][r2] … [ctxN][msgN]
Turn N+1 = Turn N 的全部 + [rN][ctx_{N+1}][msg_{N+1}]
```

Turn N+1 的前缀与 Turn N 完全一致 → 整段命中，只写 delta `[rN][ctx_{N+1}][msg_{N+1}]`（实测 ~1900 tokens）。这就是 `cache_read` 每轮 +1900 紧跟 `input`、`cache_write` 稳定在 ~1900 的来源。

**历史轮只回放用户原话（命中链断裂）：**

```text
Turn N   = [sys][msg1][r1] … [r_{N-1}][ctxN][msgN]
Turn N-1 = [sys][msg1][r1] … [r_{N-2}][ctx_{N-1}][msg_{N-1}]
```

对比前缀：到 `[r_{N-2}]` 之后，Turn N 接 `[msg_{N-1}]`，Turn N-1 接 `[ctx_{N-1}]` → 在此发散。当前轮的 ctx 注入在末尾，但历史轮没有 ctx，于是 ctx 的"插入点"每轮位置都不同 → 命中链断在上一个插入点，之后全量重写 → 缓存率跌到 63%。

因此**保留历史轮完整 context_frame 回放**，让 `ctx_K + msg_K` 作为整体留在历史里，维持滚动前缀。

##### 通用准则（后续 Phase 必须遵守）

- **"动态内容"本身不是敌人**：它写一次就永久进缓存（当轮的 `cache_write`，下一轮起变成别人的 `cache_read`）。
- **真正的敌人是"打破滚动前缀"的任何改动**：在非尾部位置插内容、跨轮结构不一致、历史回放与当初发送不一致。
- 硬约束：**历史逐字回放，只有当前轮在末尾追加**，绝不在历史中间改结构。

#### 结论

Phase 2 不再对 history 做删减，而是把优化重点转向**压缩当前轮 context_frame 本身**（Phase 3）：减少 L1 长度、控制 L2 召回、避免日期重复，从而降低每轮需要新写入的 token 数。

| 策略 | call:1 缓存率（实测） | 结论 |
|------|----------------------|------|
| 历史轮保留完整 context_frame | 81~90% | ✅ 保留 |
| 历史轮只回放用户原话 | 63~64% | ❌ 否决 |

---

### 方案 3：压缩 context_frame 冗余（P2）

#### 3.1 日期去重

当前 context_frame 里日期出现两次：

```text
当前日期：2026-06-13
...
Current date: 2026-06-13
```

只保留一个。

#### 3.2 L0 去重

当前 `_build_fixed_block()` 从 `USER.md` 读取的是聚合后的画像内容，`_build_context_block()` 是 L1 近期对话，没有同时返回原始条目和聚合版的问题。实施方案 1 后 L0 已移出 context_frame，本条自动解决。

#### 3.3 L2 召回严格限长

当前 L2 为空，启用外部 provider 后可能返回大量内容：

```python
dynamic_ctx = await manager.build_context(...)
if estimate_tokens(dynamic_ctx) > 500:
    dynamic_ctx = truncate_to_tokens(dynamic_ctx, 500)
```

#### 3.4 Skills / deferred hint 按需注入

只有触发 skill 时才注入 skills_frame，只有存在延迟工具时才注入 deferred_hint。当前已经是这样。

---

### 方案 4：监控与告警（P3）

#### 改造点

1. **`core/agent.py`**
   - `_log_usage()` 增加 `cache_write_pct` 字段：
     ```python
     cache_write_pct = round((cache_w / input_t) * 100, 1) if input_t else 0.0
     ```
   - 当 `scope == "call:1"` 且 `cache_hit_pct < 85%` 时打 warning。

---

## 实施顺序

| 阶段 | 内容 | 预期 call:1 命中率 |
|------|------|-------------------|
| Phase 1 | system prompt 内部分层：stable + volatile suffix（L0/PARTNER 移入） | 85~92% | ✅ 已完成 |
| Phase 2 | history 回放策略验证：保留完整 context_frame 作为前缀 anchor | 81~90% | ✅ 已完成（否决"历史轮去重"方案） |
| Phase 3 | context_frame 压缩：L1 摘要化、日期去重、L2 限长、历史工具结果/assistant 回复截断 | 90~93% | ✅ 已完成（最优配置：assistant 800 字符、tool result 1500 字符；否决 500 字符与滑动窗口） |
| Phase 4 | 监控告警 | - | 部分完成（已增加 cache_write_pct） |

---

## 预期效果

| 指标 | 当前 | 优化后 |
|------|------|--------|
| system prompt stable | 1,506 | 1,506 |
| system prompt context（L0 + PARTNER） | 0 | ~620 |
| context_frame tokens | ~636 | ~100~150 |
| call:1 平均命中率 | 34~80% | **90~95%** |
| 整体缓存命中率 | 66.8% | **85~90%** |

> **理论天花板**：`call:1` 命中率 = prefix / (prefix + delta)，其中 delta = `[上一轮 AI 回复 + 当前 context_frame + 当前用户消息]`。"上一轮 AI 回复"和"用户消息"不可压缩，**唯一能动的是当前 context_frame（L1/L2/日期）**。因此 Phase 3 的现实区间更接近 **90~93%**，难达 95%——L1 摘要化是主战场，但有收益递减。

---

## Phase 1 实施记录

实施时间：2026-06-13

### 改动文件

| 文件 | 改动 |
|------|------|
| `lib/agent/system_prompt_builder.py` | 重构为 `build_stable_system_prompt()` + `build_system_prompt(user_id)`；增加 `_build_context_suffix()`（nickname + L0 + PARTNER.md）；按 `(user_id, fingerprint)` 缓存；增加失效接口 |
| `lib/memory/snapshot.py` | `build_snapshot` 退化为 `build_recent_context`，只返回 L1；删除 L0 构建逻辑 |
| `lib/chat/agent_runner.py` | `_build_context_frame` 移除 L0/PARTNER；`detect_and_build` 改为 async + user_id |
| `lib/tools/memory.py` | memory 工具写入后失效 system prompt 缓存 |
| `lib/tools/profile.py` | update_profile 写入后失效缓存 |
| `lib/memory/understanding.py` | AI 画像刷新写入 USER.md 后失效缓存 |
| `server/routes/memory.py` | 各记忆写入接口后失效缓存 |
| `lib/memory/housekeeping.py` | 过期整理写入后失效缓存 |
| `eval/ingest.py` | benchmark 数据摄入后失效缓存 |
| `core/agent.py` | `_log_usage` 增加 `cache_write_pct`；修复 `_looks_like_fact_question` 未定义问题 |

### 实测结果

启动后端后，用新用户连续发送 5 轮闲聊（不主动保存记忆）：

| 轮次 | scope | input | cache_read | cache_write | cache_hit_pct |
|------|-------|-------|------------|-------------|---------------|
| 1 | call:1 | 13,955 | 12,032 | 1,923 | 86.2% |
| 2 | call:1 | 15,952 | 13,952 | 2,000 | 87.5% |
| 3 | call:1 | - | - | - | 83.8%（后台 review 写入导致缓存失效） |

- 改造前 `call:1` 常见 **20~65%**；改造后稳定段提升到 **85~90%**。
- `system prompt cache hit` 日志确认 L0 + PARTNER 已按用户缓存。
- 当用户通过 memory/update_profile/tell 等写入记忆时，日志出现 `system prompt cache invalidated`，下轮重建新缓存并命中。

### 仍存在的限制

- `call:1` 尚未稳定 ≥90%，主要受 **history 中每轮回放完整 context_frame** 和 **后台 review 写入** 影响。这将在 Phase 2/3 继续优化。

## Phase 2 实施记录

实施时间：2026-06-13

### 改动文件

| 文件 | 改动 |
|------|------|
| `lib/session/manager.py` | 验证后保留原方案：所有 user 轮次原样回放 `llm_context_frame`，作为 prefix cache anchor |
| `lib/chat/agent_runner.py` | 回滚 `_build_context_frame()` 返回 `(full, stable)` tuple 的改动，恢复为返回单个 `str`；不再存储 `llm_context_frame_stable` |

### 实测结果

使用 user `cache_test_p2_v4_user` 连续 7 轮闲聊：

| 轮次 | scope | input | cache_read | cache_write | cache_hit_pct |
|------|-------|-------|------------|-------------|---------------|
| 1 | call:1 | 8,425 | 8,320 | 105 | 98.8% |
| 2 | call:1 | 10,234 | 8,320 | 1,914 | 81.3% |
| 3 | call:1 | 12,082 | 10,112 | 1,970 | 83.7% |
| 4 | call:1 | 13,912 | 12,032 | 1,880 | 86.5% |
| 5 | call:1 | 15,871 | 13,824 | 2,047 | 87.1% |
| 6 | call:1 | 17,801 | 15,744 | 2,057 | 88.4% |
| 7 | call:1 | 19,742 | 17,792 | 1,950 | 90.1% |

作为对比，"历史轮只回放用户原话"方案（`cache_test_p2_v3_user`）实测：

| 轮次 | cache_hit_pct |
|------|---------------|
| 1 | 63.4% |
| 2 | 64.0% |
| 3 | 63.1% |
| 4 | 48.2% |

### 结论

- **保留历史完整 context_frame** 方案：缓存率 81~90%，随轮次上升。
- **历史轮去重方案**：缓存率 63~64%，显著低于前者。
- 原因：DeepSeek 的 prefix cache 依赖前缀完全匹配，历史中的完整 `context_frame` 为当前轮提供了稳定的 prefix anchor；去掉后当前轮需要重新写入更多 token。

### 仍存在的限制

- 仍未稳定 ≥90%，主要受 L1 近期对话每轮变化、input 随历史增长、后台 review 写入影响。
- 下一步 Phase 3：压缩 context_frame（L1 摘要化、L2 限长、日期去重），把每轮新增 token 控制在更小范围。

## Phase 3 第一轮实施记录

实施时间：2026-06-13

### 改动文件

| 文件 | 改动 |
|------|------|
| `lib/memory/snapshot.py` | 增加 `_maybe_summarize_context()`：当 L1 上下文超过 350 tokens 时，调用 LLM 压缩成 ≤180 tokens 的摘要；失败时硬截断 |

### 实测结果

使用 user `cache_test_p3_v1_user` 连续 7 轮闲聊：

| 轮次 | scope | input | cache_read | cache_write | cache_hit_pct |
|------|-------|-------|------------|-------------|---------------|
| 1 | call:1 | 8,425 | 8,320 | 105 | 98.8% |
| 2 | call:1 | 10,234 | 8,320 | 1,914 | 81.3% |
| 3 | call:1 | 12,139 | 10,112 | 2,027 | 83.3% |
| 4 | call:1 | 13,964 | 12,032 | 1,932 | 86.2% |
| 5 | call:1 | 16,587 | 13,952 | 2,635 | 84.1% |
| 6 | call:1 | 19,079 | 16,512 | 2,567 | 86.5% |
| 7 | call:1 | 22,873 | 19,072 | 3,801 | 83.4% |

### 结论

- L1 摘要化**未触发**（日志中没有 `L1 context summarized`），因为 L1 原始内容未达到 350 tokens 阈值。
- `call:1` 缓存率与 Phase 2 回滚后基本持平（81~86%），没有明显改善。
- input 从 8,425 增长到 22,873，主要增长来源不是 L1，而是：
  - 历史 assistant 回复累积
  - 工具调用结果（特别是 `web_search` / `web_extract` 返回的网页内容）进入 history

### 下一步调整

Phase 3 重点应从 L1 摘要化转向**压缩历史消息中的工具结果和 assistant 回复**：

1. **历史工具结果激进截断**：非当前轮的工具结果从 10,000 字符降到 1,000~2,000 字符。
2. **历史 assistant 回复长度限制**：超过阈值时做摘要或截断。
3. **限制完整 history 轮数**：超过 N 轮后，旧轮次只保留用户原话 + assistant 摘要。

## Phase 3 第二轮实施记录：固定 history 长度

实施时间：2026-06-13

### 改动文件

| 文件 | 改动 |
|------|------|
| `lib/chat/agent_runner.py` | 将 `_MEMORY_WINDOW` 固定为较小值（当前代码为 8），意图限制携带的完整历史消息数。 |

### 关键发现

`lib/session/manager.py` 的 `get_history()` 在传入 `start_index` 时会取 `self.messages[start:]`，此时 `max_messages` 参数只用于非正数判断，**不会真正截断**。因此 `_MEMORY_WINDOW` 当前实际上并未限制历史长度，历史会完整携带。

完整历史（配合后续 assistant / tool result 截断）的实测表现反而优于显式滑动窗口，见 Phase 3 第三轮。

## Phase 3 第三轮实施记录：历史工具结果 + Assistant 回复截断

实施时间：2026-06-13

### 改动文件

| 文件 | 改动 |
|------|------|
| `lib/session/manager.py` | 引入 `_HISTORY_TOOL_RESULT_CHAR_BUDGET = 1500`：非当前轮的工具结果截断到 1,500 字符；当前轮保留 `_TOOL_RESULT_CHAR_BUDGET = 3000`。 |
| `lib/session/manager.py` | 引入 `_ASSISTANT_HISTORY_CHAR_BUDGET = 800`：非当前轮的 assistant 文本回复超过 800 字符时截断并追加 `…（历史回复已截断）`；当前轮保留完整回复。 |

### 实测结果

使用 user `cache_test_p3_v4_user_3`，在同一 conversation 内连续 12 轮，每轮请求模型“用约 300 词简述 AI 记忆系统”：

| 轮次 | input | cache_read | cache_write | cache_hit_pct | history_messages |
|------|-------|------------|-------------|---------------|------------------|
| 1 | 8,439 | 8,320 | 119 | 98.6% | 0 |
| 2 | 10,571 | 8,320 | 2,251 | 78.7% | 3 |
| 3 | 12,675 | 10,496 | 2,179 | 82.8% | 6 |
| 4 | 14,753 | 12,672 | 2,081 | 85.9% | 9 |
| 5 | 16,813 | 14,720 | 2,093 | 87.6% | 12 |
| 6 | 18,795 | 16,768 | 2,027 | 89.2% | 15 |
| 7 | 20,827 | 18,688 | 2,139 | 89.7% | 18 |
| 8 | 22,954 | 20,736 | 2,218 | 90.3% | 21 |
| 9 | 25,109 | 22,912 | 2,197 | 91.3% | 24 |
| 10 | 27,273 | 25,088 | 2,185 | 92.0% | 27 |
| 11 | 29,471 | 27,264 | 2,207 | 92.5% | 30 |
| 12 | 31,606 | 29,440 | 2,166 | 93.1% | 33 |

### 结论

- **第 8 轮起 `call:1` 缓存率稳定超过 90%**，第 12 轮达到 **93.1%**。
- 每轮新增 `cache_write` 稳定在约 2,100 tokens，主要是“上一轮 assistant 回复 + 当前 context_frame + 当前用户消息”。
- 历史 assistant 截断到 800 字符对闲聊场景足够，且没有观察到回复质量明显劣化。

## Phase 3 第四轮实施记录：激进压缩（失败）

实施时间：2026-06-13

### 尝试 1：Assistant 历史回复截断到 500 字符

将 `_ASSISTANT_HISTORY_CHAR_BUDGET` 从 800 降到 500。

#### 实测结果

使用 user `cache_test_p3_v4_user_500`，同一 conversation 连续 12 轮：

| 轮次 | input | cache_read | cache_write | cache_hit_pct | history_messages |
|------|-------|------------|-------------|---------------|------------------|
| 1 | 8,439 | 8,320 | 119 | 98.6% | 0 |
| 2 | 10,648 | 8,320 | 2,328 | 78.1% | 3 |
| 3 | 12,561 | 8,320 | 4,241 | 66.2% | 6 |
| 4 | 14,672 | 8,448 | 6,224 | 57.6% | 9 |
| 5 | 16,814 | 10,624 | 6,190 | 63.2% | 12 |
| 6 | 18,845 | 12,672 | 6,173 | 67.2% | 15 |
| 7 | 20,952 | 14,848 | 6,104 | 70.9% | 18 |
| 8 | 23,123 | 16,896 | 6,227 | 73.1% | 21 |
| 9 | 25,273 | 19,072 | 6,201 | 75.5% | 24 |
| 10 | 27,327 | 21,120 | 6,207 | 77.3% | 27 |
| 11 | 29,171 | 23,296 | 5,875 | 79.9% | 30 |
| 12 | 30,989 | 29,056 | 1,933 | 93.8% | 33 |

#### 结论

- 缓存率**显著下降**：第 4~11 轮只有 57~80%，远低于 800 字符预算时的 85~92%。
- 第 12 轮的 93.8% 是因为模型已学会极短回复（输出仅 38 tokens），并非压缩本身带来好处。
- 模型在看到历史回复被截断后，后期开始出现“够了。”、“扯淡。”等异常短回复，**质量受损**。

**原因分析**：这是与滑动窗口（尝试 2）**不同类型的失败**，不要混为一谈。

- 500 截断的命中率**仍随轮次爬升**（`cache_read` 从 8,320 涨到 29,056），说明滚动前缀并未像滑动窗口那样完全断裂——KV cache 仍在累积复用。
- 真正的主要损伤是**质量崩塌**：模型看到历史回复被截断后，后期退化成”够了。””扯淡。”等极短回复（第 12 轮仅 38 tokens），第 12 轮的 93.8% 也是这种退化带来的假象。
- 缓存方面的异常是每轮 `cache_write` 飙到 ~6,200 tokens（800 配置约 2,100），具体机制尚不明确（可能是截断点与 DeepSeek 缓存分块对齐的交互），但 500 既没换来缓存收益、又损质量。

> **关键区分**：滑动窗口 = 干净的滚动前缀断裂（命中率**平跌卡死**在 48%，`cache_read` 卡在 8,320）；过度截断 = **质量悬崖**（命中率仍爬升，但回复退化）。前者是缓存机制禁区，后者是质量取舍有个甜点位（~800 字符），低于它质量先崩。

### 尝试 2：显式限制历史消息数为 12 条

在 `Session.get_history()` 的 `start_index` 分支也应用 `max_messages` 截断，使 `_MEMORY_WINDOW=8` 真正生效。

#### 实测结果

使用 user `cache_test_p3_v4_user_limited`，同一 conversation 连续 12 轮：

| 轮次 | input | cache_read | cache_write | cache_hit_pct | history_messages |
|------|-------|------------|-------------|---------------|------------------|
| 1 | 8,439 | 8,320 | 119 | 98.6% | 0 |
| 2 | 10,567 | 8,320 | 2,247 | 78.7% | 3 |
| 3 | 12,563 | 8,320 | 4,243 | 66.2% | 6 |
| 4 | 14,728 | 12,544 | 2,184 | 85.2% | 9 |
| 5 | 16,871 | 14,720 | 2,151 | 87.3% | 12 |
| 6 | 17,098 | 8,320 | 8,778 | 48.7% | 12 |
| 7 | 17,146 | 8,320 | 8,826 | 48.5% | 12 |
| 8 | 17,161 | 8,320 | 8,841 | 48.5% | 12 |
| 9 | 17,236 | 8,320 | 8,916 | 48.3% | 12 |
| 10 | 17,210 | 8,320 | 8,890 | 48.3% | 12 |
| 11 | 17,238 | 8,320 | 8,918 | 48.3% | 12 |
| 12 | 17,249 | 8,320 | 8,929 | 48.2% | 12 |

#### 结论

- 一旦 history_messages 达到上限（12 条）后，缓存率**暴跌并稳定在 48% 左右**。
- 原因：滑动窗口每轮丢弃最旧的一轮，导致历史部分的前缀无法复用；DeepSeek 只能命中 system + context_frame（约 8,320 tokens），后面 8,800+ tokens 全部重写。
- 这说明**对 DeepSeek prefix cache 而言，滑动窗口比完整历史更差**。

## Phase 3 最终结论

| 策略 | `call:1` 缓存率（实测） | 结论 |
|------|------------------------|------|
| 完整历史 + assistant 800 字符截断 + 工具结果 1500 字符截断 | 第 8 轮起 90%+，第 12 轮 93.1% | ✅ 当前最优 |
| Assistant 截断到 500 字符 | 第 4~11 轮 57~80%，且质量受损 | ❌ 否决 |
| 显式限制历史消息数（滑动窗口） | 上限后稳定在 48% | ❌ 否决 |

### 当前推荐配置

```python
# lib/session/manager.py
_TOOL_RESULT_CHAR_BUDGET = 3000
_HISTORY_TOOL_RESULT_CHAR_BUDGET = 1500
_ASSISTANT_HISTORY_CHAR_BUDGET = 800
```

- **历史 assistant 回复保留 800 字符截断**：保留足够的跨轮共享前缀，同时控制 input 增长。
- **历史工具结果保留 1,500 字符截断**：非当前轮工具结果大幅压缩，当前轮保留 3,000 字符。
- **不启用显式滑动窗口**：当前 `_MEMORY_WINDOW` 在 `start_index` 模式下未真正生效，完整历史反而更有利于滚动前缀命中。

### 仍存在的限制

- 前 3 轮缓存率仍较低（66~82%），这是正常滚动前缀建立过程，难以避免。
- 长对话（>12 轮）input 会持续增长，但目前测试显示缓存率仍随轮次上升（第 12 轮 93.1%）。
- 后台 `review_service.py` 的 memory review 写入会触发 system prompt 缓存失效，是不稳定因素之一。

### 下一步可选方向

1. **context_frame 进一步压缩**：L1 近期对话如果超过阈值，可尝试更积极的摘要策略。
2. **长对话旧历史摘要化**：当 conversation 超过 20 轮时，将旧历史压缩为固定摘要，避免滑动窗口破坏前缀。
3. **探索固定锚点方案**：在 history 开头保留一个固定文本锚点，使后续内容即使滑动也能复用前缀。

## 验证方法

1. **连续多轮对话**
   - 启动后端，发起 5~10 轮对话。
   - 观察 `scope=call:1` 的 `cache_hit_pct`。
   - 预期：稳定在 90% 以上。

2. **记忆更新测试**
   - 用户说"记住我叫小明"。
   - 下一轮 system prompt 应包含新记忆。
   - 该轮缓存率可能短暂下降，但后续恢复高位。

3. **L2 启用测试**
   - 启用 honcho/akasha。
   - 确认 L2 返回被截断，不会导致 call:1 暴跌。

4. **长对话测试**
   - 进行 20+ 轮对话。
   - 确认 call:1 缓存率不随历史长度明显下降。

---

## 相关代码位置

| 文件 | 职责 |
|------|------|
| `lib/agent/system_prompt_builder.py` | 构建分层 system prompt，增加 user_id 参数和缓存失效 |
| `lib/chat/agent_runner.py` | 构建精简 context_frame，移除 L0/PARTNER |
| `lib/session/manager.py` | history 保留每轮完整 `llm_context_frame` 回放，作为 prefix cache anchor |
| `lib/memory/snapshot.py` | L0 移走后退化为 L1-only 快照；需移除 L0 构建逻辑或改为可选 |
| `lib/memory/manager.py` | 提供 `build_system_prompt(user_id)` 给 system_prompt_builder 使用；L2 召回增加长度限制 |
| `lib/tools/memory.py` | `memory` 工具执行后，失效 system prompt 缓存 |
| `lib/tools/profile.py` | `update_profile` 工具执行后，失效 system prompt 缓存 |
| `lib/memory/understanding.py` | AI 画像刷新，失效 system prompt 缓存 |
| `core/agent.py` | `_log_usage()` 增加动态比例监控 |

---

## 附录：消息序列对比

### 当前

```text
[system]  stable system prompt（1,506 tokens）

[user]    === context_frame of turn 1 ===
          当前日期：2026-06-13
          # 用户记忆（L0 + L1，~550 tokens）     <- 半稳定，被拖入 volatile
          <partner-rules>
          <memory-context>Current date: 2026-06-13</memory-context>

[user]    性格呢

[assistant] ...

[user]    === context_frame of turn 2 ===          <- 新的，miss
          当前日期：2026-06-13
          # 用户记忆（L0 + L1，~550 tokens）
          ...

[user]    七点作业吧
```

### 优化后

```text
[system]
  ├── stable block（1,506 tokens）                 <- 长期缓存
  └── context block: L0 + PARTNER（~620 tokens）   <- 跨 session 相对稳定，放末尾

[user]    === context_frame ===
          当前日期：2026-06-13
          # 近期对话（L1，动态）
          <memory-context>L2 召回</memory-context>

[user]    性格呢

[assistant] ...

[user]    七点作业吧（历史轮次不再回放 turn 1 的 context_frame）
```

> 语义对齐：L0 属于 context 层（跨 session 相对稳定），日期/L1/L2/skills 属于 volatile 层（每轮变化）。不要把 L0 和真 volatile 混为一谈。

动态内容占比从 ~30% 降到 ~5%，call:1 缓存率可稳定 90% 以上。

---

## 参考：hermes-agent 的分层缓存

`E:\OpenHub\hermes-agent` 在 `run_agent.py:_build_system_prompt_parts()` 中明确把 system prompt 分成 `stable` / `context` / `volatile` 三层，并按 `"stable\n\ncontext\n\nvolatile"` 顺序拼接。在 `agent/prompt_caching.py` 中通过 Anthropic `cache_control` 对 stable block 标记 1h TTL、对最近消息标记 5m TTL，实现跨 session 和 session 内的分层缓存。

Lumen 使用 DeepSeek，无法使用 `cache_control`，但**分层排序思想完全适用**：把 stable 内容放在 token 序列最前，把 volatile 内容放在最后，最大化前缀缓存命中。
