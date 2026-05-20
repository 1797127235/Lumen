# Story：Skill 动态加载 — `skill_load` 工具

## 背景

Skill v2 的激活机制有两个问题：

1. **`always: true` 全量注入**：每轮把完整 skill 正文塞进 system prompt，无论对话内容是否相关，白白消耗 token。
2. **`$skill_name` 依赖用户手动写**：用户不知道有哪些 skill，也不会写语法。

参考 Claude Code 的做法：
- **Skill 描述（description）**始终注入 context，作为模型的路由信号
- **Skill 正文**懒加载——模型判断需要时调用 `skill_load` 工具获取，当轮即生效
- 这样大多数对话零 skill token 消耗，命中时才付费

## 变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `lib/tools/skill_load.py` | 新建 | `skill_load` 工具实现 |
| `lib/tools/factory.py` | 修改 | 注册 `create_skill_tools()` |
| `core/agent.py` | 修改 | `_skills_prompt` 只保留 XML 目录，删除正文注入逻辑 |
| `lib/skills/builtins/emotional-companion/SKILL.md` | 修改 | `always: false`，精简 description |

---

## 任务 1 — 新建 `lib/tools/skill_load.py`

```python
"""skill_load 工具 — Agent 按需加载 Skill 正文。"""

from __future__ import annotations

from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error
from shared.logging import get_logger

logger = get_logger(__name__)


async def _load(args: dict[str, Any], deps) -> str:
    name = args.get("skill_name", "").strip()
    if not name:
        return tool_error("请提供 skill_name")

    from lib.skills import get_skills_loader

    loader = get_skills_loader()
    content = loader.load_skill(name)
    if content is None:
        available = [s["name"] for s in loader.list_skills(filter_unavailable=True)]
        hint = "、".join(available) if available else "（暂无可用技能）"
        return tool_error(f"Skill '{name}' 不存在。可用技能：{hint}")

    body = loader._strip_frontmatter(content)
    if not body:
        return tool_error(f"Skill '{name}' 正文为空")

    logger.info("skill 已加载", skill=name)
    return body


def create_skill_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="skill_load",
            description=(
                "加载指定技能的完整指令内容。"
                "当技能目录（可用技能目录）中某个技能与当前对话相关时，"
                "调用此工具获取完整指令并立即应用。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "技能名称，与目录中的 <name> 一致",
                    }
                },
                "required": ["skill_name"],
            },
            read_only=True,
            execute=_load,
            meta=ToolMeta(
                risk="read-only",
                always_on=True,
                search_hint="技能 skill 加载 激活",
                tags=["skill"],
            ),
        )
    ]
```

---

## 任务 2 — 修改 `lib/tools/factory.py`

在 `register_all_tools()` 的 `all_tools` 列表中追加 `*create_skill_tools()`，并在文件顶部添加 import。

### import（加在现有 import 块末尾）

```python
from lib.tools.skill_load import create_skill_tools
```

### `all_tools` 列表（加一行）

```python
    all_tools: list[ToolDef] = [
        *create_file_tools(),
        *create_memory_tools(),
        *create_profile_tools(),
        *create_notes_tools(),
        *create_web_search_tools(),
        *create_shell_tools(),
        *create_skill_tools(),   # ← 新增
        create_tool_search(),
    ]
```

---

## 任务 3 — 修改 `core/agent.py`

将 `_skills_prompt` 函数简化为**只输出 XML 目录**，删除 `always`/`detected`/`active_names` 相关逻辑。

### 改前（当前实现）

```python
@agent.system_prompt
async def _skills_prompt(ctx: RunContext[LumenDeps]) -> str:
    from lib.skills import get_skills_loader

    loader = get_skills_loader()

    parts: list[str] = []

    summary = loader.build_skills_summary()
    if summary:
        parts.append(f"## 可用技能目录\n\n{summary}")

    always_names = loader.get_always_skills()
    detected_names = loader.detect_skills(ctx.deps.current_user_input or "")
    active_names = list(dict.fromkeys([*always_names, *detected_names]))

    if active_names:
        content = loader.load_skills_for_context(active_names)
        if content:
            parts.append(f"# Active Skills\n\n{content}")

    return "\n\n".join(parts)
```

### 改后

```python
@agent.system_prompt
async def _skills_prompt(ctx: RunContext[LumenDeps]) -> str:
    from lib.skills import get_skills_loader

    loader = get_skills_loader()
    summary = loader.build_skills_summary()
    if summary:
        return f"## 可用技能目录\n\n{summary}"
    return ""
```

`ctx` 参数保留（PydanticAI 装饰器要求签名），但不再读取 `ctx.deps`。

---

## 任务 4 — 修改 `emotional-companion/SKILL.md`

### 改动点

1. `metadata.always` 从 `true` 改为 `false`
2. `description` 精简为一句话路由信号（模型用它判断是否调用 `skill_load`）

### 改后 frontmatter

```yaml
---
name: emotional-companion
description: 情感支持与引导。当用户表现出焦虑、迷茫、低落、压力大、烦躁、难受、崩溃等情绪时加载此技能。
metadata:
  always: false
  requires:
    env: []
---
```

正文内容不变。

---

## 验证方式

```bash
# 1. 工具注册
python -c "
from lib.tools.factory import register_all_tools
reg = register_all_tools()
print('skill_load' in reg.get_registered_names())   # True
print(reg.get_tool('skill_load').meta.always_on)     # True
"

# 2. 工具执行
python -c "
import asyncio
from lib.tools.skill_load import _load

class FakeDeps: pass

async def test():
    result = await _load({'skill_name': 'emotional-companion'}, FakeDeps())
    print(result[:200])   # 应输出 skill 正文前 200 字

asyncio.run(test())
"

# 3. 工具找不到时的错误提示
python -c "
import asyncio
from lib.tools.skill_load import _load

class FakeDeps: pass

async def test():
    result = await _load({'skill_name': 'nonexistent'}, FakeDeps())
    print(result)   # 应输出 [工具错误] Skill 'nonexistent' 不存在...

asyncio.run(test())
"

# 4. system prompt 不再包含 skill 正文（只有 XML 目录）
# 启动后发一条对话，在后端日志中搜索 system prompt，确认只含 <skills> 标签，无 '# 情感伴侣 Skill' 字样
```

---

## 注意事项

- `_strip_frontmatter` 是 `SkillsLoader` 的实例方法，`_load` 里通过 `loader._strip_frontmatter(content)` 调用，不要直接 import
- `always_on=True` 确保 `skill_load` 出现在每轮工具列表里，不需要用户先 `tool_search` 解锁
- `SkillsLoader.get_always_skills()` / `detect_skills()` 方法保留不删，未来可能复用
