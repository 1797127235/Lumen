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

            skills.append(
                {
                    "name": skill_dir.name,
                    "path": str(skill_file),
                    "description": self._get_description(skill_dir.name),
                }
            )
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
                return content[match.end() :].strip()
        return content.strip()

    def _check_requirements(self, meta: dict) -> bool:
        return all(os.environ.get(env) for env in meta.get("requires", {}).get("env", []))

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
