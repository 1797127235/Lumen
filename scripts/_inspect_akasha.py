import sqlite3
from pathlib import Path

db = Path.home() / ".lumen" / "memory" / "8595876131" / "akasha.db"
print(f"DB: {db}")
print(f"大小: {db.stat().st_size} bytes")
print()
conn = sqlite3.connect(str(db))
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print(f"表: {tables}")
print()

for t in ["akasha_nodes", "akasha_edges", "akasha_turn_content", "akasha_embedding_cache", "akasha_query_log"]:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"{t}: {cur.fetchone()[0]} 行")
    except Exception as e:
        print(f"{t}: 读取失败 {e}")

print()
print("=== 最近 5 个节点(按时间倒序)===")
try:
    cur.execute(
        "SELECT key, session_key, seq, role, first_ts_unix FROM akasha_nodes ORDER BY first_ts_unix DESC LIMIT 5"
    )
    for row in cur.fetchall():
        print(f"  key={row[0]} session={row[1]} seq={row[2]} role={row[3]} ts={row[4]}")
except Exception as e:
    print(f"  失败: {e}")

print()
print("=== 最近 5 条对话内容 ===")
try:
    cur.execute(
        "SELECT turn_key, substr(user_content,1,80), substr(assistant_preview,1,60) FROM akasha_turn_content ORDER BY rowid DESC LIMIT 5"
    )
    for row in cur.fetchall():
        print(f"  [{row[0]}]")
        print(f"    用户: {row[1]}")
        print(f"    AI:   {row[2]}")
except Exception as e:
    print(f"  失败: {e}")

conn.close()
