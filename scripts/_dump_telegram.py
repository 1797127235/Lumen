import sqlite3
from pathlib import Path

db = Path.home() / ".lumen" / "sessions.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 找 telegram 相关 session
print("== telegram sessions ==")
cur.execute(
    "SELECT key, created_at, updated_at FROM sessions WHERE key LIKE '%telegram%' ORDER BY updated_at DESC LIMIT 5"
)
sessions = [dict(r) for r in cur.fetchall()]
for s in sessions:
    print(f"  {s}")

# 取最新 telegram session 的消息
if sessions:
    sk = sessions[0]["key"]
    print(f"\n== messages in {sk} ==")
    cur.execute(
        "SELECT seq, role, substr(content,1,80) as preview, ts FROM messages WHERE session_key=? ORDER BY seq",
        (sk,),
    )
    for r in cur.fetchall():
        print(f"  seq={r[0]} role={r[1]:10} ts={r[3]}")
        print(f"    {r[2]}")

conn.close()
