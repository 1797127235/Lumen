"""skill_load 工具 — Agent 按需加载 Skill 正文。"""

from __future__ import annotations

from typing import Any

from lib.skills import get_skills_loader
from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)


async def _load(args: dict[str, Any], deps):
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

    logger.info("skill 已加载", skill=name)
    return tool_ok(body, skill=name)


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
