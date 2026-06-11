"""工具迁移脚本 — 批量将工具 execute 签名从 (args, deps) 改为 (args)。

用法:
    uv run python scripts/migrate_tools.py

处理逻辑:
1. 扫描 lib/tools/ 下所有 .py（排除 __init__, _base, _registry, _middleware 等基础设施）
2. 找到 async def 工具函数（签名含第二个参数 deps 或 ctx）
3. 删除第二个参数
4. 将函数体内的 `deps.xxx` 替换为 `args.get("xxx")`
5. 保留其他代码不变
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

TOOLS_DIR = Path("lib/tools")
EXCLUDE = frozenset(
    {
        "__init__.py",
        "_base.py",
        "_registry.py",
        "_middleware.py",
        "_discovery.py",
        "_loop_guard.py",
        "_path_safety.py",
        "_search_tool.py",
        "factory.py",
    }
)


def is_tool_handler(node: ast.AsyncFunctionDef) -> bool:
    """判断是否为工具 execute 函数（第二个参数名为 deps 或 ctx）。"""
    args = node.args.args
    if len(args) < 2:
        return False
    second = args[1].arg
    return second in ("deps", "ctx")


def migrate_source(src: str) -> str | None:
    """返回迁移后的源码；无需修改时返回 None。"""
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)

    # 收集需要修改的函数
    handlers: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and is_tool_handler(node):
            handlers.append(node)

    if not handlers:
        return None

    # 从后往前修改行，避免偏移错位
    edits: list[tuple[int, int, str]] = []  # (start_line, end_line, new_text)

    for handler in handlers:
        # 1. 修改函数签名：删除第二个参数
        original_def = lines[handler.lineno - 1]

        # 简单字符串替换：找 , deps) 或 , ctx)
        # 更稳健的方式：用正则匹配整个参数列表
        pattern = rf"(async def {handler.name}\([^,]+),\s*(deps|ctx)\s*(:\s*[^\)]+)?\)"
        replacement = r"\1)"
        new_def = re.sub(pattern, replacement, original_def)

        if new_def == original_def:
            # 尝试另一种模式（可能换行）
            pattern2 = rf"(async def {handler.name}\([^)]+),\s*\n?\s*(deps|ctx)\b[^)]*\)"
            new_def = re.sub(pattern2, replacement, original_def, flags=re.DOTALL)

        if new_def != original_def:
            edits.append((handler.lineno, handler.lineno, new_def))

        # 2. 修改函数体内的 deps.xxx → args.get("xxx")
        for child in ast.walk(handler):
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id in ("deps", "ctx")
            ):
                # 获取源码中的原始文本
                start = (child.lineno, child.col_offset)

                # 构造新文本
                attr_name = child.attr
                new_text = f'args.get("{attr_name}")'

                # 记录编辑
                # 注意：这里只做行级替换，不做字符级（避免复杂）
                # 改为在行内做简单字符串替换
                line_idx = child.lineno - 1
                line = lines[line_idx]
                old_attr = f"{child.value.id}.{attr_name}"
                if old_attr in line:
                    lines[line_idx] = line.replace(old_attr, new_text, 1)

    # 应用签名编辑
    edits.sort(key=lambda x: x[0], reverse=True)
    for start, _end, text in edits:
        lines[start - 1] = text

    return "".join(lines)


def main() -> int:
    changed = 0
    skipped = 0

    for fpath in sorted(TOOLS_DIR.glob("*.py")):
        if fpath.name in EXCLUDE:
            continue

        src = fpath.read_text(encoding="utf-8")
        migrated = migrate_source(src)

        if migrated is None:
            skipped += 1
            continue

        fpath.write_text(migrated, encoding="utf-8")
        changed += 1
        print(f"✅ {fpath.name}")

    # 处理子目录（如 mcp/）
    for subdir in ["mcp"]:
        subpath = TOOLS_DIR / subdir
        if not subpath.exists():
            continue
        for fpath in sorted(subpath.glob("*.py")):
            if fpath.name in EXCLUDE:
                continue
            src = fpath.read_text(encoding="utf-8")
            migrated = migrate_source(src)
            if migrated is None:
                skipped += 1
                continue
            fpath.write_text(migrated, encoding="utf-8")
            changed += 1
            print(f"✅ {subdir}/{fpath.name}")

    print(f"\n总计: {changed} 个文件已迁移, {skipped} 个无需修改")
    return 0


if __name__ == "__main__":
    sys.exit(main())
