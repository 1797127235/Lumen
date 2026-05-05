"""用于画像投影快照的 Markdown 记忆读写辅助。

简化为 3 个文件：
- memory.md: 核心画像 + 状态 + 目标 + 偏好 + 决策
- skills.md: 技能
- experiences.md: 经历
"""

from __future__ import annotations

from app.backend.config import USER_DATA_DIR
from app.backend.services.memory_limits import LIMITS
from app.backend.services.memory_templates import (
    experiences_default as _default_experiences_template,
)
from app.backend.services.memory_templates import (
    memory_default as _default_memory_template,
)
from app.backend.services.memory_templates import (
    skills_default as _default_skills_template,
)

MEMORY_DIR = USER_DATA_DIR / "memory"


def ensure_memory_dirs() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def extract_profile_fields(md_text: str) -> dict:
    """从简历 markdown 用正则提取结构化字段（不调 LLM）。

    复用：投影器处理 legacy memory_md blob 用，简历上传后写 profile_updated 也用它。
    """
    import re

    patterns = {
        "school_name": r"- 学校：(.+)",
        "major": r"- 专业：(.+)",
        "grade": r"- 年级：(.+)",
        "graduation_year": r"- 毕业年份：(.+)",
        "school_level": r"- 学校层次：(.+)",
        "target_direction": r"- 目标岗位：(.+)",
        "target_company_level": r"- 目标公司类型：(.+)",
        "city": r"- 意向城市：(.+)",
        "gpa": r"- GPA：(.+)",
        "ranking": r"- 排名：(.+)",
        "english_level": r"## 英语水平\s*\n- (.+)",
        "expected_salary": r"## 期望薪资\s*\n- (.+)",
    }
    fields: dict = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, md_text)
        if m:
            val = m.group(1).strip()
            if val and val != "（待填写）":
                fields[key] = val

    # 多行字段：bio 可能跨行
    m = re.search(r"## 个人简介\s*\n(.+?)(?=\n##|\n---|\Z)", md_text, re.DOTALL)
    if m:
        val = m.group(1).strip()
        if val and val != "（待填写）":
            fields["bio"] = val

    return fields


def read_memory() -> str:
    memory_file = MEMORY_DIR / "memory.md"
    if not memory_file.exists():
        return ""
    return memory_file.read_text(encoding="utf-8")


def write_memory(content: str) -> None:
    ensure_memory_dirs()
    (MEMORY_DIR / "memory.md").write_text(content, encoding="utf-8")


def read_skills() -> str:
    skills_file = MEMORY_DIR / "skills.md"
    if not skills_file.exists():
        return ""
    return skills_file.read_text(encoding="utf-8")


def write_skills(content: str) -> None:
    ensure_memory_dirs()
    (MEMORY_DIR / "skills.md").write_text(content, encoding="utf-8")


def read_experiences() -> str:
    exp_file = MEMORY_DIR / "experiences.md"
    if not exp_file.exists():
        return ""
    return exp_file.read_text(encoding="utf-8")


def write_experiences(content: str) -> None:
    ensure_memory_dirs()
    (MEMORY_DIR / "experiences.md").write_text(content, encoding="utf-8")


def search_memory(query: str) -> list[dict]:
    results: list[dict] = []

    # 搜索 memory.md
    memory_content = read_memory()
    if query.lower() in memory_content.lower():
        results.append(
            {
                "file": "memory.md",
                "section": "核心记忆",
                "content": _extract_relevant_content(memory_content, query),
            }
        )

    # 搜索 skills.md
    skills_content = read_skills()
    if query.lower() in skills_content.lower():
        results.append(
            {
                "file": "skills.md",
                "section": "技能",
                "content": _extract_relevant_content(skills_content, query),
            }
        )

    # 搜索 experiences.md
    exp_content = read_experiences()
    if query.lower() in exp_content.lower():
        results.append(
            {
                "file": "experiences.md",
                "section": "经历",
                "content": _extract_relevant_content(exp_content, query),
            }
        )

    return results


def _extract_relevant_content(content: str, query: str, context_lines: int = 3) -> str:
    lines = content.split("\n")
    relevant_lines: list[str] = []

    for index, line in enumerate(lines):
        if query.lower() not in line.lower():
            continue
        start = max(0, index - context_lines)
        end = min(len(lines), index + context_lines + 1)
        relevant_lines.extend(lines[start:end])
        relevant_lines.append("---")

    unique_lines: list[str] = []
    seen: set[str] = set()
    for line in relevant_lines:
        if line in seen:
            continue
        seen.add(line)
        unique_lines.append(line)
    return "\n".join(unique_lines[:50])


def get_memory_usage(name: str) -> dict:
    """返回指定记忆文件的字符用量信息，用于 Hermes 风格的 system prompt 注入。"""
    readers = {"memory": read_memory, "skills": read_skills, "experiences": read_experiences}
    if name not in readers:
        return {"chars": 0, "limit": 0, "pct": 0}
    content = readers[name]()
    chars = len(content)
    limit = LIMITS[name]
    pct = int(chars / limit * 100) if limit else 0
    return {"chars": chars, "limit": limit, "pct": pct}


def initialize_memory() -> None:
    ensure_memory_dirs()
    if not (MEMORY_DIR / "memory.md").exists():
        write_memory(_default_memory_template())
    if not (MEMORY_DIR / "skills.md").exists():
        write_skills(_default_skills_template())
    if not (MEMORY_DIR / "experiences.md").exists():
        write_experiences(_default_experiences_template())
