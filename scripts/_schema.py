import sqlite3
from pathlib import Path

db = Path.home() / ".lumen" / "memory" / "8595876131" / "akasha.db"
conn = sqlite3.connect(str(db))
cur = conn.cursor()
out = []
for t in ["akasha_nodes", "akasha_turn_content", "akasha_query_log", "akasha_edges"]:
    cur.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in cur.fetchall()]
    out.append(f"{t}: {cols}")

Path("_schema_out.txt").write_text("\n".join(out), encoding="utf-8")
conn.close()
