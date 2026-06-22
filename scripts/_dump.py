import sqlite3
from datetime import UTC, datetime
from pathlib import Path

db = Path.home() / ".lumen" / "memory" / "8595876131" / "akasha.db"
conn = sqlite3.connect(str(db))
cur = conn.cursor()
out = []

out.append("=" * 60)
out.append("Akasha 真实数据 dump (Telegram user 8595876131)")
out.append("=" * 60)

# 统计
out.append("")
out.append("== 统计 ==")
for t in ["akasha_nodes", "akasha_edges", "akasha_turn_content", "akasha_query_log"]:
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    out.append(f"  {t}: {cur.fetchone()[0]} 行")

# 节点详情
out.append("")
out.append("== 节点 (akasha_nodes) ==")
cur.execute(
    "SELECT key, session_key, turn_seq, first_ts_unix, salience, strength, recall_count FROM akasha_nodes ORDER BY first_ts_unix"
)
for row in cur.fetchall():
    ts = datetime.fromtimestamp(row[3], tz=UTC).strftime("%m-%d %H:%M:%S")
    out.append(f"  key={row[0]}")
    out.append(f"    session={row[1]} seq={row[2]} ts={ts}")
    out.append(f"    salience={row[4]:.3f} strength={row[5]:.3f} recall={row[6]}")

# 对话内容
out.append("")
out.append("== 已写入对话 (akasha_turn_content) ==")
cur.execute("SELECT key, user_message, assistant_preview FROM akasha_turn_content ORDER BY updated_at")
for row in cur.fetchall():
    out.append(f"  [{row[0]}]")
    out.append(f"    用户: {row[1][:120]}")
    out.append(f"    AI:   {row[2][:100]}")

# 边
out.append("")
out.append("== 共激活边 (akasha_edges) ==")
cur.execute("SELECT src_key, dst_key, weight, co_count FROM akasha_edges ORDER BY weight DESC")
for row in cur.fetchall():
    out.append(f"  {row[0]} --(w={row[2]:.3f}, co={row[3]})--> {row[1]}")

# 召回日志
out.append("")
out.append("== 召回日志 (akasha_query_log) ==")
cur.execute(
    "SELECT query_text, seed_count, activated_count, inject_chars, text_block_preview FROM akasha_query_log ORDER BY ts"
)
for i, row in enumerate(cur.fetchall(), 1):
    out.append(f"  [{i}] query: 「{row[0]}」")
    out.append(f"      seed={row[1]} activated={row[2]} inject_chars={row[3]}")
    if row[4]:
        out.append(f"      preview: {row[4][:150]}")

conn.close()
result = "\n".join(out)
Path("_dump_out.txt").write_text(result, encoding="utf-8")
print(result)
