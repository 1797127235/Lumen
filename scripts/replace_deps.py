import re
from pathlib import Path

for f in sorted(Path("lib/tools").glob("*.py")):
    if f.name in (
        "__init__.py",
        "_base.py",
        "_registry.py",
        "_middleware.py",
        "_discovery.py",
        "_loop_guard.py",
        "_path_safety.py",
        "_search_tool.py",
        "factory.py",
    ):
        continue
    src = f.read_text(encoding="utf-8")
    new = re.sub(r"\bdeps\.([a-zA-Z_][a-zA-Z0-9_]*)", r'args.get("\1")', src)
    if new != src:
        f.write_text(new, encoding="utf-8")
        print(f"Replaced deps in: {f.name}")

for f in sorted((Path("lib/tools") / "mcp").glob("*.py")):
    src = f.read_text(encoding="utf-8")
    new = re.sub(r"\bdeps\.([a-zA-Z_][a-zA-Z0-9_]*)", r'args.get("\1")', src)
    if new != src:
        f.write_text(new, encoding="utf-8")
        print(f"Replaced deps in: mcp/{f.name}")
