import sqlite3
from pathlib import Path

for dbfile in ["sessions.db", "lumen.db"]:
    db = Path.home() / ".lumen" / dbfile
    if not db.exists():
        print(f"[skip] {db} 不存在")
        continue
    print(f"\n=== {dbfile} ({db.stat().st_size} bytes) ===")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    print(f"tables: {tables}")

    for t in tables:
        if any(k in t.lower() for k in ["message", "session", "turn"]):
            cur.execute(f"PRAGMA table_info({t})")
            cols = [r[1] for r in cur.fetchall()]
            print(f"  {t}: {cols}")
    conn.close()
