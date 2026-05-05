# PydanticAI 迁移计划

## TL;DR

> **目标**：将 CareerOS 手搓的 ReAct Loop Agent 系统迁移到 PydanticAI 框架
> 
> **核心收益**：
> - 代码量减少 50%+
> - 类型安全（Pydantic 原生）
> - 官方维护，社区活跃
> - 支持 MCP/A2A/多 Agent 扩展
> 
> **预计工作量**：8-12 小时
> **并行执行**：YES - 3 个 Wave

---

## Context

### 原始需求

CareerOS 当前使用手搓的 ReAct Loop 实现 Agent 系统：
- `agent_loop.py` — 手动实现 Thought → Action → Observation 循环
- `tools.py` — 自定义 ToolRegistry 类
- `llm_router.py` — LiteLLM 调用封装
- `orchestrator.py` — 意图分类 + 系统提示词组装

**问题**：
- 代码复杂，维护成本高
- 缺乏类型安全
- 流式输出需要手动处理
- 工具调用日志需要自定义实现

### 技术调研结论

已完成技术调研（`_bmad/bmm/planning-artifacts/research/technical-pydantic-ai-research-2026-05-05.md`），结论：
- ✅ PydanticAI 与现有架构完全兼容
- ✅ 迁移成本适中（8-12 小时）
- ✅ 风险可控

---

## Work Objectives

### 核心目标

将 Agent 系统从手搓 ReAct Loop 迁移到 PydanticAI 框架，保持所有现有功能不变。

### 具体交付物

1. **新文件**：
   - `app/backend/agent/pydantic_agent.py` — PydanticAI Agent 定义
   - `app/backend/agent/deps.py` — 依赖类型定义

2. **重构文件**：
   - `app/backend/agent/tools.py` — 从 ToolRegistry 改为 @agent.tool 装饰器
   - `app/backend/services/chat_service.py` — 使用 PydanticAI Agent

3. **删除文件**：
   - `app/backend/agent/agent_loop.py` — 旧的 ReAct Loop 实现

4. **保留文件**：
   - `app/backend/agent/llm_router.py` — 保留作为 LiteLLM 适配层
   - `app/backend/agent/orchestrator.py` — 保留意图分类逻辑

### Definition of Done

- [ ] 所有现有测试通过
- [ ] 流式对话功能正常
- [ ] 工具调用（get_profile/update_profile/diagnose_jd）正常
- [ ] Agent 可观测性（traces）正常
- [ ] 前端 SSE 接口兼容

### Must Have

- PydanticAI Agent 替代手搓 ReAct Loop
- 工具注册使用 @agent.tool 装饰器
- 流式输出使用 Agent.run_stream()
- 依赖注入使用 RunContext
- 保留 LiteLLM 多 Provider 支持

### Must NOT Have

- 不改变前端 API 接口
- 不改变 SQLite 数据模型
- 不改变 Cognee 记忆层集成
- 不引入新的重量级依赖

---

## Verification Strategy

### 测试决策

- **Framework**: pytest + pytest-asyncio
- **Tests**: 运行现有测试，确保功能一致

---

## Execution Strategy

### 并行执行 Wave

```
Wave 1 (基础层 - 可并行):
├── Task 1: 安装 PydanticAI + 创建依赖类型 [quick]
├── Task 2: 创建 PydanticAI Agent 定义 [quick]
└── Task 3: 重构工具注册 [unspecified-high]

Wave 2 (集成层 - 依赖 Wave 1):
├── Task 4: 实现动态系统提示词 [quick]
├── Task 5: 实现流式对话 [unspecified-high]
└── Task 6: 更新 chat_service.py [unspecified-high]

Wave 3 (清理 + 验证):
├── Task 7: 删除旧代码 [quick]
├── Task 8: 更新 orchestrator.py [quick]
└── Task 9: 运行测试 + 修复问题 [deep]
```

---

## TODOs

- [ ] 1. 安装 PydanticAI + 创建依赖类型

  **What to do**:
  - 安装 pydantic-ai 包
  - 创建 `app/backend/agent/deps.py`
  - 定义 CareerOSDeps 数据类：
    - user_id: str
    - db: AsyncSession
    - user_profile: dict | None

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Tasks 4, 5, 6
  - **Blocked By**: None

  **References**:
  - 技术调研报告第 2.3 节 — SQLAlchemy 集成
  - PydanticAI 官方文档：https://ai.pydantic.dev/dependencies/

  **Acceptance Criteria**:
  - [ ] pydantic-ai 安装成功
  - [ ] CareerOSDeps 类型定义完整
  - [ ] 类型提示正确

  **Commit**: YES
  - Message: `deps: add pydantic-ai and define CareerOSDeps`
  - Files: `requirements.txt`, `app/backend/agent/deps.py`

---

- [ ] 2. 创建 PydanticAI Agent 定义

  **What to do**:
  - 创建 `app/backend/agent/pydantic_agent.py`
  - 初始化 Agent 实例
  - 配置 LiteLLM Provider（通过 llm_router）
  - 设置基础系统提示词

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Tasks 4, 5, 6
  - **Blocked By**: None

  **References**:
  - 技术调研报告第 2.1 节 — LiteLLM 集成
  - `app/backend/agent/llm_router.py` — 现有 LLM 配置

  **Acceptance Criteria**:
  - [ ] Agent 实例创建成功
  - [ ] LiteLLM Provider 配置正确
  - [ ] 基础系统提示词设置

  **Commit**: YES
  - Message: `feat(agent): create PydanticAI agent definition`
  - Files: `app/backend/agent/pydantic_agent.py`

---

- [ ] 3. 重构工具注册

  **What to do**:
  - 将 `tools.py` 中的 ToolRegistry 改为 @agent.tool 装饰器
  - 迁移 3 个工具：
    - get_profile
    - update_profile
    - diagnose_jd
  - 使用 RunContext 访问依赖

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Tasks 4, 5, 6
  - **Blocked By**: None

  **References**:
  - `app/backend/agent/tools.py` — 现有工具实现
  - 技术调研报告第 2 节 — 工具注册模式

  **Acceptance Criteria**:
  - [ ] 3 个工具使用 @agent.tool 装饰器
  - [ ] RunContext 正确注入依赖
  - [ ] 工具功能与现有实现一致

  **Commit**: YES
  - Message: `refactor(agent): migrate tools to PydanticAI decorators`
  - Files: `app/backend/agent/tools.py`

---

- [ ] 4. 实现动态系统提示词

  **What to do**:
  - 使用 @agent.system_prompt 装饰器
  - 加载用户画像
  - 加载 Cognee 记忆
  - 加载对话摘要

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6)
  - **Blocks**: Task 6
  - **Blocked By**: Tasks 1, 2

  **References**:
  - `app/backend/agent/orchestrator.py` — 现有提示词组装逻辑
  - 技术调研报告第 3.2 节 — 动态系统提示词

  **Acceptance Criteria**:
  - [ ] 系统提示词包含用户画像
  - [ ] 系统提示词包含相关记忆
  - [ ] 动态更新正常

  **Commit**: YES
  - Message: `feat(agent): implement dynamic system prompt`
  - Files: `app/backend/agent/pydantic_agent.py`

---

- [ ] 5. 实现流式对话

  **What to do**:
  - 使用 Agent.run_stream() 实现流式输出
  - 适配 SSE 格式（与现有前端兼容）
  - 处理工具调用事件

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 6)
  - **Blocks**: Task 6
  - **Blocked By**: Tasks 1, 2

  **References**:
  - 技术调研报告第 4 节 — 流式输出
  - `app/backend/services/chat_service.py` — 现有 SSE 实现

  **Acceptance Criteria**:
  - [ ] 流式输出正常
  - [ ] SSE 格式兼容前端
  - [ ] 工具调用事件正确处理

  **Commit**: YES
  - Message: `feat(agent): implement streaming with Agent.run_stream()`
  - Files: `app/backend/agent/pydantic_agent.py`

---

- [ ] 6. 更新 chat_service.py

  **What to do**:
  - 替换 agent_loop() 调用为 PydanticAI Agent
  - 保留 SSE 流式输出格式
  - 保留 Agent 可观测性（traces）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (after Tasks 4, 5)
  - **Blocks**: Task 7
  - **Blocked By**: Tasks 4, 5

  **References**:
  - `app/backend/services/chat_service.py` — 现有实现
  - Tasks 4, 5 的输出

  **Acceptance Criteria**:
  - [ ] 使用 PydanticAI Agent 替代 agent_loop
  - [ ] SSE 格式不变
  - [ ] Agent traces 正确记录

  **Commit**: YES
  - Message: `refactor(chat): use PydanticAI agent`
  - Files: `app/backend/services/chat_service.py`

---

- [ ] 7. 删除旧代码

  **What to do**:
  - 删除 `app/backend/agent/agent_loop.py`
  - 清理对 agent_loop 的引用
  - 更新导入

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 9)
  - **Blocks**: Task 9
  - **Blocked By**: Task 6

  **References**:
  - `app/backend/agent/agent_loop.py` — 要删除的文件
  - 全局搜索 "agent_loop" 确保无遗漏

  **Acceptance Criteria**:
  - [ ] 文件删除
  - [ ] 无残留引用
  - [ ] 应用正常启动

  **Commit**: YES
  - Message: `chore: remove old agent_loop.py`
  - Files: `app/backend/agent/agent_loop.py`

---

- [ ] 8. 更新 orchestrator.py

  **What to do**:
  - 保留意图分类逻辑
  - 移除旧的 _retrieve_memories 函数
  - 更新 run_orchestrator 为 PydanticAI 兼容

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 7, 9)
  - **Blocks**: Task 9
  - **Blocked By**: Task 6

  **References**:
  - `app/backend/agent/orchestrator.py` — 现有实现

  **Acceptance Criteria**:
  - [ ] 意图分类功能保留
  - [ ] 与 PydanticAI Agent 集成正常
  - [ ] 无冗余代码

  **Commit**: YES
  - Message: `refactor(orchestrator): simplify for PydanticAI`
  - Files: `app/backend/agent/orchestrator.py`

---

- [ ] 9. 运行测试 + 修复问题

  **What to do**:
  - 运行 pytest 确保所有测试通过
  - 运行 ruff check 确保无 lint 错误
  - 修复任何问题

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (after all tasks)
  - **Blocks**: None
  - **Blocked By**: All tasks

  **References**:
  - `tests/` — 测试目录

  **Acceptance Criteria**:
  - [ ] pytest 全部通过
  - [ ] ruff check 无错误
  - [ ] 应用正常启动

  **Commit**: YES
  - Message: `test: fix all tests for PydanticAI migration`
  - Files: 测试文件（如有修改）

---

## Final Verification Wave

- [ ] F1. **功能验证** — 手动测试对话、工具调用、流式输出
- [ ] F2. **代码审查** — 检查代码质量和一致性
- [ ] F3. **性能验证** — 确认延迟和 Token 消耗无异常

---

## Commit Strategy

- **Task 1-3**: Wave 1 提交
- **Task 4-6**: Wave 2 提交
- **Task 7-9**: Wave 3 提交

---

## Success Criteria

### 验证命令

```bash
pytest  # Expected: all tests pass
ruff check .  # Expected: no errors
python -m uvicorn app.backend.main:app  # Expected: app starts
```

### 最终检查

- [ ] PydanticAI Agent 替代手搓 ReAct Loop
- [ ] 所有测试通过
- [ ] 流式对话功能正常
- [ ] 工具调用功能正常
- [ ] 前端兼容
