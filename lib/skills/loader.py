"""Skill 加载器 — 管理 workspace 和内置两套技能目录

参照 akashic-agent 设计：
- list_skills: 列举所有可用技能
- get_always_skills: 获取 always=true 的技能（每轮自动注入）
- detect_skills: 检测用户输入中匹配的技能（$skill_name 或关键词）
- load_skills_for_context: 将技能内容注入 system prompt
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)

BUILTIN_SKILLS_DIR = Path(__file__).parent / "builtins"
WORKSPACE_SKILLS_DIR = Path.home() / ".lumen" / "skills"


def _escape_xml(s: str) -> str:
    return s.replace("\u0026", "\u0026amp;").replace("<", "\u0026lt;").replace(">", "\u0026gt;")


class SkillsLoader:
    """管理 workspace 和内置两套技能目录。"""

    def __init__(
        self,
        builtin_dir: Path | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self.builtin_dir = builtin_dir or BUILTIN_SKILLS_DIR
        self.workspace_dir = workspace_dir or WORKSPACE_SKILLS_DIR

    # ── 列举 ──────────────────────────────────────────────────────────

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, Any]]:
        """列举所有技能。按优先级：workspace 优先于 builtin。"""
        skills: list[dict[str, Any]] = []
        seen: set[str] = set()

        # 1. workspace 技能（优先级最高）
        if self.workspace_dir.exists():
            for skill_dir in sorted(self.workspace_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists():
                    name = skill_dir.name
                    seen.add(name)
                    skills.append(
                        {
                            "name": name,
                            "path": str(skill_file),
                            "source": "workspace",
                        }
                    )

        # 2. 内置技能
        if self.builtin_dir.exists():
            for skill_dir in sorted(self.builtin_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists():
                    name = skill_dir.name
                    if name in seen:
                        continue
                    skills.append(
                        {
                            "name": name,
                            "path": str(skill_file),
                            "source": "builtin",
                        }
                    )

        # 3. 过滤依赖不满足的
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(s["name"])]
        return skills

    def _check_requirements(self, name: str) -> bool:
        """检查技能的运行依赖是否满足（CLI 工具 + 环境变量）。"""
        config = self._get_skill_config(name)
        requires = config.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        return all(os.environ.get(env) for env in requires.get("env", []))

    # ── 加载 ──────────────────────────────────────────────────────────

    def load_skill(self, name: str) -> str | None:
        """按名称读取 SKILL.md 原始内容（含 frontmatter）。"""
        # workspace 优先
        ws_file = self.workspace_dir / name / "SKILL.md"
        if ws_file.exists():
            return ws_file.read_text(encoding="utf-8")

        # 回退 builtin
        builtin_file = self.builtin_dir / name / "SKILL.md"
        if builtin_file.exists():
            return builtin_file.read_text(encoding="utf-8")

        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """将指定技能内容加载到 system prompt（剥除 frontmatter）。"""
        parts: list[str] = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                body = self._strip_frontmatter(content)
                if body:
                    parts.append(f"### Skill: {name}\n\n{body}")
        return "\n\n---\n\n".join(parts) if parts else ""

    # ── frontmatter 解析 ──────────────────────────────────────────────

    def _strip_frontmatter(self, content: str) -> str:
        """剥除 YAML frontmatter，只保留正文。"""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content.strip()

    def get_skill_metadata(self, name: str) -> dict[str, str] | None:
        """读取 frontmatter 键值对。"""
        content = self.load_skill(name)
        if not content or not content.startswith("---"):
            return None
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None
        meta: dict[str, str] = {}
        for line in match.group(1).split("\n"):
            if ":" in line and not line.strip().startswith("-"):
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip().strip("\"'")
        return meta

    def _get_skill_config(self, name: str) -> dict[str, Any]:
        """从 metadata 字段解析 JSON 配置。"""
        meta = self.get_skill_metadata(name) or {}
        raw = meta.get("metadata", "")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {}
            # 兼容 Lumen 原生格式
            return data
        except (json.JSONDecodeError, TypeError):
            return {}

    # ── always skills ─────────────────────────────────────────────────

    def get_always_skills(self) -> list[str]:
        """返回 always=true 且依赖满足的技能。"""
        result: list[str] = []
        for s in self.list_skills(filter_unavailable=True):
            name = s["name"]
            meta = self.get_skill_metadata(name) or {}
            config = self._get_skill_config(name)
            if config.get("always") or meta.get("always"):
                result.append(name)
        return result

    # ── 技能检测 ──────────────────────────────────────────────────────

    def detect_skills(self, user_input: str) -> list[str]:
        """检测用户输入中匹配的技能。

        匹配方式：
        1. 显式 $skill_name 提及（包含不可用的，让 LLM 看到安装指令）
        2. description 中的关键词匹配（如 bilibili.com、.mp4 等）
        """
        # 所有技能（包含不可用的），但标记状态
        all_skills = {s["name"]: s for s in self.list_skills(filter_unavailable=False)}
        seen: set[str] = set()
        result: list[str] = []

        # 1. 显式 $skill_name（不过滤不可用，让用户可以触发安装）
        raw_names = re.findall(r"\$([a-zA-Z0-9_-]+)", user_input)
        for name in raw_names:
            if name in all_skills and name not in seen:
                seen.add(name)
                result.append(name)
                logger.info("$skill 提及，注入完整内容", skill=name)

        # 2. 关键词匹配（只匹配可用的，避免不可用的干扰正常对话）
        available = {s["name"] for s in self.list_skills(filter_unavailable=True)}
        input_lower = user_input.lower()
        for skill_name in available:
            if skill_name in seen:
                continue
            desc = self._get_skill_description(skill_name).lower()
            keywords = re.findall(r"[\w.\-]+(?:\.[\w.\-]+)+", desc)
            for kw in keywords:
                if kw in input_lower:
                    seen.add(skill_name)
                    result.append(skill_name)
                    logger.info("skill 关键词匹配", skill=skill_name, keyword=kw)
                    break

        return result

    # ── 技能目录摘要 ──────────────────────────────────────────────────

    def build_skills_summary(self) -> str:
        """生成 XML 格式的技能目录摘要，供 system prompt 使用。"""
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        lines = ["<skills>"]
        for s in all_skills:
            name = _escape_xml(s["name"])
            desc = _escape_xml(self._get_skill_description(s["name"]))
            available = self._check_requirements(s["name"])
            source = s.get("source", "builtin")
            lines.append(f'  <skill available="{str(available).lower()}" source="{source}">')
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{s['path']}</location>")
            lines.append("  </skill>")
        lines.append("</skills>")
        return "\n".join(lines)

    # ── 辅助 ──────────────────────────────────────────────────────────

    def _get_skill_description(self, name: str) -> str:
        """从 frontmatter 读取描述，未设置时回退为名称。"""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name


# 全局单例
_loader: SkillsLoader | None = None


def get_skills_loader() -> SkillsLoader:
    global _loader
    if _loader is None:
        _loader = SkillsLoader()
    return _loader
