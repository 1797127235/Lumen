"""记忆层共享常量。"""

from __future__ import annotations

# .md 文件字符限制（markdown.py 和 snapshot.py 共用）
MD_CHAR_LIMITS: dict[str, int] = {
    "memory": 4000,
    "skills": 3000,
    "experiences": 5000,
}
