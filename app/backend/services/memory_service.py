"""用于画像投影快照的 Markdown 记忆读写辅助。

简化为 3 个文件：
- memory.md: 核心画像 + 状态 + 目标 + 偏好 + 决策
- skills.md: 技能
- experiences.md: 经历
"""

from __future__ import annotations

from datetime import datetime

from app.backend.config import USER_DATA_DIR

MEMORY_DIR = USER_DATA_DIR / "memory"

MEMORY_CHAR_LIMIT = 5000
SKILLS_CHAR_LIMIT = 2000
EXPERIENCES_CHAR_LIMIT = 2000

_LIMITS = {
    "memory": MEMORY_CHAR_LIMIT,
    "skills": SKILLS_CHAR_LIMIT,
    "experiences": EXPERIENCES_CHAR_LIMIT,
}


def ensure_memory_dirs() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


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


def _default_memory_template() -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return f"""# 用户核心记忆

> 这个文件由 AI 自动管理，记录用户的核心信息。
> 每次对话开始时会自动注入到 system prompt。

## 基础信息
- 学校：（待填写）
- 专业：（待填写）
- 年级：（待填写）
- 毕业年份：（待填写）

## 目标方向
- 目标岗位：（待填写）
- 目标公司类型：（待填写）
- 意向城市：（待填写）

## 当前状态
- 正在学习：（待填写）
- 正在准备：（待填写）
- 焦虑程度：（待填写）

---
*最后更新：{date}*
"""


def _default_skills_template() -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return f"""# 技能列表

> 记录用户的技能状态，用于能力评估和学习建议。

## 已掌握技能
（待填写）

---
*最后更新：{date}*
"""


def _default_experiences_template() -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return f"""# 经历列表

> 记录用户的项目、实习、竞赛和其它成长经历。

（待填写）

---
*最后更新：{date}*
"""


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
    limit = _LIMITS[name]
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
