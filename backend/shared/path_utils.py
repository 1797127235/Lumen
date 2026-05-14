"""文件路径工具 — 项目根目录检测等。"""

from __future__ import annotations

from pathlib import Path


def find_project_root() -> Path:
    """查找项目根目录。

    策略：
    1. 从当前文件向上查找，找到包含 pyproject.toml 或 .git 的目录
    2. 若找不到，回退到当前工作目录
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    return Path.cwd()
