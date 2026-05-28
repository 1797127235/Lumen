# CLI TUI 斜杠命令支持 - 技术方案文档

## 1. 问题概述

### 1.1 当前状态
- **CLI TUI**：期望通过 `sync.data.command` 获取可用斜杠命令列表，但目前该数组为空
- **斜杠命令检测失败**：`prompt/index.tsx` 第 1167-1174 行的检测逻辑永远返回 `false`
- **Lumen 后端**：
  - ✅ 技能系统：使用 `$skill-name` 格式（`lib/skills/builtins/`）
  - ✅ 会话管理 API：`/api/chat*` 端点完整
  - ❌ 无斜杠命令解析器
  - ❌ 无技能列表 API

### 1.2 根本原因
1. **TUI 期望**：从后端获取 `sync.data.command` 列表，包含所有可用命令
2. **命令格式不匹配**：TUI 期待 `/command` 格式，后端使用 `$skill-name` 格式
3. **会话管理命令缺失**：`/resume`、`/delete`、`/rename` 等命令未实现
4. **SDK command 通道是 no-op**：`channels/cli/cmd/tui/context/sdk.tsx` 第 118 行 `session.command()` 实现为空（"slash commands not supported in Lumen"），因此斜杠命令**不能**走 SDK 通道，必须走独立 REST 端点

---

## 2. 设计原则

### 2.1 架构原则
- **职责清晰**：TUI 负责 UI 层，后端负责业务逻辑层
- **单一真相源**：`/api/commands` 是命令列表的唯一来源
- **最小侵入**：复用 Lumen 现有架构（会话 API、技能系统）

### 2.2 技术原则
- **向后兼容**：保持现有 `$skill-name` 格式继续工作
- **OpenCode 一致性**：遵循 OpenCode 的命令模式（`sync.data.command` → 后端执行）
- **渐进式扩展**：支持未来命令注册机制

---

## 3. 技术方案

### 3.1 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                      CLI TUI (UI 层)                      │
├─────────────────────────────────────────────────────────┤
│  1. 从 /api/commands 获取命令列表 → sync.data.command    │
│  2. 斜杠命令检测 → 调用 /api/commands/execute             │
│  3. 根据返回 action 执行对应操作                         │
└─────────────────────────────────────────────────────────┘
                           │ HTTP
                           ↓
┌─────────────────────────────────────────────────────────┐
│                  Lumen 后端 (业务层)                    │
├─────────────────────────────────────────────────────────┤
│  GET /api/commands/list  → 返回所有可用命令             │
│  POST /api/commands/execute → 执行命令并返回 action       │
│                                                         │
│  命令执行逻辑：                                            │
│  • /new      → ensure_conversation()                      │
│  • /resume  → 返回 switch action + session_id           │
│  • /delete  → 返回 delete action + session_id           │
│  │
│  • /skill   → 转换为 $skill-name 格式，返回 skill action   │
└─────────────────────────────────────────────────────────┘
```

### 3.2 命令分类

#### 3.2.1 会话管理命令
| 命令 | 参数 | Action | 后端实现 |
|------|------|--------|----------|
| `/new` | 无 | - | `ensure_conversation()` |
| `/resume` | `<session_id>` | `switch` | 直接返回 session_id |
| `/delete` | `<session_id>` | `delete` | 调用删除 API |
| `/rename` | `<session_id> <title>` | `rename` | 调用更新 API |
| `/exit` | 无 | `exit` | 特殊处理 |
| `/quit` | 无 | `exit` | 特殊处理 |
| `/help` | 无 | `help` | 返回帮助文本 |

#### 3.2.2 技能命令
| 命令 | 参数 | Action | 转换 |
|------|------|--------|------|
| `/skill-name` | 可选参数 | `skill` | `$skill-name` + 参数 |

### 3.3 Action 返回值格式

```typescript
type CommandResponse =
  | { ok: true; action: "switch"; session_id: string }
  | { ok: true; action: "delete"; session_id: string }
  | { ok: true; action: "rename"; session_id: string; title: string }
  | { ok: true; action: "skill"; text: string }  // 已转换为 $skill-name
  | { ok: true; action: "help"; response: string }
  | { ok: true; action: "exit" }
  | { ok: false; error: string }  // 命令执行失败
```

---

## 4. 实现细节

### 4.1 后端实现

#### 4.1.1 新建 `server/routes/commands.py`

```python
"""
斜杠命令 API — TUI 可用命令列表和执行
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.skills.loader import get_skills_loader
from core.db import get_db
from lib.chat.session import ensure_conversation
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/commands", tags=["commands"])


# ─── 请求/响应模型 ────────────────────────────────────────


class CommandItem(BaseModel):
    """命令项定义"""
    name: str
    description: str
    arg_required: bool = False


class CommandExecuteRequest(BaseModel):
    """命令执行请求"""
    command: str
    arguments: str
    session_id: str | None = None


class CommandExecuteResponse(BaseModel):
    """命令执行响应"""
    ok: bool
    action: str | None = None
    session_id: str | None = None
    title: str | None = None
    text: str | None = None
    response: str | None = None
    error: str | None = None


# ─── API 端点 ─────────────────────────────────────────────────────


@router.get("/list", response_model=list[CommandItem])
async def list_commands():
    """
    返回 TUI 可用的斜杠命令列表
    
    包括：
    - 会话管理命令
    - 技能命令（从 $skill-name 转换为 /skill_name）
    """
    loader = get_skills_loader()
    skills = loader.list_skills(filter_unavailable=True)
    
    # 技能命令：$skill-name → /skill_name
    skill_commands = [
        CommandItem(
            name=f"/{s['name']}",
            description=f"加载技能：{s['description']}",
            arg_required=False,
        )
        for s in skills
    ]
    
    # 会话管理命令
    session_commands = [
        CommandItem(name="/new", description="创建新会话", arg_required=False),
        CommandItem(name="/resume", description="恢复指定会话", arg_required=True),
        CommandItem(name="/delete", description="删除会话", arg_required=True),
        CommandItem(name="/rename", description="重命名会话", arg_required=True),
        CommandItem(name="/exit", description="退出 TUI"),
        CommandItem(name="/quit", description="退出 TUI（exit 别名）"),
        CommandItem(name="/help", description="显示帮助信息", arg_required=False),
    ]
    
    return session_commands + skill_commands


@router.post("/execute", response_model=CommandExecuteResponse)
async def execute_command(req: CommandExecuteRequest, db: AsyncSession = Depends(get_db)):
    """
    执行斜杠命令
    
    流程：
    1. 解析命令和参数
    2. 执行对应逻辑
    3. 返回 action 和相关数据
    """
    cmd = req.command
    args = req.arguments or ""
    session_id = req.session_id
    
    logger.info("执行命令", command=cmd, args=args, session_id=session_id)
    
    # ── 会话管理命令 ─────────────────────────────────────────────
    
    if cmd == "new":
        # ensure_conversation(db, user_id, conversation_id, user_input)
        result = await ensure_conversation(db, "demo_user", None, "")
        if isinstance(result, str):
            return CommandExecuteResponse(ok=False, error=result)
        await db.commit()
        return CommandExecuteResponse(
            ok=True,
            action="switch",
            session_id=result.conversation_id,
        )
    
    if cmd == "exit" or cmd == "quit":
        return CommandExecuteResponse(ok=True, action="exit")
    
    if cmd == "help":
        commands = await list_commands()
        help_text = "## 可用命令\n\n" + "\n".join(
            f"**{c.name}** - {c.description}"
            + (" `<arg_required>`" if c.arg_required else "")
            for c in commands
        )
        return CommandExecuteResponse(
            ok=True,
            action="help",
            response=help_text,
        )
    
    # 需要会话 ID 的命令
    if cmd in ("resume", "delete", "rename"):
        if not args:
            return CommandExecuteResponse(
                ok=False,
                error=f"命令 /{cmd} 需要参数：<session_id> [args...]"
            )
        
        # 解析第一个参数作为 session_id
        first_arg_end = args.find(" ") if " " in args else len(args)
        session_id_arg = args[:first_arg_end].strip()
        remaining_args = args[first_arg_end + 1:] if first_arg_end + 1 < len(args) else ""
        
        if cmd == "resume":
            # 切换会话
            return CommandExecuteResponse(
                ok=True,
                action="switch",
                session_id=session_id_arg,
            )
        
        if cmd == "delete":
            # 删除会话（返回 action，由 TUI 调用 API）
            return CommandExecuteResponse(
                ok=True,
                action="delete",
                session_id=session_id_arg,
            )
        
        if cmd == "rename":
            # 重命名会话
            if not remaining_args:
                return CommandExecuteResponse(
                    ok=False,
                    error=f"命令 /rename 需要两个参数：<session_id> <title>"
                )
            return CommandExecuteResponse(
                ok=True,
                action="rename",
                session_id=session_id_arg,
                title=remaining_args.strip(),
            )
    
    # ── 技能命令 ───────────────────────────────────────────────────────
    
    loader = get_skills_loader()
    skills = {s["name"]: s for s in loader.list_skills(filter_unavailable=True)}
    
    if cmd in skills:
        # /skill_name → $skill-name
        skill_text = f"${cmd}"
        if args:
            skill_text += f" {args}"
        
        return CommandExecuteResponse(
            ok=True,
            action="skill",
            text=skill_text,
        )
    
    # ── 未知命令 ───────────────────────────────────────────────────────
    
    return CommandExecuteResponse(
        ok=False,
        error=f"未知命令：/{cmd}",
    )
```

#### 4.1.2 注册路由

修改 `main.py`（Lumen 的路由在这里统一注册，`server/routes/__init__.py` 是空文件）：

```python
# 在现有 import 列表末尾追加
from server.routes.commands import router as commands_router

# 在 app.include_router(...) 列表末尾追加
app.include_router(commands_router, prefix="/api")
```

### 4.2 TUI 适配

#### 4.2.1 添加 API 客户端

修改 `channels/cli/cmd/tui/lumen/api.ts`，添加命令相关方法：

```typescript
// 获取 TUI 可用命令列表
export async function getTUICommands(): Promise<
  Array<{ name: string; description: string; arg_required: boolean }>
> {
  const response = await fetch(`${API_BASE}/api/commands/list`)
  if (!response.ok) {
    throw new Error(`获取命令列表失败: ${response.statusText}`)
  }
  return response.json()
}

// 执行斜杠命令
export async function executeCommand(
  command: string,
  args: string,
  sessionID?: string,
): Promise<{
  ok: boolean
  action?: string
  session_id?: string
  title?: string
  text?: string
  response?: string
  error?: string
}> {
  const response = await fetch(`${API_BASE}/api/commands/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, arguments: args, session_id: sessionID }),
  })
  if (!response.ok) {
    throw new Error(`执行命令失败: ${response.statusText}`)
  }
  return response.json()
}
```

#### 4.2.2 填充命令列表

修改 `channels/cli/cmd/tui/context/sync.tsx`，在 `onMount` 中调用：

```typescript
async function refreshCommands() {
  try {
    const commands = await LumenApi.getTUICommands()
    setStore("command", commands)
  } catch (error) {
    logger.error("获取命令列表失败", error)
    // fallback: 静态命令列表
    setStore("command", [
      { name: "/new", description: "创建新会话", arg_required: false },
      { name: "/exit", description: "退出", arg_required: false },
      { name: "/quit", description: "退出", arg_required: false },
      { name: "/help", description: "显示帮助信息", arg_required: false },
    ])
  }
}

// 在 onMount 中调用
onMount(async () => {
  await refreshSessions()
  await refreshCommands()  // ← 新增
  setStatusSignal("complete")
  setReadySignal(true)
})
```

#### 4.2.3 斜杠命令处理逻辑

修改 `channels/cli/cmd/tui/component/prompt/index.tsx`，替换第 1167-1230 行的斜杠命令处理逻辑：

```typescript
    // ── 斜杠命令处理（替代原 1167-1197 行）───────────────────────
    
    } else if (inputText.startsWith("/")) {
      // 解析斜杠命令
      const firstLineEnd = inputText.indexOf("\n")
      const firstLine = firstLineEnd === -1 ? inputText : inputText.slice(0, firstLineEnd)
      const [command, ...argParts] = firstLine.split(" ")
      const args = argParts.join(" ")
      const rest = firstLineEnd === -1 ? "" : inputText.slice(firstLineEnd + 1)
      const fullArgs = args + (rest ? `\n${rest}` : "")
      
      try {
        const result = await LumenApi.executeCommand(
          command.slice(1),  // 去掉 /
          fullArgs,
          sessionID,
        )
        
        if (!result.ok) {
          toast.show({
            message: result.error || "命令执行失败",
            variant: "error",
            duration: 3000,
          })
          return false
        }
        
        // 根据 action 执行对应操作
        switch (result.action) {
          case "switch":
            // 切换会话
            if (result.session_id) {
              route.navigate({ type: "session", sessionID: result.session_id })
            }
            break
          
          case "delete":
            // 删除会话
            if (result.session_id) {
              await LumenApi.deleteConversation(result.session_id)
              await sync.session.refresh()   // refreshSessions 是 sync.tsx 私有函数，需通过 context 调用
              route.navigate({ type: "home" })
            }
            break
          
          case "rename":
            // 重命名会话
            if (result.session_id && result.title) {
              await LumenApi.renameConversation(result.session_id, result.title)
              await sync.session.refresh()   // 同上
            }
            break
          
          case "skill":
            // 技能命令：发送转换后的 $skill-name 文本
            if (result.text) {
              sdk.client.session.prompt({
                sessionID: sessionID!,
                ...selectedModel,
                messageID,
                agent: agent.name,
                model: selectedModel,
                variant,
                parts: [
                  { id: PartID.ascending(), type: "text", text: result.text },
                  ...editorParts,
                  ...nonTextParts.map(assign),
                ],
              }).catch(() => {})
            }
            break
          
          case "help":
            // 帮助命令：本地显示，不发给 AI
            if (result.response) {
              toast.show({
                message: result.response,
                variant: "info",
                duration: 8000,
              })
            }
            break
          
          case "exit":
            useExit()()
            break
          
          default:
            throw new Error(`未知 action: ${result.action}`)
        }
        
        // 清空输入
        input.clear()
        input.extmarks.clear()
        setStore("prompt", {
          input: "",
          parts: [],
        })
        setStore("extmarkToPartIndex", new Map())
        props.onSubmit?.()
        
        return true
        
      } catch (error: any) {
        logger.error("命令执行失败", error)
        toast.show({
          message: error.message || "命令执行失败",
          variant: "error",
          duration: 3000,
        })
        return false
      }
    }
```

---

## 5. 测试计划

### 5.1 后端 API 测试

```bash
# 1. 获取命令列表
curl http://localhost:8000/api/commands/list

# 2. 执行会话命令
curl -X POST http://localhost:8000/api/commands/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "new", "arguments": "", "session_id": null}'

# 3. 执行技能命令
curl -X POST http://localhost:8000/api/commands/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "emotional-partner", "arguments": "", "session_id": "xxx"}'

# 4. 测试错误命令
curl -X POST http://localhost:8000/api/commands/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "unknown", "arguments": "", "session_id": "xxx"}'
```

### 5.2 TUI 集成测试

1. **命令列表加载**
   - 启动 TUI，检查 `sync.data.command` 是否被填充
   - 验证包含 `/new`、`/exit`、`/skill-name` 等命令

2. **会话管理命令**
   - 输入 `/new` → 验证创建新会话并跳转
   - 输入 `/resume <id>` → 验证切换会话
   - 输入 `/delete <id>` → 验证删除会话

3. **技能命令**
   - 输入 `/emotional-partner` → 验证发送 `$emotional-partner` 到后端
   - 验证 Agent 正确加载技能

4. **帮助命令**
   - 输入 `/help` → 验证显示帮助信息

5. **错误处理**
   - 输入 `/unknown` → 验证显示错误提示
   - 输入 `/delete`（无参数）→ 验证参数错误提示

---

## 6. 迁移计划

### Phase 1：后端实现（1-2 天）
1. 创建 `server/routes/commands.py`
2. 在 `main.py` 注册路由（`app.include_router(commands_router, prefix="/api")`）
3. 测试 API 端点

### Phase 2：TUI 适配（1-2 天）
1. 添加 API 客户端方法
2. 修改 `sync.tsx` 填充命令列表
3. 修改 `prompt/index.tsx` 处理斜杠命令
4. 集成测试

### Phase 3：文档和优化（1 天）
1. 更新 `AGENTS.md` 添加命令使用说明
2. 添加单元测试
3. 用户体验优化

---

## 7. 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 后端命令解析逻辑复杂化 | 中 | 保持简单，只做转换和路由，不做复杂解析 |
| TUI 改动影响现有逻辑 | 低 | 只修改斜杠命令分支，不影响普通消息 |
| 技能格式转换破坏兼容性 | 无 | 后端保持 `$skill-name` 格式，TUI 做转换 |
| 会话 ID 解析错误 | 低 | 添加参数验证和错误提示 |

---

## 8. 未来扩展

### 8.1 命令注册机制
未来可以支持插件命令注册：

```python
# server/routes/commands.py

_command_registry = {
    "new": _cmd_new,
    "resume": _cmd_resume,
    # ...
}

def register_command(name: str, handler: Callable):
    """注册自定义命令"""
    _command_registry[name] = handler
```

### 8.2 命令帮助系统
扩展 `/help` 命令，支持：
- 分组显示（会话/技能/自定义）
- 详细参数说明
- 使用示例

### 8.3 命令历史
记录命令执行历史，支持上下箭头重复命令。

---

## 9. 参考资料

- OpenCode TUI 命令系统：`channels/cli/cmd/tui/component/prompt/index.tsx`
- Lumen 技能系统：`lib/skills/loader.py`
- Lumen 会话 API：`server/routes/chat.py`
- Lumen Agent：`core/agent.py`

---

**文档版本**: 1.0  
**创建日期**: 2026-01-XX  
**状态**: 待审核