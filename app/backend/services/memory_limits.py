"""记忆文件字符限制（一处定义，全项目引用）。"""

from __future__ import annotations

MEMORY_CHAR_LIMIT = 5000
SKILLS_CHAR_LIMIT = 2000
EXPERIENCES_CHAR_LIMIT = 2000

_LIMITS: dict[str, int] = {
    "memory": MEMORY_CHAR_LIMIT,
    "skills": SKILLS_CHAR_LIMIT,
    "experiences": EXPERIENCES_CHAR_LIMIT,
}
