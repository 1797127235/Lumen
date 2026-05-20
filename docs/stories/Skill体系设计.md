# Story：Skill 体系设计

## 背景

工具层（`lib/tools/`）已有 `ToolDef` + 工厂 + 中间件的扁平架构，扩展性良好。但缺少更高阶的"技能包"概念——一组工具 + prompt 指令 + 触发条件的打包，让 Agent 能根据对话场景自动切换工作模式。

## 目标

- Agent 能"加载技能"：动态获得额外工具和 prompt 偏移
- 用户可以开关技能：设置页面管理，无需重启
- 技能可热插拔：激活/停用不重建 Agent
- 技能可自动触发：Agent 根据用户消息判断是否激活

---

## 范围（P1）

| 模块 | 说明 |
|---|---|
| Skill 定义文件 | YAML 格式的技能元数据 + prompt delta |
| Skill 注册表 | 发现、激活、停用、查询 |
| 内置 Skills | 3 个示例技能（写作教练 / 代码助手 / 深度调研） |
| Agent 集成 | assemble_tools 按技能过滤工具，system prompt 追加 delta |
| 前端管理页 | Settings 下 "技能" Tab，开关式激活 |
| API | `GET/POST /api/skills` + 单个开关 |

**不在范围**：Skill 热重载（修改 YAML 后自动重载）、Skill 市场/远程下载、Skill 间冲突检测。

---

## 文件变更清单

```
新增:
  lib/skills/__init__.py            # 模块公开接口
  lib/skills/_base.py               # SkillDef dataclass + SkillRegistry
  lib/skills/loader.py              # YAML 加载
  lib/skills/builtins/              # 内置技能目录
  lib/skills/builtins/writing_coach/skill.yaml
  lib/skills/builtins/writing_coach/prompt.md
  lib/skills/builtins/code_assistant/skill.yaml
  lib/skills/builtins/code_assistant/prompt.md
  lib/skills/builtins/web_research/skill.yaml
  lib/skills/builtins/web_research/prompt.md
  server/routes/skills.py           # 技能管理 API
  src/lib/api/skills.ts             # 前端技能 API 客户端
  src/pages/Skills.tsx              # 前端技能管理页

修改:
  lib/tools/factory.py              # assemble_tools 接收 active_skills
  core/agent.py                     # build_system_prompt 接收 active_skills
  lib/chat/service.py               # _inject_context_frame 注入技能列表
  lib/chat/event_handlers.py        # （可选）技能激活事件广播
  main.py                           # 注册 skills router + 初始化 skill registry
  core/startup.py                   # lifespan 中加载技能
  src/lib/api.ts                    # 重新导出 skills API
  src/App.tsx                       # Settings 页面增加 Skills Tab
  src/pages/Settings.tsx            # 增加技能管理 Tab
```

---

## 任务 1 — SkillDef + SkillRegistry

### 新建文件 `lib/skills/_base.py`

```python
"""Skill 定义 + 注册表 — 热插拔技能包。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SkillDef:
    """单个技能的定义。"""
    name: str                          # 唯一标识，如 "writing_coach"
    display_name: str                  # 显示名称，如 "写作教练"
    description: str                   # 一句话描述
    trigger: str = "manual"            # manual | auto | keyword
    keywords: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)       # 技能专属工具名列表
    extra_tools: list[str] = field(default_factory=list)  # 额外激活的工具（如果不在基础工具列表中）
    system_prompt_delta: str = ""      # 追加到 system prompt 的内容
    pre_conditions: list[str] = field(default_factory=list)  # 激活前提条件
    icon: str = "•"                    # 前端显示图标
    version: str = "1.0"
    active: bool = False               # 当前是否激活

    @property
    def is_auto_trigger(self) -> bool:
        return self.trigger == "auto"

    @property
    def is_manual_trigger(self) -> bool:
        return self.trigger == "manual"

    @property
    def is_keyword_trigger(self) -> bool:
        return self.trigger == "keyword"


class SkillRegistry:
    """技能注册表：发现、激活、查询。"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDef] = {}
        self._skills_dir: Path | None = None

    def initialize(self, skills_dir: Path) -> None:
        """扫描技能目录，加载所有 skill.yaml。"""
        self._skills_dir = skills_dir
        self._skills.clear()
        self._reload_all()

    def _reload_all(self) -> None:
        """重新扫描 skills_dir 下所有 skill.yaml。"""
        if not self._skills_dir or not self._skills_dir.exists():
            return

        from lib.skills.loader import load_skill

        for yaml_path in sorted(self._skills_dir.glob("*/skill.yaml")):
            try:
                skill = load_skill(yaml_path)
                self._skills[skill.name] = skill
                logger.debug("技能已加载", name=skill.name, display=skill.display_name)
            except Exception as e:
                logger.warning("技能加载失败", path=str(yaml_path), error=str(e))

    # ── 查询 ──

    def list_all(self) -> list[SkillDef]:
        return list(self._skills.values())

    def list_active(self) -> list[SkillDef]:
        return [s for s in self._skills.values() if s.active]

    def get(self, name: str) -> SkillDef | None:
        return self._skills.get(name)

    # ── 激活/停用 ──

    def activate(self, name: str) -> tuple[bool, str]:
        """激活技能。Returns (success, message)。"""
        skill = self._skills.get(name)
        if not skill:
            return False, f"技能不存在：{name}"

        # 检查前置条件
        for cond in skill.pre_conditions:
            if not self._check_pre_condition(cond):
                return False, f"前置条件不满足：{cond}"

        if skill.active:
            return True, f"{skill.display_name} 已激活"

        skill.active = True
        self._apply_tools(skill, activate=True)
        logger.info("技能已激活", name=name)
        return True, f"{skill.display_name} 已激活"

    def deactivate(self, name: str) -> tuple[bool, str]:
        """停用技能。Returns (success, message)。"""
        skill = self._skills.get(name)
        if not skill:
            return False, f"技能不存在：{name}"

        if not skill.active:
            return True, f"{skill.display_name} 未激活"

        skill.active = False
        self._apply_tools(skill, activate=False)
        logger.info("技能已停用", name=name)
        return True, f"{skill.display_name} 已停用"

    def _apply_tools(self, skill: SkillDef, activate: bool) -> None:
        """激活/停用技能专属工具。目前只打日志，实际工具过滤在 assemble_tools 中完成。"""
        if skill.tools or skill.extra_tools:
            action = "注册" if activate else "移除"
            logger.debug("技能工具变更", name=skill.name, action=action, tools=skill.tools + skill.extra_tools)

    def _check_pre_condition(self, cond: str) -> bool:
        """检查前置条件。支持格式：config:xxx（检查配置项是否存在且非空）"""
        if cond.startswith("config:"):
            key = cond[len("config:"):]
            from core.config import get_settings
            settings = get_settings()
            val = getattr(settings, key, None)
            return bool(val)
        return True

    # ── 自动触发 ──

    def detect_skills(self, user_input: str) -> list[str]:
        """根据用户输入检测应激活的技能名列表（仅 auto 和 keyword 类型）。"""
        detected: list[str] = []
        lower = user_input.lower()
        for skill in self._skills.values():
            if skill.active:
                continue  # 已激活的不重复触发检测
            if skill.trigger == "keyword":
                if any(kw in lower for kw in skill.keywords):
                    detected.append(skill.name)
            elif skill.trigger == "auto":
                # auto 类型始终作为"候选"提供给 Agent（由 Agent 在 context frame 中自行判断）
                pass
        return detected


# ── 模块级单例 ──

_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
```

---

## 任务 2 — YAML 加载器

### 新建文件 `lib/skills/loader.py`

```python
"""技能 YAML 加载器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lib.skills._base import SkillDef


def load_skill(yaml_path: Path) -> SkillDef:
    """从 skill.yaml 加载单个技能定义。

    格式示例:
        name: writing_coach
        display_name: 写作教练
        description: 帮助用户优化文字表达
        trigger: keyword
        keywords: [写作, 润色, 改一下, 表达]
        tools: []
        extra_tools: []
        icon: ✏️
        version: "1.0"
        pre_conditions: []
    """
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"无效的 skill.yaml：{yaml_path}")

    skill_dir = yaml_path.parent

    # 加载 prompt delta（从同目录 prompt.md，可选）
    prompt_md_path = skill_dir / "prompt.md"
    prompt_delta = ""
    if prompt_md_path.exists():
        prompt_delta = prompt_md_path.read_text(encoding="utf-8").strip()

    return SkillDef(
        name=raw.get("name", ""),
        display_name=raw.get("display_name", raw.get("name", "")),
        description=raw.get("description", ""),
        trigger=raw.get("trigger", "manual"),
        keywords=raw.get("keywords", []),
        tools=raw.get("tools", []),
        extra_tools=raw.get("extra_tools", []),
        system_prompt_delta=prompt_delta or raw.get("system_prompt_delta", ""),
        pre_conditions=raw.get("pre_conditions", []),
        icon=raw.get("icon", "•"),
        version=raw.get("version", "1.0"),
    )
```

---

## 任务 3 — 模块公开接口

### 新建文件 `lib/skills/__init__.py`

```python
from lib.skills._base import SkillDef, SkillRegistry, get_skill_registry
from lib.skills.loader import load_skill

__all__ = [
    "SkillDef",
    "SkillRegistry",
    "get_skill_registry",
    "load_skill",
]
```

---

## 任务 4 — 内置技能（3 个示例）

### 新建文件 `lib/skills/builtins/writing_coach/skill.yaml`

```yaml
name: writing_coach
display_name: 写作教练
description: 帮助优化文字表达，提供措辞建议、结构调整和风格打磨
trigger: keyword
keywords:
  - 帮我写
  - 润色
  - 改一下
  - 措辞
  - 表达
  - 文笔
  - 流畅
  - 简洁一点
  - 正式一点
  - 口语化
tools: []
extra_tools: []
icon: ✏️
version: "1.0"
pre_conditions: []
```

### 新建文件 `lib/skills/builtins/writing_coach/prompt.md`

```markdown
## 写作教练模式

你现在是写作教练模式。当用户需要写作帮助时：

1. **理解意图**：先确认用户想要什么——润色现有文字、生成新内容、调整风格，还是优化结构
2. **分层反馈**：
   - 结构层面：段落组织、逻辑流、信息密度
   - 句子层面：长短句搭配、过渡自然度
   - 用词层面：精确性、一致性、避免重复
3. **保留用户声音**：优化文字时保持用户原有的语气和个性，而不是替换成模板化表达
4. **给出对比**：修改后附带原文对比，让用户看到改动和理由
5. **不主动说教**：除非用户问"怎么写更好"，否则不主动输出写作技巧讲座
```

### 新建文件 `lib/skills/builtins/code_assistant/skill.yaml`

```yaml
name: code_assistant
display_name: 代码助手
description: 帮助写代码、调试、解释代码逻辑
trigger: keyword
keywords:
  - 代码
  - 编程
  - 报错
  - 调试
  - bug
  - 函数
  - 实现
  - 写一个
  - 帮我看看这个代码
tools:
  - file_read
  - file_write
  - file_grep
  - file_ls
extra_tools: []
icon: 💻
version: "1.0"
pre_conditions: []
```

### 新建文件 `lib/skills/builtins/code_assistant/prompt.md`

```markdown
## 代码助手模式

你现在是代码助手模式。当用户需要编程帮助时：

1. **先读代码**：在给建议前，用 file_read 读相关文件，理解上下文
2. **最小修改原则**：只改需要改的地方，不要顺手重构
3. **给出完整 diff**：修改后展示 diff，让用户清楚改了什么
4. **解释原因**：每个修改都要解释"为什么"，不是"改了就行"
5. **考虑边界**：空输入、大文件、并发、异常——都要考虑
6. **不假设环境**：先确认用户用的什么语言、框架、版本
```

### 新建文件 `lib/skills/builtins/web_research/skill.yaml`

```yaml
name: web_research
display_name: 深度调研
description: 针对复杂话题进行多轮搜索和信息整合
trigger: keyword
keywords:
  - 调研
  - 研究
  - 深入了解
  - 调查
  - 帮我查
  - 最新
  - 什么情况
  - 怎么看
tools:
  - web_search
  - file_write
extra_tools: []
icon: 🔍
version: "1.0"
pre_conditions:
  - config:search_provider
  - config:search_api_key
```

### 新建文件 `lib/skills/builtins/web_research/prompt.md`

```markdown
## 深度调研模式

你现在是深度调研模式。当用户需要深入了解某个话题时：

1. **拆解问题**：把一个复杂问题拆成 2-4 个子问题，分别搜索
2. **交叉验证**：同一信息从不同来源确认，发现矛盾时指出
3. **信息整合**：不逐条念搜索结果，而是综合成连贯的回答
4. **标注来源**：关键事实标注出处，方便用户追溯
5. **知识盲区**：找不到的信息坦诚说明，不编造
6. **保存结果**：调研结束后用 file_write 保存为 .md 文件，方便用户离线查阅
```

---

## 任务 5 — 工具工厂适配

### 修改文件 `lib/tools/factory.py`

**5.1** 修改 `assemble_tools()` 函数签名：

```python
def assemble_tools(active_skills: list[str] | None = None) -> list[ToolDef]:
    """合并所有来源的工具并应用中间件。

    active_skills: 当前激活的技能名列表，用于过滤/追加工具
    """
    tools: list[ToolDef] = [
        *create_file_tools(),
        *create_memory_tools(),
        *create_profile_tools(),
        *create_notes_tools(),
        *create_web_search_tools(),
        *_discover_mcp_tools(),
    ]

    # ── 技能特殊处理 ──
    if active_skills:
        from lib.skills import get_skill_registry
        registry = get_skill_registry()
        for skill_name in active_skills:
            skill = registry.get(skill_name)
            if skill and skill.tools:
                # 技能声明了 tools 白名单 → 过滤基础工具
                allowed = set(skill.tools)
                tools = [t for t in tools if t.name in allowed]
            if skill and skill.extra_tools:
                # 技能声明的额外工具 → 不需要处理，
                # 因为基础工具已经包含所有可能的工具，
                # extra_tools 仅作为元数据标记
                pass

    tools = wrap_with_logging(tools)
    tools = wrap_with_budget(tools, limit=20)
    return tools
```

> 设计决策：技能 `tools` 字段作为**白名单过滤器**，限制 Agent 在该技能下可调用的工具。例如代码助手的 `tools: [file_read, file_write, file_grep, file_ls]` 意味着激活后 Agent 不能再调用 `web_search`。如果 `tools` 为空 → 不限制，所有基础工具可用。

---

## 任务 6 — Agent 适配

### 修改文件 `core/agent.py`

**6.1** 修改 `build_system_prompt` 方法签名和内部逻辑：

```python
def build_system_prompt(self, active_skills: list[str] | None = None) -> str:
    """组装 system prompt，包含技能 prompt delta。"""
    parts = [base_prompt]  # 原有静态 prompt

    # ── 技能 prompt delta ──
    if active_skills:
        from lib.skills import get_skill_registry
        registry = get_skill_registry()
        for name in active_skills:
            skill = registry.get(name)
            if skill and skill.system_prompt_delta:
                parts.append(f"\n\n---\n\n## 激活技能：{skill.display_name}\n\n{skill.system_prompt_delta}")

    return "".join(parts)
```

**6.2** 修改 `create` 方法，支持传入 `active_skills`：

```python
def create(self, active_skills: list[str] | None = None) -> Agent[LumenDeps, str]:
    from lib.tools.factory import assemble_tools, build_pydantic_toolset

    model = self._create_model()
    filtered_tools = assemble_tools(active_skills=active_skills)
    all_toolsets = [build_pydantic_toolset(filtered_tools)]

    return Agent(
        model=model,
        deps_type=LumenDeps,
        output_type=str,
        system_prompt=self.build_system_prompt(active_skills=active_skills),
        retries=2,
        end_strategy="graceful",
        toolsets=all_toolsets,
        capabilities=[ReinjectSystemPrompt()],
    )
```

**6.3** 修改 `get_agent()` 和 `_config_fingerprint()`，将激活技能纳入缓存指纹：

在 `LumenAgent` 类中增加字段：
```python
class LumenAgent:
    def __init__(self) -> None:
        self._agent: Agent[LumenDeps, str] | None = None
        self._config_hash: str = ""
        self._generation: int = 0
        self._active_skills_hash: str = ""  # 新增
```

修改 `get` 方法：
```python
def get(self, active_skills: list[str] | None = None) -> Agent[LumenDeps, str]:
    skills_hash = hashlib.sha256(
        "|".join(sorted(active_skills or [])).encode()
    ).hexdigest()[:16]

    fp = self._config_fingerprint()
    combined = f"{fp}|{skills_hash}"

    if self._agent is not None and self._config_hash == combined:
        return self._agent
    self._agent = self.create(active_skills=active_skills)
    self._config_hash = combined
    self._generation += 1
    logger.info("Agent 已重建", generation=self._generation, skills=active_skills)
    return self._agent
```

---

## 任务 7 — 对话服务适配

### 修改文件 `lib/chat/service.py`

**7.1** 在 `stream_chat` 中获取当前激活的技能，传入 agent：

```python
    # 获取当前激活的技能
    from lib.skills import get_skill_registry
    registry = get_skill_registry()
    active_names = [s.name for s in registry.list_active()]

    agent = get_agent(active_skills=active_names)
```

**7.2** 在 `_inject_context_frame` 中注入可用技能信息，让 Agent 知道可以建议用户激活哪些技能：

```python
    # ── 可用技能（未被激活的）─
    available_skills = [s for s in registry.list_all() if not s.active]
    if available_skills:
        auto_skills = [s for s in available_skills if s.trigger == "keyword"]
        if auto_skills:
            parts.append(
                f"# 可用技能\n\n"
                f"以下技能可在用户提到相关话题时激活：\n" +
                "\n".join(
                    f"- **{s.display_name}**（关键词：{', '.join(s.keywords[:5])}）：{s.description}"
                    for s in auto_skills
                )
            )
```

**7.3** 在 `persist_turn` 后，根据 user_input 检测是否应自动建议激活技能（可选，可以由 Agent 自行判断，而不是后端自动）。

---

## 任务 8 — 技能管理 API

### 新建文件 `server/routes/skills.py`

```python
"""技能管理 API — 列出/激活/停用技能。"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from lib.skills import get_skill_registry
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillInfo(BaseModel):
    name: str
    display_name: str
    description: str
    trigger: str
    keywords: list[str]
    tools: list[str]
    icon: str
    version: str
    active: bool
    pre_conditions: list[str]


def _to_info(s) -> SkillInfo:
    return SkillInfo(
        name=s.name,
        display_name=s.display_name,
        description=s.description,
        trigger=s.trigger,
        keywords=s.keywords,
        tools=s.tools,
        icon=s.icon,
        version=s.version,
        active=s.active,
        pre_conditions=s.pre_conditions,
    )


@router.get("", response_model=list[SkillInfo])
async def list_skills():
    """列出所有技能（含激活状态）"""
    registry = get_skill_registry()
    return [_to_info(s) for s in registry.list_all()]


@router.post("/{name}/activate")
async def activate_skill(name: str):
    """激活指定技能"""
    registry = get_skill_registry()
    ok, msg = registry.activate(name)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}


@router.post("/{name}/deactivate")
async def deactivate_skill(name: str):
    """停用指定技能"""
    registry = get_skill_registry()
    ok, msg = registry.deactivate(name)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}
```

### 修改文件 `main.py`

**8.1** Import：
```python
from server.routes.skills import router as skills_router
```

**8.2** 注册路由（在 `app.include_router(mcp_router)` 之后）：
```python
app.include_router(skills_router, prefix="/api")
```

### 修改文件 `core/startup.py`

在 lifespan 中初始化 SkillRegistry：

```python
from lib.skills import get_skill_registry
from pathlib import Path

# 在 startup 逻辑中添加：
registry = get_skill_registry()
skills_dir = Path(__file__).parent.parent / "lib" / "skills" / "builtins"
registry.initialize(skills_dir)
logger.info("技能系统已初始化", count=len(registry.list_all()))
```

---

## 任务 9 — 前端 API 客户端

### 新建文件 `src/lib/api/skills.ts`

```typescript
import { http } from "./core";

export type SkillInfo = {
  name: string;
  display_name: string;
  description: string;
  trigger: "manual" | "auto" | "keyword";
  keywords: string[];
  tools: string[];
  icon: string;
  version: string;
  active: boolean;
  pre_conditions: string[];
};

export function listSkills(): Promise<SkillInfo[]> {
  return http<SkillInfo[]>("/api/skills");
}

export function activateSkill(name: string): Promise<{ ok: boolean; message: string }> {
  return http(`/api/skills/${encodeURIComponent(name)}/activate`, { method: "POST" });
}

export function deactivateSkill(name: string): Promise<{ ok: boolean; message: string }> {
  return http(`/api/skills/${encodeURIComponent(name)}/deactivate`, { method: "POST" });
}
```

### 修改文件 `src/lib/api.ts`

追加：
```typescript
// ── Skills ──
export { listSkills, activateSkill, deactivateSkill } from "./api/skills";
export type { SkillInfo } from "./api/skills";
```

---

## 任务 10 — 前端技能管理页

### 新建文件 `src/pages/Skills.tsx`

```tsx
import { useEffect, useState } from "react";
import { listSkills, activateSkill, deactivateSkill, type SkillInfo } from "../lib/api";

export default function Skills() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);

  useEffect(() => {
    listSkills()
      .then(setSkills)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  async function handleToggle(skill: SkillInfo) {
    setToggling(skill.name);
    try {
      if (skill.active) {
        await deactivateSkill(skill.name);
      } else {
        await activateSkill(skill.name);
      }
      setSkills((prev) =>
        prev.map((s) =>
          s.name === skill.name ? { ...s, active: !s.active } : s
        )
      );
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setToggling(null);
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-text-subtle">加载中...</p>
      </div>
    );
  }

  const triggerLabels: Record<string, string> = {
    manual: "手动激活",
    auto: "自动触发",
    keyword: "关键词触发",
  };

  return (
    <div className="mx-auto max-w-[680px] px-md py-xl">
      <h1 className="mb-lg text-xl text-text">技能管理</h1>
      <p className="mb-xl text-sm text-text-subtle">
        技能是一组工具 + 专属指令的打包。激活后，Lumen 会在相关对话中自动切换工作模式。
      </p>

      {skills.length === 0 ? (
        <p className="text-text-subtle">暂无可用的技能。</p>
      ) : (
        <div className="flex flex-col gap-md">
          {skills.map((skill) => (
            <div
              key={skill.name}
              className="rounded-xl border border-border bg-surface/50 p-md transition-colors hover:border-border-soft"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-xs">
                    <span className="text-lg">{skill.icon}</span>
                    <h3 className="text-base text-text">{skill.display_name}</h3>
                    <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-text-subtle">
                      {triggerLabels[skill.trigger] ?? skill.trigger}
                    </span>
                  </div>
                  <p className="mt-xs text-sm text-text-subtle">{skill.description}</p>

                  {skill.keywords.length > 0 && (
                    <div className="mt-sm flex flex-wrap gap-1">
                      {skill.keywords.slice(0, 8).map((kw) => (
                        <span
                          key={kw}
                          className="rounded-md bg-ink-soft/8 px-1.5 py-0.5 text-[11px] text-text-muted"
                        >
                          {kw}
                        </span>
                      ))}
                      {skill.keywords.length > 8 && (
                        <span className="text-[11px] text-text-subtle">
                          +{skill.keywords.length - 8}
                        </span>
                      )}
                    </div>
                  )}

                  {skill.pre_conditions.length > 0 && (
                    <div className="mt-xs text-[11px] text-text-subtle/60">
                      需要：{skill.pre_conditions.join(", ")}
                    </div>
                  )}
                </div>

                <button
                  onClick={() => handleToggle(skill)}
                  disabled={toggling === skill.name || skill.pre_conditions.length > 0 && !skill.active}
                  className={`
                    ml-md flex h-8 min-w-[64px] items-center justify-center rounded-full text-xs transition-all
                    ${
                      skill.active
                        ? "bg-ink text-bg hover:bg-ink-deep"
                        : "border border-border text-text-subtle hover:border-ink/30 hover:text-text"
                    }
                    ${toggling === skill.name ? "opacity-50" : ""}
                  `}
                >
                  {toggling === skill.name ? "..." : skill.active ? "已激活" : "激活"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

### 修改 `src/App.tsx`

在 Settings 路由所在处增加 Skills Tab。如果 Settings 页面已有 Tab 结构，在 Tab 列表中加入：

```tsx
// 在 Settings 页面的 Tab 列表中添加：
{ key: "skills", label: "技能" }
```

对应的 Tab 内容：
```tsx
case "skills":
  return <Skills />
```

### 修改 `src/main.tsx`（如果需要独立路由）

如果有独立路由需求，在 main.tsx 中增加：
```tsx
{
  path: "/skills",
  element: <Skills />,
}
```

---

## 执行顺序

1. **任务 1 + 2 + 3 + 4**：Skill 核心代码 + 内置技能 YAML（独立模块，可先完成）
2. **任务 8 + startup + main.py 改造**：API + 初始化（验证 YAML 加载 → API 返回正确）
3. **任务 5 + 6 + 7**：Agent/工具/对话适配（核心集成，验证技能激活后 prompt 变化）
4. **任务 9 + 10**：前端（最后做，前面后端已跑通）

## 验证方式

```bash
# 1. 验证 API 返回技能列表
curl http://localhost:8000/api/skills

# 2. 激活一个技能
curl -X POST http://localhost:8000/api/skills/writing_coach/activate

# 3. 发送消息，观察 system prompt 中是否包含技能 delta
# （查看后端日志，搜索 "激活技能"）

# 4. 停用技能
curl -X POST http://localhost:8000/api/skills/writing_coach/deactivate
```

---

## 注意事项

- Skill `tools` 为空时 = 不限制工具（所有基础工具可用），与 `tools: []` 语义一致
- 技能激活/停用触发 Agent 重建（通过 `_config_fingerprint` 的 skills_hash 变化），下次对话生效
- YAML 中 `pre_conditions` 用 `config:xxx` 格式检查 `Settings` 字段是否存在且非空
- 前端 Skills 页作为 Settings 下新增 Tab，不改变现有路由结构
- 内置技能目录 `lib/skills/builtins/` 作为数据目录，不包含 Python 代码
