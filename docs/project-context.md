# CareerOS Project Context

> AI Agent 实现代码时必须遵循的关键规则和模式。
> 聚焦于容易被忽略的细节，优化 LLM 上下文效率。

---

## Technology Stack & Versions

| 类别 | 技术 | 版本 | 备注 |
|------|------|------|------|
| Python | Python | ≥3.11 | 需要 `from __future__ import annotations` |
| 后端框架 | FastAPI | ≥0.111.0 | async 风格 |
| ORM | SQLAlchemy | ≥2.0.0 | 必须使用 2.0 async 风格 |
| 数据库 | SQLite + aiosqlite | ≥0.20.0 | 单文件，零运维 |
| AI Agent | PydanticAI | ==1.89.1 | 精确版本，避免 API 变更 |
| LLM 路由 | LiteLLM | ≥1.30.0 | 多 Provider 统一抽象 |
| 记忆层 | Cognee + Kuzu + LanceDB | 1.0.5 / 0.11.3 / 0.30.2 | 可选依赖 |
| 前端框架 | React | 19.2.5 | 非 18，注意新 API |
| 构建工具 | Vite | 8.0.10 | — |
| 样式 | Tailwind CSS | 4.2.4 | v4 新语法 |
| TypeScript | TypeScript | 6.0 | strict mode |
| Linter | ruff | ==0.15.8 | 精确版本，CI 一致性 |
| 测试 | pytest + pytest-asyncio | ≥8.0.0 / ≥0.24.0 | asyncio_mode = "auto" |

---

## Critical Implementation Rules

### Language-Specific Rules (Python)

- **必须** 文件头 `from __future__ import annotations`
- **必须** 类型提示：`def func(db: AsyncSession, user_id: str) -> User | None:`
- **必须** async DB 调用：`await db.execute(select(...))`，禁止同步 `db.query()`
- **必须** 日志用 `logging.getLogger(__name__)`，禁止 `print`
- **必须** 异常链：`raise ... from e`，保留上下文
- **必须** 字符串格式化用 `%s`：`logger.info("msg: %s", val)`，禁止 f-string in logger
- **禁止** 裸 `except:`，必须指定异常类型
- **禁止** `@ts-ignore` / `as any` 类型压制

### Language-Specific Rules (TypeScript)

- **必须** 显式类型：`useState<Type>(...)`，禁止隐式 `any`
- **必须** 接口定义在 `lib/api.ts`，页面内禁止内联复杂类型
- **必须** 组件 Props 用 `interface`，不用 `type`
- **禁止** `any` 类型，用 `unknown` + 类型守卫

### Framework-Specific Rules (FastAPI)

- **必须** 路由函数 `async def`，禁止同步
- **必须** 返回 Pydantic 模型，禁止裸 `dict`
- **必须** 查询参数有默认值：`user_id: str = Query("demo_user")`
- **必须** 依赖注入：`db: AsyncSession = Depends(get_db)`
- **禁止** 在路由函数中直接写业务逻辑，调用 service

### Framework-Specific Rules (SQLAlchemy 2.0)

- **必须** 模型用 `Mapped[...]` + `mapped_column()`
- **必须** 查询用 `select(Model).where(...)`，禁止 `db.query()`
- **必须** 结果用 `result.scalar_one_or_none()` 或 `result.scalars().all()`
- **必须** 事务用 `await db.commit()` 或 `get_db` 自动 commit
- **禁止** 同步调用，所有 DB 操作必须 `await`

### Framework-Specific Rules (PydanticAI)

- **必须** 工具用 `@agent.tool` 装饰器注册
- **必须** 工具函数签名：`async def tool(ctx: RunContext[Deps], ...) -> str:`
- **必须** 工具 docstring 清晰描述用途和参数
- **必须** 动态 prompt 用 `@agent.system_prompt`
- **禁止** 在工具中直接操作 DB，调用 service

### Framework-Specific Rules (React)

- **必须** 页面组件用 `export default function PageName()`
- **必须** 状态管理用 `useChatSession()` Context，禁止 prop drilling
- **必须** API 调用用 `lib/api.ts`，禁止组件内 fetch
- **必须** 样式用 Tailwind CSS，禁止内联 style
- **禁止** Class 组件，必须函数组件 + Hooks

### Testing Rules

- **必须** 测试文件在 `tests/` 目录，命名 `test_*.py`
- **必须** 测试函数 `async def test_...()`，用 `asyncio_mode = "auto"`
- **必须** Fixtures 在 `tests/conftest.py`
- **必须** 测试数据库用内存 SQLite：`sqlite+aiosqlite:///:memory:`
- **必须** 使用 `httpx.AsyncClient` 测试 API
- **禁止** 测试依赖外部服务，用 `AsyncMock`
- **禁止** 测试间共享状态，每个测试独立

### Code Quality & Style Rules

- **Linter**: ruff ==0.15.8，配置在 `pyproject.toml`
- **行宽**: 120 字符（由 formatter 处理，lint 忽略 E501）
- **导入排序**: isort via ruff，`known-first-party = ["app"]`
- **文件命名**: snake_case（Python），PascalCase（React 组件）
- **类命名**: PascalCase
- **函数命名**: snake_case，私有函数 `_` 前缀
- **常量命名**: UPPER_SNAKE_CASE
- **必须** 模块级 docstring
- **必须** 公共函数 docstring
- **禁止** 明显代码注释（代码自解释）

### Development Workflow Rules

- **提交格式**: `<type>: <description>`（type: feat/fix/refactor/docs/test/chore）
- **分支命名**: `feat/xxx`, `fix/xxx`
- **CI**: GitHub Actions，push/PR 触发
- **后端 CI**: ruff check → ruff format → pytest → build
- **前端 CI**: npm install → npm run build
- **禁止** 直接 push 到 main/master
- **禁止** 跳过 CI 检查

### Critical Don't-Miss Rules

**绝对禁止**:
- `db.query()` — SQLAlchemy 1.x 同步，用 `await db.execute(select(...))`
- `print()` — 用 `logger.debug()`
- `as any` / `@ts-ignore` — 修复类型错误
- 裸 `except:` — 用 `except Exception:`
- 同步 DB 操作 — 全部 `async/await`
- 组件内 fetch — 用 `lib/api.ts`
- prop drilling — 用 Context

**边界情况处理**:
- Cognee 不可用 → 降级 SQLite 查询
- LLM 调用失败 → 返回错误消息，不崩溃
- 空查询 → 返回提示，不搜索全部
- 重复事件 → UNIQUE 约束 + IntegrityError
- 投影失败 → 记录日志，不阻塞写入

**安全规则**:
- 禁止硬编码 API Key
- `.env` 不提交 git
- user_id 输入验证
- SQL 参数化（SQLAlchemy 自动）

---

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Usage Guidelines

**For AI Agents:**
- Read this file before implementing any code
- Follow ALL rules exactly as documented
- When in doubt, prefer the more restrictive option
- Update this file if new patterns emerge

**For Humans:**
- Keep this file lean and focused on agent needs
- Update when technology stack changes
- Review quarterly for outdated rules
- Remove rules that become obvious over time

---

---

## 新增规则（2026-05-06）

### Memory System Rules

- **必须** Agent 写入走 `memory_save(entity_type, section, content)` 或 `update_profile(14 params)`，禁止直接操作 `.md` 文件
- **必须** 写入后 `deps.pending_event_ids` 会累积事件 ID，需在 `finally` 块中调用 `sync_projections()`
- **必须** `CareerOSDeps` 含 `pending_event_ids: list[str]`，用于追踪本轮 Agent 调用产生的记忆事件
- **必须** FTS5 由 `lifespan` 自动建表 + 触发器，代码中不可手动 DROP/CREATE（DELETE 端点除外）
- **必须** 记忆去重用 `dedupe_key = {event_type}:{entity_type}:{entity_id}` 的 UNIQUE 约束，冲突时返回 `None`
- **必须** 后台审查失败不能影响用户，用 `logger.exception` 记录
- **禁止** 用 Cognee 相关 API（当前未接入，所有搜索走 FTS5 或 `.md` 文件）
- **禁止** `db.delete()` 配合 FTS5 触发器使用（SQLite 3.45.3 bug），DELETE 端点在删除前先 DROP TRIGGER

### PydanticAI Rules

- **必须** `end_strategy` 只接受 `"graceful"` 或 `"exhaustive"`，无 `"early"`
- **必须** 工具调用后 `deps.pending_event_ids` 不为空时触发 `sync_projections()`
- **必须** 动态 context 用 `@agent.system_prompt` 注入（用户消息保持干净），禁止拼接到 `user_input`
- **必须** 工具函数 docstring 用中文+英文说明触发条件和示例，避免模型不理解何时调用
- **禁止** 工具参数用 `dict[str, Any]`（PydanticAI 无法正确序列化），必须拆成显式参数
- **禁止** `run_stream()` 之后手动拼接工具结果（PydanticAI 自动处理 `tool_calls`）

### DeepSeek 兼容规则

- **必须** `base_url` 为 `https://api.deepseek.com`（不加 `/v1`，PydanticAI 和 liteLLM 各自补全）
- **必须** 模型名与 provider 都支持在 Settings 页切换，不硬编码
- **必须** 存 API Key 时去首尾空格

### Background Review Rules

- **必须** 仅在 `deps.pending_event_ids` 为空（Agent 本轮没调工具）时触发
- **必须** `asyncio.create_task` 搭配 `task.add_done_callback(_log_task_error)`
- **必须** 使用独立 `db session`（`get_async_session_maker`），不共享主请求的 session
- **必须** 审查 Agent 与主 Agent 同模型、同配置（复用 `get_agent()`）
- **必须** 审查失败只打日志，不抛出异常到用户
- **禁止** 在审查 Agent 中启用新的 nudge 或审查（防止递归）

---

Last Updated: 2026-05-06
