"""Markdown memory helpers for projected profile snapshots."""

from __future__ import annotations

from datetime import datetime

from app.backend.config import USER_DATA_DIR

MEMORY_DIR = USER_DATA_DIR / "memory"
ENTITIES_DIR = MEMORY_DIR / "entities"


def ensure_memory_dirs() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    ENTITIES_DIR.mkdir(parents=True, exist_ok=True)


def read_memory() -> str:
    memory_file = MEMORY_DIR / "memory.md"
    if not memory_file.exists():
        return ""
    return memory_file.read_text(encoding="utf-8")


def write_memory(content: str) -> None:
    ensure_memory_dirs()
    (MEMORY_DIR / "memory.md").write_text(content, encoding="utf-8")


def read_entity(entity_type: str) -> str:
    entity_file = ENTITIES_DIR / f"{entity_type}.md"
    if not entity_file.exists():
        return ""
    return entity_file.read_text(encoding="utf-8")


def write_entity(entity_type: str, content: str) -> None:
    ensure_memory_dirs()
    (ENTITIES_DIR / f"{entity_type}.md").write_text(content, encoding="utf-8")


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

## 关键偏好
- 学习风格：（待填写）
- 交互偏好：（待填写）
- 每日可用时间：（待填写）

## 最近决定

---
*最后更新：{date}*
"""


def _default_entity_template(entity_type: str) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    templates = {
        "skills": f"""# 技能列表

> 记录用户的技能状态，用于能力评估和学习建议。

---
*最后更新：{date}*
""",
        "experiences": f"""# 经历列表

> 记录用户的项目、实习、竞赛和其他成长经历。

---
*最后更新：{date}*
""",
        "preferences": f"""# 偏好列表

> 记录用户的偏好和习惯，用于个性化建议。

---
*最后更新：{date}*
""",
        "goals": f"""# 目标列表

> 记录用户的短期和长期目标。

---
*最后更新：{date}*
""",
        "decisions": f"""# 决策记录

> 记录用户做出的重要决策。

---
*最后更新：{date}*
""",
        "relationships": f"""# 关系网络

> 记录用户的重要人际关系。

---
*最后更新：{date}*
""",
        "status": f"""# 当前状态

> 记录用户当前的求职、学习和情绪状态。

---
*最后更新：{date}*
""",
    }
    return templates.get(entity_type, f"# {entity_type}\n\n---\n*最后更新：{date}*\n")


def search_memory(query: str) -> list[dict]:
    results: list[dict] = []
    memory_content = read_memory()
    if query.lower() in memory_content.lower():
        results.append(
            {
                "file": "memory.md",
                "section": "核心记忆",
                "content": _extract_relevant_content(memory_content, query),
            }
        )

    if ENTITIES_DIR.exists():
        for entity_file in ENTITIES_DIR.glob("*.md"):
            entity_content = entity_file.read_text(encoding="utf-8")
            if query.lower() in entity_content.lower():
                results.append(
                    {
                        "file": f"entities/{entity_file.name}",
                        "section": entity_file.stem,
                        "content": _extract_relevant_content(entity_content, query),
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


def initialize_memory() -> None:
    ensure_memory_dirs()
    if not (MEMORY_DIR / "memory.md").exists():
        write_memory(_default_memory_template())
    for entity_type in [
        "skills",
        "experiences",
        "preferences",
        "goals",
        "decisions",
        "relationships",
        "status",
    ]:
        entity_file = ENTITIES_DIR / f"{entity_type}.md"
        if not entity_file.exists():
            write_entity(entity_type, _default_entity_template(entity_type))
