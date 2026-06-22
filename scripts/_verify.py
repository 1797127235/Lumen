"""验证记忆时间戳重构:语法 + 正则 + strptime 三层。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

out = []

# 1. 语法检查(导入三个改动文件)
out.append("=== 1. 语法/导入检查 ===")
try:
    import importlib

    for mod in ["lib.memory.markdown", "lib.memory.housekeeping", "lib.tools.memory"]:
        importlib.import_module(mod)
        out.append(f"  OK  {mod}")
except Exception as e:
    out.append(f"  FAIL {type(e).__name__}: {e}")

# 2. 正则匹配新旧两种格式
out.append("")
out.append("=== 2. housekeeping 正则 _ENTRY_RE 匹配测试 ===")
from lib.memory.housekeeping import _parse_entry, _parse_entry_date

cases = [
    ("- 2026-06-14 — [fact] 老格式只有日期", "2026-06-14"),
    ("- 2026-06-14 23:05 — [transient] 新格式带时分", "2026-06-14 23:05"),
    ("- 2026-06-14 23:05:08 — [fact] 新格式带秒", "2026-06-14 23:05:08"),
    ("- 2026-06-14 — [user] 老格式 user 条目", "2026-06-14"),
]
for line, expected_date in cases:
    parsed = _parse_entry(line)
    if parsed and parsed[0] == expected_date:
        out.append(f"  OK  {line[:50]}  → date_str={parsed[0]!r}")
    else:
        out.append(f"  FAIL {line[:50]}  → parsed={parsed}")

# 3. _parse_entry_date 三种格式都能解析
out.append("")
out.append("=== 3. _parse_entry_date 解析测试 ===")
date_cases = ["2026-06-14", "2026-06-14 23:05", "2026-06-14 23:05:08", "invalid"]
for ds in date_cases:
    result = _parse_entry_date(ds)
    out.append(f"  {ds!r:30} → {result}")

# 4. memory.py replace 前缀正则
out.append("")
out.append("=== 4. replace 前缀正则 _MATCH_ENTRY_PREFIX 测试 ===")
from lib.tools.memory import _MATCH_ENTRY_PREFIX

prefix_cases = [
    "- 2026-06-14 — [fact] 老格式",
    "- 2026-06-14 23:05 — [transient] 新格式",
    "- 2026-06-14 23:05:08 — [user] 带秒",
]
for line in prefix_cases:
    m = _MATCH_ENTRY_PREFIX.match(line)
    if m:
        out.append(f"  OK  匹配前缀={m.group(0)!r}  ← {line[:45]}")
    else:
        out.append(f"  FAIL 未匹配  ← {line[:45]}")

# 5. 验证 housekeep_memory 函数对新旧混合内容不崩
out.append("")
out.append("=== 5. housekeep_memory 混合格式集成测试 ===")
from lib.memory.housekeeping import housekeep_memory

mixed = """# 关于你

## Long-term notes

- 2026-06-14 — [transient] 老格式通宵
- 2026-06-14 23:05 — [transient] 新格式通宵
- 2026-06-14 — [fact] 老格式事实
- 2026-06-14 23:05:08 — [preference] 新格式偏好
"""
from datetime import UTC, datetime, timedelta

future_now = datetime.now(UTC) + timedelta(days=10)  # 模拟 10 天后,transient 都该过期
new_content, removed, stale = housekeep_memory(mixed, now=future_now)
out.append(f"  输入 {len(mixed.splitlines())} 行 → 移除 {removed} 行, 标记 stale {stale} 行")
out.append("  剩余内容:")
for line in new_content.splitlines():
    if line.strip().startswith("- "):
        out.append(f"    {line}")

result = "\n".join(out)
Path("_verify_out.txt").write_text(result, encoding="utf-8")
print(result)
