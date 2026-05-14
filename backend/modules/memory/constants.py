"""记忆层共享常量。"""

from __future__ import annotations

# .md 文件字符限制
MD_CHAR_LIMITS: dict[str, int] = {
    "memory": 8000,  # 合并后综合画像总上限
    "about_you": 2000,  # AI 生成画像
    "patterns": 2000,  # 模式洞察（预留）
}
