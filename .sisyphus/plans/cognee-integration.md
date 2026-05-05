# Cognee 集成实施计划

## TL;DR

> **目标**：替换 Mem0 + Chroma，引入 Cognee + Kuzu + LanceDB 作为成长轨迹记忆层
> 
> **核心原则**：SQLite 管状态（唯一真相源），Cognee 管记忆（派生检索层）
> 
> **关键变化**：
> - 新增 `growth_events` 表作为成长事件存储
> - 事件驱动写入，不是逐对话 LLM 提取
> - 使用 Cognee v1 API：remember/recall/improve/forget
> - 直接删除 Mem0，不并行运行
> 
> **预计工作量**：Medium（4-6 小时）
> **并行执行**：YES - 3 个 Wave

---

## Context

### 原始需求

CareerOS 需要追踪学生成长轨迹（从大一到拿 offer），而非简单的用户偏好记忆。当前 Mem0 的问题：
- 每次 `add()` 调用 LLM 提取事实，成本高
- 无时间线，无法回答"大二时我是什么水平"
- 自动提取质量不稳定

### 架构决策

已确认的架构文档：`_bmad-output/planning-artifacts/architecture.md`

核心决策：
1. **Cognee + Kuzu + LanceDB**：嵌入式，零额外容器
2. **双层架构**：SQLite（状态层）+ Cognee（记忆层）
3. **事件驱动**：只在 `growth_events` 写入时投影到 Cognee
4. **直接删除 Mem0**：不并行运行

---

## Work Objectives

### 核心目标

实现 Cognee 集成，替换 Mem0，支持成长轨迹追踪。

### 具体交付物

1. `app/backend/models/growth_event.py` — 成长事件表模型
2. `app/backend/agent/cognee_client.py` — Cognee + Kuzu + LanceDB 初始化
3. `app/backend/services/cognee_service.py` — remember/recall/rebuild 封装
4. `app/backend/services/cognee_projector.py` — SQLite → Cognee 投影
5. 更新 `chat_service.py` — 替换 `_extract_memory_bg`
6. 更新 `profile_service.py` — 画像变更时写入 growth_events
7. 更新 `routers/memory.py` — 重写为 Cognee 接口
8. 更新 `main.py` — 移除 Mem0 初始化，添加 growth_events 表创建
9. 更新 `requirements.txt` — 添加 cognee/kuzu/lancedb，删除 mem0ai/chromadb
10. 删除 `mem0_client.py`

### Definition of Done

- [ ] `pytest` 所有测试通过
- [ ] `ruff check .` 无错误
- [ ] 前端 Memories 页面能显示 Cognee 数据
- [ ] 对话后不再调用 Mem0
- [ ] `growth_events` 表正确写入

### Must Have

- Cognee 存储路径显式配置到 `~/.careeros/`
- 使用 remember/recall/improve/forget API（不是 add/cognify/search）
- 事件驱动写入，不逐对话提取
- SQLite 降级模式（Cognee 故障时直接查 SQLite）

### Must NOT Have

- 不保留 Mem0 代码或依赖
- 不在每轮对话后调用 cognee.add(raw_chat_text)
- 不使用旧的 add/cognify/search API

---

## Verification Strategy

### 测试决策

- **Infrastructure exists**: YES（pytest 已配置）
- **Automated tests**: Tests-after（先实现，后补测试）
- **Framework**: pytest + pytest-asyncio

### QA Policy

每个任务完成后，执行以下验证：
1. `ruff check .` — 无错误
2. `pytest` — 所有测试通过
3. 手动验证关键功能

---

## Execution Strategy

### 并行执行 Wave

```
Wave 1 (基础层 - 可并行):
├── Task 1: 创建 growth_events 表模型 [quick]
├── Task 2: 创建 cognee_client.py [quick]
└── Task 3: 更新 requirements.txt [quick]

Wave 2 (服务层 - 依赖 Wave 1):
├── Task 4: 创建 cognee_service.py [deep]
├── Task 5: 创建 cognee_projector.py [deep]
└── Task 6: 更新 main.py [quick]

Wave 3 (集成层 - 依赖 Wave 2):
├── Task 7: 更新 chat_service.py [unspecified-high]
├── Task 8: 更新 profile_service.py [quick]
├── Task 9: 更新 routers/memory.py [unspecified-high]
└── Task 10: 删除 mem0_client.py [quick]

Wave 4 (验证):
└── Task 11: 运行测试 + 修复问题 [deep]
```

### 依赖矩阵

- **Task 1-3**: 无依赖，可并行
- **Task 4**: 依赖 Task 1, 2
- **Task 5**: 依赖 Task 1, 4
- **Task 6**: 依赖 Task 1
- **Task 7**: 依赖 Task 4, 5
- **Task 8**: 依赖 Task 1, 5
- **Task 9**: 依赖 Task 4
- **Task 10**: 依赖 Task 7, 8, 9
- **Task 11**: 依赖所有

---

## TODOs

- [ ] 1. 创建 growth_events 表模型

  **What to do**:
  - 创建 `app/backend/models/growth_event.py`
  - 定义 GrowthEvent 模型，字段包括：
    - id (UUID)
    - user_id
    - event_type (profile_updated/skill_added/skill_level_changed/jd_diagnosed/target_created/target_status_changed/reflection_added/project_added/resume_uploaded)
    - entity_type (profile/skill/jd/target/reflection/project)
    - entity_id
    - payload_json (Text)
    - source (user主动/对话识别/简历提取/系统产出)
    - created_at
  - 在 `app/backend/models/__init__.py` 中导入

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Tasks 4, 5, 6, 7, 8
  - **Blocked By**: None

  **References**:
  - `app/backend/models/skill_record.py` — 参考现有模型结构
  - `app/backend/db/base.py` — Base 声明

  **Acceptance Criteria**:
  - [ ] 文件创建成功
  - [ ] 模型字段完整
  - [ ] `__init__.py` 中正确导入

  **QA Scenarios**:

  ```
  Scenario: 模型创建成功
    Tool: Bash (python)
    Steps:
      1. python -c "from app.backend.models.growth_event import GrowthEvent; print('OK')"
    Expected Result: 输出 "OK"
    Evidence: .sisyphus/evidence/task-1-model-import.txt
  ```

  **Commit**: YES
  - Message: `feat(models): add growth_events table for Cognee integration`
  - Files: `app/backend/models/growth_event.py`, `app/backend/models/__init__.py`

---

- [ ] 2. 创建 cognee_client.py

  **What to do**:
  - 创建 `app/backend/agent/cognee_client.py`
  - 显式配置 Cognee 存储路径到 `~/.careeros/`：
    - GRAPH_DATABASE_PATH = ~/.careeros/kuzu
    - VECTOR_DATABASE_PATH = ~/.careeros/lancedb
  - 实现 init_cognee() 函数
  - 实现 get_cognee_status() 函数
  - 添加错误处理和日志

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Tasks 4, 5
  - **Blocked By**: None

  **References**:
  - `app/backend/agent/mem0_client.py` — 参考初始化模式
  - `app/backend/config.py` — USER_DATA_DIR 定义
  - 架构文档 `_bmad-output/planning-artifacts/architecture.md` 第 2 节

  **Acceptance Criteria**:
  - [ ] 文件创建成功
  - [ ] 存储路径配置正确
  - [ ] init_cognee() 可调用
  - [ ] 错误处理完善

  **QA Scenarios**:

  ```
  Scenario: Cognee 初始化
    Tool: Bash (python)
    Steps:
      1. python -c "from app.backend.agent.cognee_client import init_cognee; print(init_cognee())"
    Expected Result: 输出状态（ready 或 no_api_key）
    Evidence: .sisyphus/evidence/task-2-cognee-init.txt
  ```

  **Commit**: YES
  - Message: `feat(agent): add cognee_client with Kuzu+LanceDB config`
  - Files: `app/backend/agent/cognee_client.py`

---

- [ ] 3. 更新 requirements.txt

  **What to do**:
  - 添加依赖：
    - cognee
    - kuzu
    - lancedb
  - 删除依赖：
    - mem0ai
    - chromadb
  - 运行 `pip install -r requirements.txt` 验证

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Task 11
  - **Blocked By**: None

  **References**:
  - `requirements.txt` — 当前依赖列表
  - 架构文档第 6 节 — 依赖评估

  **Acceptance Criteria**:
  - [ ] cognee/kuzu/lancedb 已添加
  - [ ] mem0ai/chromadb 已删除
  - [ ] `pip install` 成功

  **QA Scenarios**:

  ```
  Scenario: 依赖安装成功
    Tool: Bash
    Steps:
      1. pip install -r requirements.txt
      2. python -c "import cognee; import kuzu; import lancedb; print('OK')"
    Expected Result: 输出 "OK"
    Evidence: .sisyphus/evidence/task-3-deps-install.txt
  ```

  **Commit**: YES
  - Message: `deps: add cognee/kuzu/lancedb, remove mem0ai/chromadb`
  - Files: `requirements.txt`

---

- [ ] 4. 创建 cognee_service.py

  **What to do**:
  - 创建 `app/backend/services/cognee_service.py`
  - 实现核心函数：
    - `remember(user_id, content, metadata)` — 记忆
    - `recall(user_id, query)` — 检索
    - `improve(user_id, feedback)` — 改进
    - `forget(user_id, content)` — 遗忘
    - `rebuild_from_sqlite(user_id)` — 从 SQLite 重建
  - 使用 cognee.recall() 而不是 cognee.search()
  - 添加降级模式：Cognee 故障时查 SQLite

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6)
  - **Blocks**: Tasks 7, 9
  - **Blocked By**: Tasks 1, 2

  **References**:
  - 架构文档第 4 节 — 集成方案
  - Cognee 官方文档：remember/recall/improve/forget API

  **Acceptance Criteria**:
  - [ ] 所有核心函数实现
  - [ ] 使用 v1 API（remember/recall）
  - [ ] 降级模式实现
  - [ ] 错误处理完善

  **QA Scenarios**:

  ```
  Scenario: remember 函数可用
    Tool: Bash (python)
    Steps:
      1. python -c "
         import asyncio
         from app.backend.services.cognee_service import remember
         asyncio.run(remember('test_user', '测试记忆'))
         print('OK')
         "
    Expected Result: 输出 "OK"
    Evidence: .sisyphus/evidence/task-4-remember.txt

  Scenario: recall 函数可用
    Tool: Bash (python)
    Steps:
      1. python -c "
         import asyncio
         from app.backend.services.cognee_service import recall
         result = asyncio.run(recall('test_user', '测试查询'))
         print(f'Result: {result}')
         "
    Expected Result: 返回查询结果（可能为空）
    Evidence: .sisyphus/evidence/task-4-recall.txt
  ```

  **Commit**: YES
  - Message: `feat(services): add cognee_service with remember/recall API`
  - Files: `app/backend/services/cognee_service.py`

---

- [ ] 5. 创建 cognee_projector.py

  **What to do**:
  - 创建 `app/backend/services/cognee_projector.py`
  - 实现 SQLite → Cognee 投影逻辑：
    - `project_event(event)` — 单个事件投影
    - `project_all_events(user_id)` — 全量重放重建
  - 监听 growth_events 表
  - 转换事件为 Cognee 可接收的数据
  - 更新 Student/Skill/Milestone 图谱实体

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 6)
  - **Blocks**: Tasks 7, 8
  - **Blocked By**: Tasks 1, 4

  **References**:
  - 架构文档第 3.2 节 — growth_events 表
  - 架构文档第 4.2 节 — 事件驱动写入

  **Acceptance Criteria**:
  - [ ] project_event() 实现
  - [ ] project_all_events() 实现
  - [ ] 支持全量重放重建
  - [ ] 错误处理完善

  **QA Scenarios**:

  ```
  Scenario: 事件投影成功
    Tool: Bash (python)
    Steps:
      1. python -c "
         import asyncio
         from app.backend.services.cognee_projector import project_event
         from app.backend.models.growth_event import GrowthEvent
         event = GrowthEvent(
             user_id='test_user',
             event_type='skill_added',
             entity_type='skill',
             entity_id='python',
             payload_json='{\"skill\": \"Python\", \"level\": \"mastered\"}',
             source='user主动'
         )
         asyncio.run(project_event(event))
         print('OK')
         "
    Expected Result: 输出 "OK"
    Evidence: .sisyphus/evidence/task-5-project-event.txt
  ```

  **Commit**: YES
  - Message: `feat(services): add cognee_projector for SQLite→Cognee projection`
  - Files: `app/backend/services/cognee_projector.py`

---

- [ ] 6. 更新 main.py

  **What to do**:
  - 移除 Mem0 初始化代码
  - 添加 growth_events 表创建（在 lifespan 中）
  - 添加 Cognee 初始化（可选，失败不阻塞启动）
  - 更新日志输出

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5)
  - **Blocks**: Task 11
  - **Blocked By**: Task 1

  **References**:
  - `app/backend/main.py` — 当前实现
  - 架构文档第 5 节 — 迁移计划

  **Acceptance Criteria**:
  - [ ] Mem0 初始化代码移除
  - [ ] growth_events 表创建
  - [ ] Cognee 初始化（可选）
  - [ ] 应用正常启动

  **QA Scenarios**:

  ```
  Scenario: 应用启动成功
    Tool: Bash
    Steps:
      1. python -m uvicorn app.backend.main:app --port 8001 &
      2. sleep 3
      3. curl http://localhost:8001/api/health
    Expected Result: 返回 {"status": "ok"}
    Evidence: .sisyphus/evidence/task-6-app-start.txt
  ```

  **Commit**: YES
  - Message: `refactor(main): remove Mem0 init, add growth_events table creation`
  - Files: `app/backend/main.py`

---

- [ ] 7. 更新 chat_service.py

  **What to do**:
  - 移除 `_extract_memory_bg` 函数
  - 移除 Mem0 相关导入
  - 添加 Cognee 检索（对话前加载成长轨迹上下文）
  - 保留滚动摘要功能

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 9, 10)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 4, 5

  **References**:
  - `app/backend/services/chat_service.py` — 当前实现
  - 架构文档第 4.3 节 — 对话前检索

  **Acceptance Criteria**:
  - [ ] _extract_memory_bg 移除
  - [ ] Mem0 导入移除
  - [ ] Cognee 检索添加
  - [ ] 对话功能正常

  **QA Scenarios**:

  ```
  Scenario: 对话功能正常
    Tool: Bash (curl)
    Steps:
      1. curl -X POST http://localhost:8001/api/chat -H "Content-Type: application/json" -d '{"message": "你好"}'
    Expected Result: 返回 SSE 流式响应
    Evidence: .sisyphus/evidence/task-7-chat.txt
  ```

  **Commit**: YES
  - Message: `refactor(chat): replace Mem0 with Cognee retrieval`
  - Files: `app/backend/services/chat_service.py`

---

- [ ] 8. 更新 profile_service.py

  **What to do**:
  - 画像更新时写入 growth_events 表
  - 事件类型：profile_updated
  - 异步投影到 Cognee

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 7, 9, 10)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 1, 5

  **References**:
  - `app/backend/services/profile_service.py` — 当前实现
  - 架构文档第 4.2 节 — 事件驱动写入

  **Acceptance Criteria**:
  - [ ] 画像更新时写入 growth_events
  - [ ] 异步投影到 Cognee
  - [ ] 不阻塞主流程

  **QA Scenarios**:

  ```
  Scenario: 画像更新触发事件
    Tool: Bash (curl)
    Steps:
      1. curl -X PATCH http://localhost:8001/api/profile/me -H "Content-Type: application/json" -d '{"school_name": "测试大学"}'
      2. curl http://localhost:8001/api/memory/stats
    Expected Result: 记忆数量增加
    Evidence: .sisyphus/evidence/task-8-profile-event.txt
  ```

  **Commit**: YES
  - Message: `feat(profile): add growth_events on profile update`
  - Files: `app/backend/services/profile_service.py`

---

- [ ] 9. 更新 routers/memory.py

  **What to do**:
  - 重写为 Cognee 接口
  - 实现端点：
    - GET /api/memory/stats — 记忆统计
    - GET /api/memory/list — 记忆列表
    - POST /api/memory/reset — 重置记忆
  - 使用 cognee_service 的函数
  - 保留 Pydantic 模型（更新字段）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 7, 8, 10)
  - **Blocks**: Task 10
  - **Blocked By**: Task 4

  **References**:
  - `app/backend/routers/memory.py` — 当前实现
  - 架构文档第 4 节 — 集成方案

  **Acceptance Criteria**:
  - [ ] 所有端点实现
  - [ ] 使用 cognee_service
  - [ ] 错误处理完善
  - [ ] 前端可调用

  **QA Scenarios**:

  ```
  Scenario: 记忆统计端点
    Tool: Bash (curl)
    Steps:
      1. curl http://localhost:8001/api/memory/stats
    Expected Result: 返回 {"status": "ready", "count": N}
    Evidence: .sisyphus/evidence/task-9-memory-stats.txt

  Scenario: 记忆列表端点
    Tool: Bash (curl)
    Steps:
      1. curl http://localhost:8001/api/memory/list
    Expected Result: 返回记忆列表
    Evidence: .sisyphus/evidence/task-9-memory-list.txt
  ```

  **Commit**: YES
  - Message: `refactor(memory): rewrite to Cognee interface`
  - Files: `app/backend/routers/memory.py`

---

- [ ] 10. 删除 mem0_client.py

  **What to do**:
  - 删除 `app/backend/agent/mem0_client.py`
  - 检查并移除所有对它的引用
  - 更新导入

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (after Tasks 7, 8, 9)
  - **Blocks**: Task 11
  - **Blocked By**: Tasks 7, 8, 9

  **References**:
  - `app/backend/agent/mem0_client.py` — 要删除的文件
  - 全局搜索 "mem0_client" 确保无遗漏

  **Acceptance Criteria**:
  - [ ] 文件删除
  - [ ] 无残留引用
  - [ ] 应用正常启动

  **QA Scenarios**:

  ```
  Scenario: 无残留引用
    Tool: Grep
    Steps:
      1. grep -r "mem0_client" app/backend/
    Expected Result: 无匹配
    Evidence: .sisyphus/evidence/task-10-no-refs.txt
  ```

  **Commit**: YES
  - Message: `chore: remove mem0_client.py`
  - Files: `app/backend/agent/mem0_client.py`

---

- [ ] 11. 运行测试 + 修复问题

  **What to do**:
  - 运行 `pytest` 确保所有测试通过
  - 运行 `ruff check .` 确保无错误
  - 修复任何问题
  - 更新测试用例（如果需要）

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
  - `pyproject.toml` — pytest 配置

  **Acceptance Criteria**:
  - [ ] `pytest` 全部通过
  - [ ] `ruff check .` 无错误
  - [ ] 应用正常启动

  **QA Scenarios**:

  ```
  Scenario: 测试全部通过
    Tool: Bash
    Steps:
      1. pytest
    Expected Result: 所有测试通过
    Evidence: .sisyphus/evidence/task-11-pytest.txt

  Scenario: Lint 无错误
    Tool: Bash
    Steps:
      1. ruff check .
    Expected Result: 无错误
    Evidence: .sisyphus/evidence/task-11-ruff.txt
  ```

  **Commit**: YES
  - Message: `test: fix all tests for Cognee integration`
  - Files: 测试文件（如有修改）

---

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** — `oracle`
  验证所有 Must Have 条件满足，所有 Must NOT Have 条件不满足。

- [ ] F2. **Code Quality Review** — `unspecified-high`
  运行 `ruff check .` + `pytest`，检查代码质量。

- [ ] F3. **Integration Test** — `unspecified-high`
  端到端测试：对话 → 成长事件 → Cognee 投影 → 检索注入。

- [ ] F4. **Scope Fidelity Check** — `deep`
  验证实现与架构文档一致。

---

## Commit Strategy

- **Task 1-3**: 基础层提交
- **Task 4-6**: 服务层提交
- **Task 7-10**: 集成层提交
- **Task 11**: 测试修复提交

---

## Success Criteria

### 验证命令

```bash
pytest  # Expected: all tests pass
ruff check .  # Expected: no errors
python -m uvicorn app.backend.main:app  # Expected: app starts
```

### 最终检查

- [ ] Cognee 集成完成
- [ ] Mem0 完全移除
- [ ] 所有测试通过
- [ ] 应用正常运行
