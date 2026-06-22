import sqlite3
from pathlib import Path

db = Path.home() / ".lumen" / "sessions.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 找所有 telegram session,按更新时间倒序
cur.execute("SELECT key, updated_at FROM sessions WHERE key LIKE '%telegram%' ORDER BY updated_at DESC LIMIT 3")
sessions = [dict(r) for r in cur.fetchall()]

out = []
for s in sessions:
    sk = s["key"]
    out.append("=" * 60)
    out.append(f"session: {sk}  (updated {s['updated_at']})")
    out.append("=" * 60)
    cur.execute(
        "SELECT seq, role, content, ts FROM messages WHERE session_key=? ORDER BY seq DESC LIMIT 20",
        (sk,),
    )
    rows = list(reversed([dict(r) for r in cur.fetchall()]))  # 正序显示
    for r in rows:
        out.append("")
        out.append(f"[{r['ts']}] {r['role']}")
        out.append(r["content"])

conn.close()
Path("_recent_out.txt").write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))
