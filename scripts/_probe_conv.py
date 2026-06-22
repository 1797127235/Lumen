import sqlite3
from pathlib import Path

db = Path.home() / ".lumen" / "lumen.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
out = ["== tables =="]
out.append(", ".join(tables))

# 找 conversation/message 相关表
conv_tables = [t for t in tables if "convers" in t.lower() or "message" in t.lower()]
out.append("")
out.append(f"== conv/message tables: {conv_tables} ==")

for t in conv_tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in cur.fetchall()]
    out.append(f"{t}: {cols}")

# 最近的对话
if "conversations" in tables:
    out.append("")
    out.append("== 最近 5 个 conversation ==")
    cur.execute("SELECT * FROM conversations ORDER BY rowid DESC LIMIT 5")
    for row in cur.fetchall():
        d = dict(row)
        out.append(f"  {d}")

# 最近的消息
if "messages" in tables:
    out.append("")
    out.append("== 最近 10 条 message ==")
    cur.execute(
        "SELECT id, conversation_id, role, substr(content,1,60) as preview, created_at FROM messages ORDER BY rowid DESC LIMIT 10"
    )
    for row in cur.fetchall():
        out.append(f"  {dict(row)}")

conn.close()
Path("_probe_out.txt").write_text("\n".join(str(x) for x in out), encoding="utf-8")
print("\n".join(str(x) for x in out))
