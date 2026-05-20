# Story：Skill 体系设计 v2（基于文件的轻量实现）

## 背景

原设计文档（v1）的方案过重：SkillRegistry 单例、YAML 加载器、前端 Toggle、API 路由、工具白名单过滤。参考 akashic-agent 的实现后，发现 Skill 本质上就是**一个 Markdown 文件 + 注入逻辑**，不需要状态管理和 API。

## 核心差异（v1 vs v2）

| 维度 | v1（原设计） | v2（本次实现） |
|------|------------|--------------|
| 技能激活 | 用户手动开关（前端 Toggle） | 关键词自动检测 + `always` 永久注入 |
| 技能内容 | YAML + prompt.md 双文件 | 单个 SKILL.md（frontmatter + 正文） |
| 工具控制 | 技能声明工具白名单，过滤可见工具 | 工具不归 Skill 管，交给已有 tool_search |
| 新增 API | 8 个路由 | 0 个路由 |
| 新增代码 | ~500 行 | ~150 行 |

## SKILL.md 格式

每个技能是一个目录 + 一个 `SKILL.md` 文件：

```
lib/skills/builtins/
  job_search/
    SKILL.md
  reflection/
    SKILL.md
  planning/
    SKILL.md
```

SKILL.md 结构：

```markdown
---
description: 一句话描述，出现在 Agent 看到的技能目录里
metadata: {"always": false, "requires": {"env": []}}
---

正文：给 Agent 的指令，激活后注入到 context frame。
```

frontmatter 字段说明：
- `description`：技能简介（注入摘要时展示）
- `metadata.always`：`true` 时每轮自动注入，`false` 时用户在消息中写 `$skill_name` 触发
- `metadata.requires.env`：需要的环境变量，不满足时从目录中过滤（不展示给 Agent）

**激活方式（与 akashic-agent 保持一致）**：
- `always: true` → 每轮自动注入完整内容
- 用户（或 Agent 引导用户）在消息中写 `$skill_name` → 当轮注入完整内容

Agent 看到技能目录摘要后，可以在回复里告诉用户"在消息中加上 `$reflection` 可以激活复盘模式"。

## 文件变更清单

### 新增文件

| 文件 | 说明 | 预估行数 |
|------|------|---------|
| `lib/skills/__init__.py` | 公开接口 | ~10 |
| `lib/skills/loader.py` | SkillsLoader 实现 | ~150 |
| `lib/skills/builtins/.gitkeep` | 占位，目录存在但无内置技能 | 0 |

> 内置技能 SKILL.md 文件**不在本次范围内**，由用户自行添加。

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `core/agent.py` | `create()` 内注册 `@agent.system_prompt` 装饰器，追加技能目录 + 激活内容到 system prompt 尾部 |

---

## 任务 1 — SkillsLoader

### 新建文件 `lib/skills/loader.py`

```python
"""Skill 加载器 — 基于文件的轻量实现，参考 akashic-agent SkillsLoader。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from shared.logging import get_logger

logger = get_logger(__name__)

BUILTIN_SKILLS_DIR = Path(__file__).parent / "builtins"


class SkillsLoader:
    """管理内置技能目录，提供加载、检测、注入能力。"""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._dir = skills_dir or BUILTIN_SKILLS_DIR

    # ── 列举 ──────────────────────────────────────────────────────────

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """列出所有技能。每项含 name、path、description。"""
        skills: list[dict[str, str]] = []
        if not self._dir.exists():
            return skills

        for skill_dir in sorted(self._dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue

            meta = self._get_metadata(skill_dir.name)
            if filter_unavailable and not self._check_requirements(meta):
                continue

            skills.append({
                "name": skill_dir.name,
                "path": str(skill_file),
                "description": self._get_description(skill_dir.name),
            })
        return skills

    # ── 激活判断 ──────────────────────────────────────────────────────

    def get_always_skills(self) -> list[str]:
        """返回 always=true 且依赖满足的技能名列表。"""
        result: list[str] = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self._get_metadata(s["name"])
            if meta.get("always"):
                result.append(s["name"])
        return result

    def detect_skills(self, user_input: str) -> list[str]:
        """检测用户消息中的 $skill_name 提及，返回匹配的技能名列表。

        与 akashic-agent collect_skill_mentions 逻辑一致：
        正则提取所有 $name，过滤出已注册且依赖满足的技能。
        """
        raw_names = re.findall(r"\$([a-zA-Z0-9_-]+)", user_input)
        if not raw_names:
            return []
        available = {s["name"] for s in self.list_skills(filter_unavailable=True)}
        seen: set[str] = set()
        result: list[str] = []
        for name in raw_names:
            if name in available and name not in seen:
                seen.add(name)
                result.append(name)
                logger.info("$skill 提及，注入完整内容", skill=name)
        return result

    # ── 内容加载 ──────────────────────────────────────────────────────

    def load_skill(self, name: str) -> str | None:
        """读取技能 SKILL.md 原始内容（含 frontmatter）。"""
        skill_file = self._dir / name / "SKILL.md"
        if skill_file.exists():
            return skill_file.read_text(encoding="utf-8")
        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """将指定技能内容加载为 context 注入字符串（剥除 frontmatter）。"""
        parts: list[str] = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                body = self._strip_frontmatter(content)
                if body:
                    parts.append(f"### Skill: {name}\n\n{body}")
        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """生成所有技能的 XML 目录摘要，注入到 context frame 告知 Agent 有哪些技能可用。"""
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for s in all_skills:
            meta = self._get_metadata(s["name"])
            available = self._check_requirements(meta)
            lines.append(f'  <skill available="{str(available).lower()}">')
            lines.append(f"    <name>{esc(s['name'])}</name>")
            lines.append(f"    <description>{esc(s['description'])}</description>")
            if not available:
                missing = self._missing_requirements(meta)
                if missing:
                    lines.append(f"    <requires>{esc(missing)}</requires>")
            lines.append("  </skill>")
        lines.append("</skills>")
        return "\n".join(lines)

    # ── 内部工具 ──────────────────────────────────────────────────────

    def _get_metadata(self, name: str) -> dict:
        """从 frontmatter 中提取 metadata 字段（already a dict via yaml.safe_load）。"""
        fm = self._extract_frontmatter(self.load_skill(name) or "")
        meta = fm.get("metadata", {})
        return meta if isinstance(meta, dict) else {}

    def _get_description(self, name: str) -> str:
        fm = self._extract_frontmatter(self.load_skill(name) or "")
        return str(fm.get("description", name))

    def _extract_frontmatter(self, content: str) -> dict:
        """用 yaml.safe_load 解析 SKILL.md frontmatter，支持多行值。"""
        if not content.startswith("---"):
            return {}
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return {}
        try:
            import yaml
            parsed = yaml.safe_load(match.group(1))
            return parsed if isinstance(parsed, dict) else {}
        except Exception as e:
            logger.warning("SKILL.md frontmatter 解析失败", error=str(e))
            return {}

    def _strip_frontmatter(self, content: str) -> str:
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content.strip()

    def _check_requirements(self, meta: dict) -> bool:
        for env in meta.get("requires", {}).get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def _missing_requirements(self, meta: dict) -> str:
        missing: list[str] = []
        for env in meta.get("requires", {}).get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)


# ── 模块级单例 ────────────────────────────────────────────────────────

_loader: SkillsLoader | None = None


def get_skills_loader() -> SkillsLoader:
    global _loader
    if _loader is None:
        _loader = SkillsLoader()
    return _loader
```

---

## 任务 2 — 公开接口

### 新建文件 `lib/skills/__init__.py`

```python
from lib.skills.loader import SkillsLoader, get_skills_loader

__all__ = ["SkillsLoader", "get_skills_loader"]
```

---

## 任务 3 — 内置技能 SKILL.md（不在本次范围）

内置 SKILL.md 文件由用户自行创建，格式参考"SKILL.md 格式"章节。
只需确保 `lib/skills/builtins/` 目录存在即可（可放 `.gitkeep` 占位）。

---

## 任务 4 — 注入 system prompt 尾部

akashic 的做法：稳定内容在 system prompt 前段（命中 KV cache），动态内容追加在末尾（仅尾部重算）。Lumen 用 PydanticAI 的 `@agent.system_prompt` 装饰器实现同样效果。

### 修改文件 `core/agent.py`

在 `LumenAgent.create()` 内，Agent 创建后注册动态 system prompt 函数：

```python
def create(self) -> Agent[LumenDeps, str]:
    from pydantic_ai.capabilities.reinject_system_prompt import ReinjectSystemPrompt

    model = self._create_model()
    agent = Agent(
        model=model,
        deps_type=LumenDeps,
        output_type=str,
        system_prompt=self.build_system_prompt(),
        retries=2,
        end_strategy="graceful",
        capabilities=[ReinjectSystemPrompt()],
    )

    # 动态 system prompt 尾部：技能目录 + 激活技能内容
    # 稳定前缀（build_system_prompt）始终命中 KV cache；
    # 此函数追加在末尾，仅在有技能时才产生额外 token。
    @agent.system_prompt
    async def _skills_prompt(ctx: RunContext[LumenDeps]) -> str:
        from lib.skills import get_skills_loader
        loader = get_skills_loader()

        parts: list[str] = []

        # 目录摘要（filter_unavailable=False，让 Agent 知道所有技能，含不可用的）
        summary = loader.build_skills_summary()
        if summary:
            parts.append(f"## 可用技能目录\n\n{summary}")

        # 激活技能：always + 本轮 $skill_name 提及
        always_names = loader.get_always_skills()
        detected_names = loader.detect_skills(ctx.deps.current_user_input or "")
        active_names = list(dict.fromkeys([*always_names, *detected_names]))

        if active_names:
            content = loader.load_skills_for_context(active_names)
            if content:
                parts.append(f"# Active Skills\n\n{content}")

        return "\n\n".join(parts)

    return agent
```

`service.py` 不需要任何改动。技能注入完全在 agent 层完成。

---

## 验证方式

```bash
# 1. 验证技能加载（目录为空时返回 []，不报错）
python -c "
from lib.skills import get_skills_loader
loader = get_skills_loader()
print(loader.list_skills())
print(loader.get_always_skills())
print(loader.detect_skills('帮我看看这个 \$job_search'))
print(loader.detect_skills('今天天气怎么样'))  # 应返回 []
print(loader.build_skills_summary())
"

# 2. 在消息中写 $skill_name，观察 system prompt 末尾是否包含技能完整内容
# 搜索后端日志关键字: "$skill 提及，注入完整内容"

# 3. 不含 $skill_name 的消息，system prompt 末尾只有目录摘要，无技能正文
```

---

## 注意事项

- `SkillsLoader` 是无状态的，每轮对话按需读取文件，不缓存激活状态
- `always=true` 目前没有内置技能使用，预留给未来的核心伴侣行为（如特定人格规则）
- `build_skills_summary()` 用 `filter_unavailable=False` 展示全部技能（含不可用的），让 Agent 知道存在但需要配置
- 技能正文不做工具白名单过滤，工具解锁交给 `tool_search` 机制
- SKILL.md 文件变更无需重启，下次对话自动生效（因为每轮都重新读文件）

---

## 执行顺序

1. **任务 1 + 2**：`lib/skills/loader.py` + `lib/skills/__init__.py` + `lib/skills/builtins/.gitkeep`
2. **任务 4**：修改 `core/agent.py`，注册 `@agent.system_prompt` 装饰器
3. **验证**：用上面的命令验证架子可用（目录为空时 `list_skills()` 返回 `[]`，system prompt 不追加任何技能内容，行为与改前一致）
