# Memory Snapshot 数据流问题清单

> 仅记录问题现象与根因，不附修改建议。

## 1. L0 与 L1 消费同一数据源，无互斥边界

- **现象**：`build_snapshot()` 内，L0 固定块（`_build_fixed_block_v2`）和 L1 近期块（`_build_recent_block`）都从 `GrowthEvent` 全表读取并合并。L0 取了全部事件做画像聚合，L1 取了 30 天内的事件做时间线，两者覆盖范围重叠。
- **根因**：没有机制标记哪些事件已被 L0 "消费"，导致 L1 把同一批事件的原始形态再次注入 system prompt。

## 2. about_you.md 生成与 snapshot 构建存在竞态

- **现象**：`flush_projections()` 里调 `_update_understanding(user_id)` 时包装在 `asyncio.create_task` 中，是后台异步任务；而 `build_snapshot()` 是同步读取 `about_you.md` 文件。两次调用之间没有 happens-before 保证。
- **根因**：about_you.md 的写操作和 snapshot 的读操作在不同任务里并发执行，导致 snapshot 可能读到旧版本 about_you.md，也可能读到半写状态。

## 3. L0→L1 去重完全缺失

- **现象**：`facade.py` 的 `build_context()` 只做了 L1→L2 去重（`recent_ids` 过滤语义召回），但 L0 和 L1 之间没有任何去重逻辑。
- **根因**：架构设计时只考虑了 L2 与 L1 的重复，未考虑 L0（聚合态）与 L1（原始态）的语义重复。

## 4. _build_fixed_block_v2 的降级逻辑不解决重复问题

- **现象**：当 `about_you.md` 长度 ≤ 50 时，降级到 `_build_fixed_block(profile, goals, skills, preferences)`。但这两个分支的数据都源自同一批 `GrowthEvent`。
- **根因**：降级只是换了一种渲染格式，没有减少事件被重复消费的数量。

## 5. _static_cache 只缓存字符串，丢失结构化元数据

- **现象**：`_static_cache` 存储的是拼接好的最终 markdown 字符串和 `recent_event_ids`，没有记录 L0 聚合时覆盖到哪个时间点、哪些事件被 L0 消费。
- **根因**：缓存设计只考虑了"加速读取"，没有考虑"支撑跨层去重"。

## 6. _build_fixed_block_v2 返回类型不透明

- **现象**：函数返回 `str`，调用方 `build_snapshot()` 无法判断返回的是 about_you.md 内容还是降级后的字段拼接内容。
- **根因**：返回类型未携带"使用了哪个数据源"的元信息，导致上层无法根据 L0 的数据源选择来调整 L1 的查询策略。

## 7. about_you.md 的 50 字符阈值缺乏语义

- **现象**：`len(about_you.strip()) > 50` 作为判断 about_you.md 是否"可用"的唯一标准。
- **根因**：50 是任意魔法数字，与画像质量、事件覆盖度无关，可能把有效短画像误判为不可用，也可能把冗长但低质量的画像误判为可用。

## 8. project_user_to_md 与 about_you.md 的生成链路独立

- **现象**：`project_user_to_md()` 生成 `memory.md` / `skills.md` / `experiences.md`，而 `update_ai_understanding()` 独立生成 `about_you.md`。两者读取同一批 `GrowthEvent`，但彼此不知道对方的存在。
- **根因**：两个投影任务（结构化 markdown 与 AI 综合画像）是平行演进的关系，没有统一的投影协调点。

## 9. about_you.md 不包含时间戳元数据

- **现象**：`about_you.md` 文件内容里没有记录"该画像聚合了截止到哪个时间的事件"。
- **根因**：文件是纯文本 markdown，没有 front matter 或元数据头，导致无法判断其覆盖的事件时间窗口。

## 10. _build_recent_block 的过滤条件不包含"已被聚合"检查

- **现象**：`_build_recent_block` 只按 `age_days` 和 `score` 过滤，没有参数或逻辑来排除"已经被 about_you.md 或 memory.md 覆盖的事件"。
- **根因**：函数签名和内部实现都没有预留"排除已聚合事件"的扩展点。
