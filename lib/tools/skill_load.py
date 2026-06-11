"""skill_load 工具 — 查询 Skill 内容（只读）。

技能注入现在由 agent_runner 在每次 turn 时自动处理：
- always=true 技能每轮自动注入
- $skill_name 或关键词匹配的技能自动检测注入

此工具仅用于查询 skill 内容，不再负责注入。
"""

from __future__ import annotations

from typing import Any

from lib.skills.loader import get_skills_loader
from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)


async def _load(args: dict[str, Any], ctx: Any = None):
    name = args.get("skill_name", "").strip()
    if not name:
        return tool_error("请提供 skill_name")

    loader = get_skills_loader()
    content = loader.load_skill(name)
    if content is None:
        available = [s["name"] for s in loader.list_skills(filter_unavailable=True)]
        hint = "、".join(available) if available else "（暂无可用技能）"
        return tool_error(f"Skill '{name}' 不存在。可用技能：{hint}")

    body = loader._strip_frontmatter(content)
    if not body:
        return tool_error(f"Skill '{name}' 正文为空")

    logger.info("skill 已查询", skill=name)
    return tool_ok(body, skill=name)


def create_skill_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="skill_load",
            description=("查询指定技能的完整指令内容。" "当需要查看某个技能的详细说明时使用。"),
            input_schema={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "技能名称",
                    }
                },
                "required": ["skill_name"],
            },
            read_only=True,
            execute=_load,
            meta=ToolMeta(
                risk="read-only",
                always_on=True,
                search_hint="技能 skill 查询",
                tags=["skill"],
            ),
        )
    ]
