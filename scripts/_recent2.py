import sqlite3
from pathlib import Path

db = Path.home() / ".lumen" / "sessions.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT seq, role, content, ts FROM messages WHERE session_key='telegram:8595876131' ORDER BY seq")
out = []
for r in cur.fetchall():
    out.append("")
    out.append(f"[seq {r['seq']} | {r['ts']}] {r['role']}")
    out.append(r["content"])

conn.close()
print("\n".join(out))
